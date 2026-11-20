"""#3185: מה הקונסולה מציגה מהזיכרון ארוך-הטווח של המוח, בדף של כל ישות (דיסק, מחשב, סבב).

המוקאפ שנדב אישר ב-14/11: עמודת "מגמה" וכרטיס דיסק, "נתונים שנאספו" כסיכום, "מה היה בחריצים", ו"קצב ואיבוד" בהיסטוריית
הסבבים. הטסטים כאן בודקים את **המספרים** שהמסכים מציגים, ולא את הציור (הציור: `tests/memory_ui_3185.test.cjs`).

מה נבדק:
- **סיכום ושורות חיות הם ספירה אחת** -- מה שיצא מהחלון ומה שנשאר נספרים יחד, לא פעם אחת מכל חצי.
- **ערך שלא נמדד אינו אפס**: מאפיין שלא דווח, מקלט שלא נקרא, ושדה NULL לא נכנסים לממוצע ולא לסכום.
- **פחות משתי מדידות = "אין עדיין מגמה"**, לעולם לא "יציב".
- **תוצאה שלא ידועה אינה "בוצע"**: החלטה שנסגרה בהפעלה מחדש מסומנת `unknown`, ופתוחה `open`.
- **הנתיבים:** משתמש deploy מקבל 403, ובלי כניסה 401; דיסק שאין עליו שום ידע -- 404.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, memory_view
from server.disks import disk_card
from server.system import housekeeping

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
MAC = "64:31:50:42:b1:9c"
SERIAL = "S21PNXAG812001"


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "view.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def smart(conn, serial, session, days, real=None, pend=None, temp=None, nvme=None):
    ata = []
    if real is not None:
        ata.append([5, 100 - real, 100 - real, 10, real, "Reallocated_Sector_Ct"])
    if pend is not None:
        ata.append([197, 100, 100, 0, pend, "Current_Pending_Sector"])
    conn.execute("INSERT INTO smart_history (serial, session_id, mac, at, attrs_json, temp_after) VALUES (?,?,?,?,?,?)",
                 (serial, session, MAC, ago(days), json.dumps({"ata": ata, "nvme": nvme}), temp))


def speed(conn, serial, session, days, mbps, model="Samsung MZ7LN256"):
    conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?,?,?,?,?,?)",
                 (session, serial, model, MAC, mbps, ago(days)))


def inventory(conn, serial=SERIAL, model="Samsung MZ7LN256", port=2, first=120):
    conn.execute("INSERT OR REPLACE INTO disk_inventory (serial, model, size_bytes, mac, port, first_seen, last_seen)"
                 " VALUES (?,?,?,?,?,?,?)", (serial, model, 256_000_000_000, MAC, port, ago(first), ago(1)))


# --- 1. מגמה ------------------------------------------------------------------------------------------------------


def row(**kw):
    return {"at": "t", "session_id": "s", "mac": MAC, "temp": None, "reallocated": None, "pending": None,
            "media_errors": None, **kw}


def test_a_counter_that_rose_is_a_rise_with_where_it_started_and_how_many_rounds():
    got = disk_card.trend_of([row(reallocated=0), row(reallocated=2), row(reallocated=9), row(reallocated=24)], None)
    assert got == {"state": "rose", "counter": "reallocated", "from": 0, "to": 24, "rounds": 4}


def test_fewer_than_two_measurements_is_no_trend_and_never_stable():
    assert disk_card.trend_of([row(reallocated=0)], None)["state"] == "none"
    assert disk_card.trend_of([], None)["state"] == "none"
    assert disk_card.trend_of([row(reallocated=0), row(reallocated=0)], None)["state"] == "stable"


def test_a_counter_that_was_never_reported_is_not_zero_and_not_stable():
    """דיסק שדיווח רק סקטורים ממתינים אינו "יציב" במוחלפים: הוא לא נמדד שם."""
    got = disk_card.trend_of([row(pending=0), row(pending=3)], None)
    assert (got["state"], got["counter"]) == ("rose", "pending")
    assert disk_card.trend_of([row(), row()], None)["state"] == "none"


def test_what_left_the_window_is_the_start_of_the_trend(conn):
    inventory(conn)
    for n, real in enumerate((0, 0, 0, 1, 1, 2, 3, 3, 4, 5)):          # ten rounds
        smart(conn, SERIAL, f"s{n:02}", 400 - n, real=real)
    for n, real in enumerate((8, 12)):                                   # two recent ones
        smart(conn, SERIAL, f"r{n}", 10 - n, real=real)
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    left = conn.execute("SELECT COUNT(*) FROM smart_history WHERE serial = ?", (SERIAL,)).fetchone()[0]
    assert left < 12, "the daily cleanup folded the old rounds, the test is not testing the fold"
    got = disk_card.trends(conn, [SERIAL])[SERIAL]
    assert (got["state"], got["from"], got["to"], got["rounds"]) == ("rose", 0, 12, 12)


def test_a_counter_that_fell_is_said_to_have_fallen():
    got = disk_card.trend_of([row(reallocated=9), row(reallocated=2)], None)
    assert (got["state"], got["from"], got["to"]) == ("fell", 9, 2)


# --- 2. כרטיס הדיסק -----------------------------------------------------------------------------------------------


def seed_disk(conn):
    inventory(conn)
    for i, (days, real, pend, temp, mbps) in enumerate(((41, 0, 0, 36, 390), (20, 2, 0, 38, 380), (9, 9, 1, 41, 352),
                                                         (2, 24, 3, 44, 301))):
        smart(conn, SERIAL, f"ses_r{i + 1}", days, real=real, pend=pend, temp=temp)
        speed(conn, SERIAL, f"ses_r{i + 1}", days, mbps)
        conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps,"
                     " cable_fail, at) VALUES (?,?,2,?,0,6.0,6.0,0,?)", (f"ses_r{i + 1}", MAC, SERIAL, ago(days)))
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?,?,?,?,?,?,?,?)", (SERIAL, "Samsung MZ7LN256", f"ses_r{i + 1}", mbps * 10**6,
                                                    mbps * 10**6, 18 * 10**9, 50, ago(days)))
    for i in range(5):                                                   # the model as a whole: other disks, faster
        speed(conn, f"OTHER{i}", f"other{i}", 30 + i, 500)
    conn.commit()


def test_the_card_has_the_rounds_the_first_seen_and_the_model_comparison(conn):
    seed_disk(conn)
    got = disk_card.card(conn, SERIAL)
    assert got["first_seen"] == ago(120)
    assert got["rounds"] == {"total": 4, "detailed": 4, "summarized": 0}
    assert [r["reallocated"] for r in got["smart"]["rounds"]] == [24, 9, 2, 0]          # the newest first
    assert [r["mbps"] for r in got["smart"]["rounds"]] == [301, 352, 380, 390]
    avg = (390 + 380 + 352 + 301 + 5 * 500) / 9
    assert got["write"]["model_avg_mbps"] == round(avg, 1) and got["write"]["model_n"] == 9
    assert got["smart"]["rounds"][0]["pct_of_model"] == round(100 * 301 / round(avg, 1))
    assert got["smart"]["rounds"][0]["port"] == 2 and got["smart"]["rounds"][0]["link_gbps"] == 6.0
    assert got["trend"]["state"] == "rose" and got["link"]["crc_total"] == 0 and got["write"]["failures"] == 0


def test_the_card_counts_the_folded_rounds_with_the_live_ones(conn):
    seed_disk(conn)
    for n in range(9):                                                   # nine older rounds, all past retention
        speed(conn, SERIAL, f"old{n}", 400 - n, 380 + n)
        conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                     " VALUES (?,?,?,?,?,?,?,?)", (SERIAL, "Samsung MZ7LN256", f"old{n}", 10**8, 10**8, 10**9, 10, ago(400 - n)))
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    got = disk_card.card(conn, SERIAL)
    assert got["rounds"]["total"] == 13 and got["rounds"]["summarized"] == got["rounds"]["total"] - got["rounds"]["detailed"]
    kept = conn.execute("SELECT COUNT(*) FROM model_speeds WHERE serial = ?", (SERIAL,)).fetchone()[0]
    assert 4 < kept < 13, "the cleanup folded none, or all: the test proves nothing"
    assert got["write"]["older"]["rounds"] == 13 - kept and got["write"]["older"]["min"] == 380
    assert got["write"]["mbps"]["n"] == 13                               # summed + still in the table: one count


def test_a_disk_nobody_knows_has_no_card(conn):
    assert disk_card.card(conn, "NOBODY") is None


def test_an_unknown_outcome_is_not_done(conn):
    """ההחלטה שנסגרה כי השרת הופעל מחדש אינה "בוצעה": `unknown`; פתוחה = `open`; נסגרה בראיה = `closed`."""
    inventory(conn)
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="a", entity=("disk", SERIAL))
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="b", entity=("disk", SERIAL),
                  outcome="הקוד הקיים כתב על הדיסק")
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="c", entity=("disk", SERIAL),
                  outcome=memory.ORPHAN_HE)
    states = {d["reason"]: d["outcome_state"] for d in disk_card.card(conn, SERIAL)["decisions"]}
    assert states == {"a": "open", "b": "closed", "c": "unknown"}


# --- 3. כרטיס המחשב -----------------------------------------------------------------------------------------------


def seed_machine(conn):
    inventory(conn)
    for i, (days, mbps, crc) in enumerate(((410, 1000, 0), (405, 1000, 0), (60, 100, 0), (20, 1000, 2), (2, 1000, 2))):
        conn.execute("INSERT INTO nic_history (mac, iface, at, speed_mbps, rx_crc_errors, rx_dropped) VALUES (?,?,?,?,?,?)",
                     (MAC, "eth0", ago(days), mbps, crc, None if i == 0 else 0))
    for i, (days, seconds) in enumerate(((409, 40.0), (200, None), (30, 36.0))):
        conn.execute("INSERT INTO wol_wakes (mac, sent_at, sent_epoch, resent, answered_at, seconds) VALUES (?,?,?,?,?,?)",
                     (MAC, ago(days), float(i), 0, ago(days) if seconds else None, seconds))
    conn.execute("INSERT INTO machine_inventory (mac, seen_at, inventory_json) VALUES (?,?,'{}')", (MAC, ago(300)))
    conn.execute("INSERT INTO machine_inventory (mac, seen_at, inventory_json) VALUES (?,?,'{}')", (MAC, ago(35)))
    for i, (days, rcv, err) in enumerate(((420, 0, 0), (100, 0, 3), (30, 0, None), (5, 0, 0))):
        conn.execute("INSERT INTO wave_rates (session_id, decision, at, drops) VALUES (?,?,?,?)",
                     (f"w{i}", "{}", ago(days), json.dumps({MAC: {"rcvbuf": rcv, "inerrors": err}})))
    for i, days in enumerate((50, 20, 2)):
        conn.execute("INSERT INTO port_history (session_id, mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, at)"
                     " VALUES (?,?,2,?,?,6.0,6.0,0,?)", (f"p{i}", MAC, SERIAL, 3 if i == 1 else 0, ago(days)))
    smart(conn, SERIAL, "p0", 50, real=0)
    smart(conn, SERIAL, "p2", 2, real=9)
    conn.commit()


def test_the_machine_summary_counts_what_left_the_window_with_what_stayed(conn):
    seed_machine(conn)
    housekeeping.purge_tables(conn, NOW)
    assert conn.execute("SELECT COUNT(*) FROM nic_history").fetchone()[0] < 5, "nothing was folded: the test proves nothing"
    got = memory_view.machine(conn, MAC)
    assert got["nic"]["samples"] == 5
    assert (got["nic"]["speed"]["last"], got["nic"]["speed"]["min"], got["nic"]["speed"]["max"]) == (1000, 100, 1000)
    assert got["nic"]["crc"] == {"first": 0, "last": 2, "max": 2}
    assert got["wol"]["sent"] == 3 and got["wol"]["answered"] == 2
    assert got["wol"]["avg_s"] == 38.0                                   # (40 + 36) / 2: the unanswered one is not 0
    assert got["hardware"]["versions"] == 2 and got["hardware"]["last_at"] == ago(35)


def test_a_receiver_that_was_not_read_is_not_a_receiver_without_drops(conn):
    seed_machine(conn)
    got = memory_view.machine(conn, MAC)["drops"]
    assert got["waves"] == 4
    assert got["rcvbuf"] == {"read": 4, "sum": 0}
    assert got["inerrors"] == {"read": 3, "sum": 3}                      # one wave never read it: 3 of 4, not 4 of 4
    nothing = memory_view.machine(conn, "aa:aa:aa:aa:aa:aa")
    assert nothing["drops"] is None and nothing["nic"] is None and nothing["wol"] is None


def test_the_slot_summary_names_what_the_disk_in_it_did(conn):
    seed_machine(conn)
    slot = memory_view.machine(conn, MAC)["slots"][0]
    assert (slot["port"], slot["rounds"], slot["crc"], slot["failures"]) == (2, 3, 3, 0)
    assert slot["disk"]["serial"] == SERIAL and slot["disk"]["trend"]["state"] == "rose"


def test_the_machine_decisions_come_with_their_outcome(conn):
    seed_machine(conn)
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="x", entity=("machine", MAC))
    assert memory_view.machine(conn, MAC)["decisions"][0]["outcome_state"] == "open"


# --- 4. היסטוריית הסבבים ------------------------------------------------------------------------------------------


def seed_round(conn, days=5, loss=41, rate="260m", kept=True):
    conn.execute("INSERT OR IGNORE INTO groups (id, label, role, sort) VALUES ('cloners', 'משכפלים', 'cloner', 1)")
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at, kind) VALUES ('ses_a','cloners','img',  '', 2, 60, 'closed', 'x', ?, 0, 'multicast')",
                 (ago(days),))
    if kept:
        conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, drops) VALUES ('ses_a', ?, ?, ?, ?)",
                     (json.dumps({"max_bitrate": rate}), ago(days),
                      json.dumps({"sent_bytes": 17_200_000_000, "loss_blocks": loss}),
                      json.dumps({MAC: {"rcvbuf": 0, "inerrors": 3}, "aa:00:00:00:00:02": {"rcvbuf": 0, "inerrors": 0}})))
    conn.commit()
    return ago(days + 0.01), ago(days - 0.01)


def test_the_round_row_has_the_rate_and_the_loss_of_its_waves(conn):
    created, closed = seed_round(conn)
    got = memory_view.flow(memory_view.round_waves(conn, created, closed))
    assert got == {"waves": 1, "kept": 1, "rate_bps": 260_000_000, "loss_blocks": 41}


def test_a_wave_nobody_kept_is_not_kept_and_not_zero_loss(conn):
    created, closed = seed_round(conn, kept=False)
    got = memory_view.flow(memory_view.round_waves(conn, created, closed))
    assert got == {"waves": 1, "kept": 0, "rate_bps": None, "loss_blocks": None}


def test_a_loss_that_was_not_measured_is_not_zero(conn):
    created, closed = seed_round(conn)
    conn.execute("UPDATE wave_rates SET stream = ?", (json.dumps({"sent_bytes": 5}),))
    got = memory_view.flow(memory_view.round_waves(conn, created, closed))
    assert got["loss_blocks"] is None and got["rate_bps"] == 260_000_000


def test_the_round_summary_has_receivers_drives_and_the_decisions_of_its_waves(conn):
    created, closed = seed_round(conn)
    inventory(conn)
    speed(conn, SERIAL, "ses_a", 5, 352)
    conn.execute("INSERT INTO disk_events (session_id, mac, disk, port, serial, verdict, updated_at) VALUES"
                 " ('ses_a', ?, 'sdb', 2, ?, 'ok', ?)", (MAC, SERIAL, ago(5)))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json) VALUES"
                 " ('ses_a', ?, 'done', ?, ?)", (MAC, ago(5), json.dumps([{"dev": "sdb", "state": "done"}])))
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="wave", round_id="ses_a",
                  entity=("machine", MAC), outcome=memory.ORPHAN_HE)
    memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="other", round_id="ses_zzz")
    conn.commit()
    got = memory_view.round_summary(conn, created, closed)
    assert {r["mac"]: r["inerrors"] for r in got["receivers"]} == {MAC: 3, "aa:00:00:00:00:02": 0}
    assert got["drives"][0]["state"] == "done" and got["drives"][0]["mbps"] == 352
    assert [d["reason"] for d in got["decisions"]] == ["wave"]
    assert got["decisions"][0]["outcome_state"] == "unknown"


# --- 5. הנתיבים ---------------------------------------------------------------------------------------------------


def test_the_disk_routes_are_admin_only_and_unknown_disks_are_404(server):
    ctx, admin, deploy, anon = server["ctx"], server["admin"], server["deploy"], server["anon"]
    inventory(ctx.conn)
    smart(ctx.conn, SERIAL, "s1", 3, real=0)
    smart(ctx.conn, SERIAL, "s2", 1, real=4)
    ctx.conn.commit()
    for path in (f"/api/console/disks/{SERIAL}/card", "/api/console/disks"):
        assert deploy.get(path).status_code == 403, path
        assert anon.get(path).status_code == 401, path
    card = admin.get(f"/api/console/disks/{SERIAL}/card").json()
    assert card["trend"]["state"] == "rose" and card["first_seen"]
    assert admin.get("/api/console/disks/NOBODY/card").status_code == 404
    listed = {d["serial"]: d for d in admin.get("/api/console/disks").json()["disks"]}
    assert listed[SERIAL]["trend"]["state"] == "rose" and listed[SERIAL]["first_seen"]


def test_the_machine_memory_route_is_admin_only(server):
    admin, deploy, anon = server["admin"], server["deploy"], server["anon"]
    path = f"/api/console/machines/{MAC}/memory"
    assert deploy.get(path).status_code == 403 and anon.get(path).status_code == 401
    assert admin.get(path).json()["mac"] == MAC
    assert admin.get("/api/console/machines/not-a-mac/memory").status_code == 400


def test_the_round_summary_route_is_closed_to_deploy_and_404_for_a_round_that_is_not_there(server):
    admin, deploy, anon = server["admin"], server["deploy"], server["anon"]
    path = "/api/console/room/history/missing/summary"
    assert deploy.get(path).status_code == 403 and anon.get(path).status_code == 401
    assert admin.get(path).status_code == 404


def test_the_history_row_carries_its_flow_and_opens_to_a_summary(server):
    ctx, admin = server["ctx"], server["admin"]
    created, closed = seed_round(ctx.conn, days=0.01)
    ctx.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, written_drives, state, opened_by, created_at, closed_at)"
                     " VALUES ('rr1','img',6,5,'closed','x',?,?)", (created, closed))
    ctx.conn.commit()
    rows = admin.get("/api/console/room/history").json()
    mine = next(r for r in rows if r["id"] == "rr1")
    assert mine["flow"] == {"waves": 1, "kept": 1, "rate_bps": 260_000_000, "loss_blocks": 41}
    summary = admin.get("/api/console/room/history/rr1/summary").json()
    assert summary["flow"] == mine["flow"] and summary["waves"][0]["session_id"] == "ses_a"
