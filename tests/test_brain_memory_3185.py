"""#3185 (#3064 שלב 4): **הזיכרון ארוך-הטווח של המוח** (`server/brain/memory.py`).

הכרעת נדב 14/11: "המוח על מחשבים ודיסקים צריך לשמור תמיד" -- ``WINDOW`` השורות האחרונות לכל ישות מלאות, מה שישן
מהן **מסוכם ולא נמחק**, והחלטות כלליות 90 יום ועוד WINDOW. הקונסולה והמוח קוראים דרך אותה פונקציה.

מה נבדק (לפי ה-Issue):
- **סיכום לא מאבד ספירות:** מה שיצא מהטבלה + מה שנשאר = מה שנכנס; הסכום, המינימום והמקסימום של מה שיצא נשמרים.
- **ישות שלא נראתה שנה עדיין קיימת** -- גם אחרי עוד שנה של ניקוי.
- **נפח הטבלה אחרי 10,000 סבבים מדומים** -- חסום במספר הישויות, לא במספר הסבבים.
- הקוראים הקיימים (`drive_rates._limit_of`, `learn_models`) רואים אותו דבר לפני הניקוי ואחריו.
- ההחלטות של הווטו ושל תקציב הרשת נרשמות פעם אחת לכל החלטה, עם התוצאה שלהן.
- נקודת קריאה אחת: אף מודול בשרת חוץ מ-`memory.py` אינו קורא את הטבלאות.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db
from server.brain import budget as B, memory, veto
from server.rounds import drive_rates
from server.system import housekeeping

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parent.parent


def ago(days: float, now: datetime = NOW) -> str:
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "memory.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()


def rate(conn, serial, session, bps, days, model="M"):
    conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                 " VALUES (?, ?, ?, ?, 100000000, 2000000000, 100, ?)", (serial, model, session, bps, ago(days)))


def wave(conn, session, days, drops=None):
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, drops) VALUES (?, ?, ?, ?)",
                 (session, json.dumps({"max_bitrate": "600m"}), ago(days),
                  None if drops is None else json.dumps(drops)))


def count(conn, table, where="1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def summary(conn, kind, key, source):
    """the summary of one source, or an empty one: "nothing was summed" fails on the numbers, not on a None."""
    return memory.summary(conn, kind, key).get(source) or {"rows": 0, "num": {}, "text": {}}


# --- 1. סיכום לא מאבד ספירות -----------------------------------------------------------------------------------------


def test_what_leaves_the_window_is_summed_into_the_disk_and_the_model_and_nothing_is_lost(conn):
    for n in range(1, 61):                                   # 60 waves, oldest first, all past the 90 days
        wave(conn, f"w{n:02}", 200 - n)
        rate(conn, "X", f"w{n:02}", 10_000_000 + n, 200 - n)
    conn.commit()
    limit_before = drive_rates._limit_of(conn, "X", "M")
    result = housekeeping.purge_tables(conn, NOW)
    assert result["failed"] == {}
    gone = result["deleted"]["drive_rates"]
    left = count(conn, "drive_rates", "serial = 'X'")
    assert gone > 0 and gone + left == 60
    assert drive_rates._limit_of(conn, "X", "M") == limit_before, "a reader saw the summary change what it reads"
    disk = summary(conn, "disk", "X", "drive_rates")
    assert disk["rows"] == gone
    got = disk["num"]["rate_bps"]
    out = [10_000_000 + n for n in range(1, gone + 1)]       # the oldest ones are the ones that left
    assert (got["n"], got["sum"], got["min"], got["max"]) == (gone, sum(out), min(out), max(out))
    assert (got["first"], got["last"]) == (out[0], out[-1])
    assert summary(conn, "model", "M", "drive_rates")["rows"] == gone   # the same rows, by the model
    assert "סוכמו לזיכרון המוח" in conn.execute(
        "SELECT detail FROM journal WHERE event = 'retention_purged'").fetchone()[0]


def test_two_folds_are_the_same_summary_as_one(conn, tmp_path):
    """הסיכום של הניקוי השני מצטרף לראשון בלי לאבד דבר: אותו n, סכום, קצוות, ראשון ואחרון."""
    other = db.connect(tmp_path / "once.db")
    for c in (conn, other):
        for n in range(1, 41):
            c.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?, 'S', 'M', 'aa', ?, ?)",
                      (f"s{n:02}", 100 + n, ago(300 - n)))
        c.commit()
    housekeeping.purge_tables(conn, NOW - timedelta(days=100))        # first fold: only what was old then
    housekeeping.purge_tables(conn, NOW)
    housekeeping.purge_tables(other, NOW)
    twice, once = summary(conn, "model", "M", "model_speeds"), summary(other, "model", "M", "model_speeds")
    assert twice["rows"] == once["rows"] > 0
    assert twice["num"]["mbps"] == once["num"]["mbps"]


def test_a_value_that_was_not_measured_is_not_counted_as_zero(conn):
    from server.learn import wol as learn_wol                  # noqa: PLC0415
    old = [4.0, None, 8.0]                                     # the three oldest leave the window; one never answered
    for n, seconds in enumerate(old + [5.0] * learn_wol.WINDOW):
        conn.execute("INSERT INTO wol_wakes (mac, sent_at, sent_epoch, answered_at, seconds) VALUES"
                     " ('aa:00:00:00:00:01', ?, ?, ?, ?)", (ago(400 - n), n, ago(400 - n) if seconds else None, seconds))
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    got = summary(conn, "machine", "aa:00:00:00:00:01", "wol_wakes")
    assert got["rows"] == 3 and count(conn, "wol_wakes") == learn_wol.WINDOW
    assert (got["num"]["seconds"]["n"], got["num"]["seconds"]["sum"]) == (2, 12.0)       # not (3, 12): unread is not 0
    assert (got["num"]["answered"]["n"], got["num"]["answered"]["sum"]) == (3, 2.0)


def test_the_smart_trend_of_a_disk_is_kept_first_and_last(conn):
    for n, realloc in enumerate((0, 2, 9)):
        conn.execute("INSERT INTO smart_history (serial, session_id, mac, at, attrs_json, temp_after) VALUES"
                     " ('D1', ?, 'aa', ?, ?, ?)", (f"s{n}", ago(300 - n),
                                                  json.dumps({"ata": [[5, 100, 100, 10, realloc, "Reallocated"]]}),
                                                  None if n == 1 else 40 + n))
    conn.commit()
    housekeeping.purge_tables(conn, NOW)                      # the last of the serial stays (the "since last round" base)
    got = summary(conn, "disk", "D1", "smart_history")["num"]
    assert got["ata_5"]["first"] == 0 and got["ata_5"]["last"] == 2 and got["ata_5"]["n"] == 2
    assert got["temp_after"]["n"] == 1                         # the NULL one is "not measured"
    assert count(conn, "smart_history", "serial = 'D1'") == 1


def test_a_waves_drops_are_kept_for_each_cloner_and_for_the_whole_network(conn):
    for n in range(1, 60):
        wave(conn, f"w{n:02}", 200 - n, {"aa": {"rcvbuf": n, "inerrors": 0, "abandoned": 1},
                                        "bb": {"rcvbuf": None, "inerrors": None}})
    conn.commit()
    gone = housekeeping.purge_tables(conn, NOW)["deleted"]["wave_rates"]
    aa = summary(conn, "network", "aa", "wave_rates")["num"]
    assert aa["rcvbuf"]["n"] == gone and aa["rcvbuf"]["sum"] == sum(range(1, gone + 1)) and aa["abandoned"]["sum"] == gone
    assert "rcvbuf" not in summary(conn, "network", "bb", "wave_rates").get("num", {})   # never read, never "0"
    server = summary(conn, "network", "server", "wave_rates")
    assert server["rows"] == gone and server["num"]["max_bitrate_bps"]["max"] == 600_000_000
    assert "rcvbuf" not in server["num"]                       # one machine unread = the wave's sum unread


# --- 2. ישות שלא נראתה שנה עדיין קיימת ---------------------------------------------------------------------------------


def test_a_disk_unseen_for_a_year_is_still_known_after_another_year_of_cleaning(conn):
    for n in range(1, 31):
        wave(conn, f"old{n:02}", 400 + n)
        rate(conn, "OLD", f"old{n:02}", 5_000_000, 400 + n)
    for n in range(1, 60):                                    # newer waves push OLD's out of the rate memory
        wave(conn, f"new{n:02}", 10 + n / 10)
    conn.commit()
    housekeeping.purge_tables(conn, NOW)
    later = NOW + timedelta(days=365)
    for _ in range(3):
        housekeeping.purge_tables(conn, later)
    known = memory.knowledge(conn, "disk", "OLD")
    assert known["summary"]["drive_rates"]["rows"] + len(known["recent"]["drive_rates"]) == 30
    assert "OLD" in [e["key"] for e in memory.entities(conn, "disk")]


# --- 3. נפח אחרי 10,000 סבבים -------------------------------------------------------------------------------------------


def test_ten_thousand_rounds_leave_a_summary_bounded_by_the_entities_not_by_the_rounds(conn):
    disks, macs, inserted = [f"S{n:02}" for n in range(30)], [f"aa:00:00:00:00:{n:02x}" for n in range(6)], 0
    start = NOW - timedelta(days=500)
    for n in range(10_000):
        at = start + timedelta(days=n * 0.05)                  # 20 rounds a day, 500 days
        session = f"r{n:05}"
        conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?, '{}', ?)",
                     (session, at.isoformat(timespec="seconds")))
        for k in range(3):
            serial = disks[(n * 3 + k) % len(disks)]
            conn.execute("INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps, bytes, seconds, at)"
                         " VALUES (?, 'M', ?, ?, 1, 1, 1, ?)", (serial, session, 1000 + k, at.isoformat(timespec="seconds")))
            inserted += 1
        conn.execute("INSERT INTO brain_decisions (at, mode, rule, action, verdict, reason, entity_kind, entity_key,"
                     " outcome) VALUES (?, 'shadow', 'K2', 'FAIL_WAVE', 'HOLD', 'r', 'machine', ?, 'done')",
                     (at.isoformat(timespec="seconds"), macs[n % len(macs)]))
        conn.execute("INSERT INTO brain_decisions (at, mode, rule, action, verdict, reason, outcome)"
                     " VALUES (?, 'shadow', 'K7', 'HTTP_BUDGET', 'CUT', 'r', 'x')", (at.isoformat(timespec="seconds"),))
        if n % 100 == 99:
            conn.commit()
            assert housekeeping.purge_tables(conn, at)["failed"] == {}
    conn.commit()
    end = start + timedelta(days=500)
    housekeeping.purge_tables(conn, end)
    entities = count(conn, "brain_memory")
    assert entities <= len(disks) + 1 + len(macs) + 1, entities          # disks, the model, the machines, the network
    summed = sum(r[0] for r in conn.execute("SELECT rows FROM brain_memory WHERE source = 'drive_rates' AND kind = 'disk'"))
    assert summed + count(conn, "drive_rates") == inserted                 # not one rate lost
    days = 90 + 1
    assert count(conn, "drive_rates") <= days * 20 * 3 + len(disks) * drive_rates.WINDOW
    assert count(conn, "brain_decisions", "entity_kind IS NULL") <= days * 20 + memory.WINDOW
    folded = sum(r[0] for r in conn.execute("SELECT rows FROM brain_memory WHERE source = 'brain_decisions'"))
    assert folded + count(conn, "brain_decisions", "entity_kind = 'machine'") == 10_000
    size = conn.execute("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()").fetchone()[0]
    assert size < 16 * 1024 * 1024, size


# --- 4. החלטות: כלליות נמחקות, של ישות מסוכמות, פתוחות נשארות --------------------------------------------------------------


def test_general_decisions_keep_ninety_days_plus_a_window_and_entity_ones_are_summed(conn):
    for n in range(40):
        memory.record(conn, mode="shadow", rule="K7", action="HTTP_BUDGET", verdict="CUT", reason="loss",
                      outcome="x", at=ago(200 - n))
        memory.record(conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="alive",
                      entity=("machine", "aa"), outcome="done", at=ago(200 - n))
    memory.record(conn, mode="enforce", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="still held",
                  entity=("machine", "bb"), at=ago(300))
    deleted = housekeeping.purge_tables(conn, NOW)["deleted"]["brain_decisions"]
    assert deleted == 2 * (40 - memory.WINDOW)
    assert count(conn, "brain_decisions", "rule = 'K7'") == memory.WINDOW
    assert count(conn, "brain_decisions", "entity_key = 'aa'") == memory.WINDOW
    assert summary(conn, "machine", "aa", "brain_decisions")["num"]["K2_HOLD"]["n"] == 40 - memory.WINDOW
    assert count(conn, "brain_decisions", "entity_key = 'bb' AND outcome IS NULL") == 1    # open: not summed away
    assert memory.decisions(conn, kind="general")[0]["rule"] == "K7"


def test_a_decision_left_open_by_a_server_that_restarted_is_closed_by_name(conn, monkeypatch):
    conn.execute("INSERT INTO brain_decisions (at, mode, rule, action, verdict, reason) VALUES"
                 " ('2026-01-01T00:00:00+00:00', 'enforce', 'K2', 'FAIL_WAVE', 'HOLD', 'held')")
    conn.commit()
    monkeypatch.setattr(memory, "_orphans_closed", False)
    memory.record(conn, mode="shadow", rule="K7", action="HTTP_BUDGET", verdict="OPEN", reason="x")
    assert conn.execute("SELECT outcome FROM brain_decisions WHERE reason = 'held'").fetchone()[0] == memory.ORPHAN_HE


def test_settings_changes_older_than_the_journal_are_summed_by_setting(conn):
    for n, (value, user) in enumerate((("shadow", "noc"), ("enforce", "nadav"), ("shadow", "nadav"))):
        conn.execute("INSERT INTO journal (ts, user, event, detail) VALUES (?, ?, 'setting_change', ?)",
                     (ago(500 - n), user, f"brain_veto_mode={value}"))
    conn.execute("INSERT INTO journal (ts, user, event, detail) VALUES (?, 'noc', 'setting_change', 'lost_wait_seconds=240')",
                 (ago(10),))
    conn.commit()
    deleted = housekeeping.purge_tables(conn, NOW)["deleted"]
    assert deleted["settings_history"] == 3
    got = summary(conn, "settings", "brain_veto_mode", "journal")
    assert got["num"]["changes"]["n"] == 3 and got["text"]["value"]["value"] == "shadow"
    assert got["text"]["user"]["value"] == "nadav"
    assert [r["detail"] for r in memory.knowledge(conn, "settings", "lost_wait_seconds")["recent"]["journal"]] == [
        "lost_wait_seconds=240"]


def test_the_nic_history_cap_sums_what_it_drops(conn, monkeypatch):
    """`learn.net_history` מוציא מעבר לתקרה בכל הכנסה -- גם שם מסוכם, לא נמחק."""
    from server.learn import net_history                       # noqa: PLC0415
    monkeypatch.setattr(net_history, "KEEP_NIC", 3)
    for n in range(6):                                          # each sample differs: a new row every time
        assert net_history.record_nic(conn, "aa:00:00:00:00:01", {"nic": [
            {"name": "eth0", "speed_mbps": 1000 - n, "stats": {"rx_crc_errors": n}}]}) == 1
    got = summary(conn, "network", "aa:00:00:00:00:01", "nic_history")
    assert count(conn, "nic_history") == 3 and got["rows"] == 3
    assert (got["num"]["rx_crc_errors"]["first"], got["num"]["rx_crc_errors"]["last"]) == (0, 2)


# --- 5. ההחלטות של הווטו ושל התקציב ---------------------------------------------------------------------------------------


def member(conn, mac, age):
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json) VALUES ('W-1', ?, 'writing', ?, '[]')",
                 (mac, (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(timespec="seconds")))
    conn.commit()


def rows(conn, rule: str | None = None):
    """``rule``: רק של כלל אחד. ב-D16 גם K6 (#3320) רושם שורה משלו, והטסטים של הווטו סופרים את של K2."""
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions ORDER BY id")
            if rule is None or r["rule"] == rule]


def test_shadow_remembers_what_it_would_have_held_and_that_the_old_code_went_on(conn):
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id="W-1", targets=["aa", "bb"], reason="780s")
    got = rows(conn, "K2")
    assert [r["rule"] for r in rows(conn) if r["rule"] != "K2"] == ["K6"]          # #3320: K6 ב-D16, שורה אחת
    assert [(r["entity_kind"], r["entity_key"], r["verdict"], r["point"]) for r in got] == [
        ("machine", "aa", "HOLD", "D16"), ("machine", "bb", "HOLD", "D16")]
    assert all(r["outcome"] == veto.SHADOW_DONE_HE and r["mode"] == "shadow" for r in got)
    assert any(e.startswith("aa=ALIVE") for e in json.loads(got[0]["evidence_json"]))


def test_enforce_remembers_a_hold_once_and_its_outcome_when_it_is_released(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    t = [1000.0]
    ask = lambda: veto.check(conn, action="FAIL_WAVE", rule="D16", round_id="W-1", targets=["aa"], reason="x",
                             mono=lambda: t[0])
    for _ in range(5):
        assert ask().proceed is False
    assert len(rows(conn, "K2")) == 1 and rows(conn, "K2")[0]["outcome"] is None  # one hold, asked five times
    t[0] += 1800
    assert ask().proceed is True
    got = rows(conn, "K2")
    assert len(got) == 1 and got[0]["outcome"].startswith("שוחררה אחרי 1800s") and got[0]["outcome_at"]


def test_a_hold_that_settles_says_the_report_came_and_nothing_was_destroyed(conn):
    member(conn, "aa", 2)
    db.set_setting(conn, veto.SETTING, "enforce")
    veto.check(conn, action="FAIL_WAVE", rule="D19", round_id="W-1", targets=["aa"], reason="x", part=4)
    veto.settle(conn, round_id="W-1", action="FAIL_WAVE", part=4)
    assert "לא בוצעה" in rows(conn)[0]["outcome"]


def test_off_remembers_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id="W-1", targets=["aa"], reason="x")
    assert rows(conn) == []


class Budget:
    def __init__(self, conn):
        self.mode, self.t, self.reading = B.SHADOW, 1000.0, {"served_bytes": 0, "broadcast_bps": 0}
        self.budget = B.Budget(mode=lambda: self.mode, ceiling=lambda: (900_000_000, "הגדרה"),
                               read=lambda: B.Reading(self.t, **self.reading), clock=lambda: self.t,
                               remember=lambda **row: memory.record(conn, **row),
                               resolve=lambda row, outcome: memory.resolve(conn, [row], outcome))

    def tick(self, **reading):
        self.t += 10
        self.reading.update(reading)
        return self.budget.tick()


def test_the_budget_remembers_a_change_of_direction_and_every_cut_not_every_cycle(conn):
    b = Budget(conn)
    b.tick()                                                  # OPEN
    b.tick()                                                  # OPEN again: the same decision
    for _ in range(4):
        b.tick(broadcast_bps=300_000_000, rx_dropped=0)       # tuning inside what is left: one decision
    b.tick(rx_dropped=5)                                      # a cut
    b.tick(rx_dropped=9)                                      # another
    b.tick(rx_dropped=9)                                      # quiet: back to tuning
    got = rows(conn)
    assert [r["verdict"] for r in got] == ["OPEN", "CLAMP", "CUT", "CUT", "RAISE"], got
    assert all(r["rule"] == "K7" and r["entity_kind"] is None for r in got)
    assert got[0]["outcome"].endswith("אחרי 20s") and got[2]["outcome"].startswith("CUT")
    assert got[-1]["outcome"] is None                                         # still in force
    b.mode = B.OFF
    b.tick()
    assert rows(conn)[-1]["outcome"].startswith("המוח כובה")


def test_abandoned_chunks_cut_the_budget_and_unread_is_not_quiet():
    cfg = B.Config(ceiling_bps=900_000_000)
    cut = B.step(None, cfg, B.Sample(300_000_000, 0.0, 0, 0, 0.0, (), 12))
    assert cut.action == B.CUT_ and "נטשו 12 חתיכות" in cut.reason
    unread = B.step(None, cfg, B.Sample(300_000_000, 0.0, None, None, None, (), None))
    assert unread.action == B.HOLD and "abandoned=unmeasured" in unread.evidence
    measured = B.derive(B.Reading(0, 0, 1, rx_abandoned=3), B.Reading(10, 0, 1, rx_abandoned=10))
    assert measured.abandoned == 7
    assert B.derive(B.Reading(0, 0, 1, rx_abandoned=3), B.Reading(10, 0, 1)).abandoned is None


# --- 6. נקודת קריאה אחת ---------------------------------------------------------------------------------------------------


def test_only_the_memory_module_reads_the_brains_tables():
    """ההכרעה: הקונסולה והמוח קוראים דרך אותה פונקציה. בית שני ל-SQL היה מתחיל לסטות מהראשון."""
    allowed = {"server/brain/memory.py", "server/db.py", "server/system/housekeeping.py"}
    found = []
    for path in sorted((REPO / "server").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel not in allowed and re.search(r"\b(FROM|INTO|UPDATE|JOIN)\s+brain_(memory|decisions)\b",
                                            path.read_text(encoding="utf-8"), re.I):
            found.append(rel)
    assert found == []
    assert "memory.fold" in (REPO / "server/system/housekeeping.py").read_text(encoding="utf-8")


def test_the_console_reads_through_the_same_function(server):
    ctx = server["ctx"]
    memory.record(ctx.conn, mode="shadow", rule="K2", action="FAIL_WAVE", verdict="HOLD", reason="alive",
                  entity=("machine", "aa:00:00:00:00:01"), outcome="done")
    got = server["admin"].get("/api/console/brain/memory/machine/aa:00:00:00:00:01").json()
    assert got == json.loads(json.dumps(memory.knowledge(ctx.conn, "machine", "aa:00:00:00:00:01")))
    assert got["decisions"][0]["reason"] == "alive"
    assert server["admin"].get("/api/console/brain/memory/slot/aa:00:00:00:00:01/2").status_code == 200
    assert server["admin"].get("/api/console/brain/memory/nothing/x").status_code == 400
    assert server["admin"].get("/api/console/brain/decisions?kind=general").json() == []
    assert server["deploy"].get("/api/console/brain/memory/machine/aa:00:00:00:00:01").status_code == 403
    assert server["anon"].get("/api/console/brain/decisions").status_code == 401
