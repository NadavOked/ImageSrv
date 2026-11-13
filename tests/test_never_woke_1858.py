"""#1858 (מוקאפ 10, מאושר 24/10) — "המכונה לא הגיעה אחרי WoL", ו-F1 רק כשיש דיסק שידוע שעוצר.

נדב: "מאושר רק אם אנחנו יודעים שיש F1 לדיסק." השרת לא רואה את מסך ה-BIOS (עיקרון 5א), ולכן:

* "לא הגיעה" מוצג אחרי **פי 2** מהזמן הטיפוסי של המכונה ולא פחות מ-3 דקות — והזמן קובע רק *מתי*;
* F1 מוזכר רק עם דיסק ידוע — SMART אדום (בריאות נכשלה / לא נקרא), בדיוק כמו חלון הכיבוי בסוכן;
* בלי ראיה כזו — `f1: null`, ושום מילה על F1;
* ראיה מאוחרת (hello, `last_seen`) מנקה, והקריאה שנכשלה אינה "הגיעה" ואינה "לא הגיעה".
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server import db
from server.learn import never_woke, wol as learn_wol

from conftest import hello_body
from test_server_room import CLONER1, room_server  # noqa: F401

MAC = "78:ac:c0:9b:11:c2"
T0 = 1_760_000_000.0
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02',"
              " 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    return c


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _typical(conn, seconds: float) -> float:
    """שלוש הערות שנענו אחרי `seconds` — יש "זמן טיפוסי". מחזיר את הרגע שאחריהן."""
    at = T0 - 100_000
    for _ in range(3):
        learn_wol.sent(conn, MAC, now=at)
        learn_wol.answered(conn, MAC, now=at + seconds)
        at += 1000
    return at


# ---------- מתי "לא הגיעה" ----------

def test_not_yet_before_three_minutes_then_yes(conn):
    learn_wol.sent(conn, MAC, now=T0)
    assert never_woke.waiting(conn, now=T0 + 179) == {}
    got = never_woke.waiting(conn, now=T0 + 181)
    assert list(got) == [MAC]
    assert got[MAC]["waited_s"] == 181 and got[MAC]["typical_s"] is None and got[MAC]["after_s"] == 180
    assert got[MAC]["sent_at"] == _iso(T0)


def test_the_threshold_is_twice_the_typical_time_but_never_under_three_minutes(conn):
    _typical(conn, 150)                                   # פי 2 = 300
    learn_wol.sent(conn, MAC, now=T0)
    assert never_woke.waiting(conn, now=T0 + 290) == {}
    got = never_woke.waiting(conn, now=T0 + 310)[MAC]
    assert got["typical_s"] == 150 and got["after_s"] == 300


def test_a_fast_machine_still_waits_three_minutes(conn):
    _typical(conn, 30)                                    # פי 2 = 60 — מתחת לרצפה
    learn_wol.sent(conn, MAC, now=T0)
    assert never_woke.waiting(conn, now=T0 + 100) == {}
    assert never_woke.waiting(conn, now=T0 + 190)[MAC]["after_s"] == 180


def test_an_answered_wake_is_not_a_miss(conn):
    learn_wol.sent(conn, MAC, now=T0)
    learn_wol.answered(conn, MAC, now=T0 + 60)
    assert never_woke.waiting(conn, now=T0 + 600) == {}


def test_a_machine_seen_after_the_send_is_not_a_miss(conn):
    learn_wol.sent(conn, MAC, now=T0)
    conn.execute("INSERT INTO net_devices (mac, last_seen) VALUES (?, ?)", (MAC, _iso(T0 + 400)))
    conn.commit()
    assert never_woke.waiting(conn, now=T0 + 600) == {}


def test_a_machine_last_seen_before_the_send_is_still_a_miss(conn):
    conn.execute("INSERT INTO net_devices (mac, last_seen) VALUES (?, ?)", (MAC, _iso(T0 - 86400)))
    conn.commit()
    learn_wol.sent(conn, MAC, now=T0)
    assert MAC in never_woke.waiting(conn, now=T0 + 600)


def test_the_notice_does_not_expire_with_the_answer_window(conn):
    """אחרי ANSWER_S ההערה "לא התעוררה" — והמכונה עדיין שותקת: עדיין "לא הגיעה"."""
    learn_wol.sent(conn, MAC, now=T0)
    assert MAC in never_woke.waiting(conn, now=T0 + learn_wol.ANSWER_S + 3600)


def test_only_the_latest_wake_counts(conn):
    learn_wol.sent(conn, MAC, now=T0)
    learn_wol.sent(conn, MAC, now=T0 + learn_wol.ANSWER_S + 100)        # הערה חדשה, אחרי שהראשונה פגה
    learn_wol.answered(conn, MAC, now=T0 + learn_wol.ANSWER_S + 150)
    assert never_woke.waiting(conn, now=T0 + 5000) == {}


def test_a_failed_read_is_not_checked_not_arrived(conn):
    conn.execute("DROP TABLE wol_wakes")
    conn.commit()
    assert never_woke.waiting(conn, now=T0) is None
    assert never_woke.for_machine(None, MAC, [], None) == {"state": "unchecked"}


# ---------- ראיה ל-F1 ----------

def _disk(port, smart="fail", reason="health_failed", model="Samsung SSD 870 EVO", **more):
    return {"dev": f"sd{chr(96 + port)}", "port": port, "model": model, "serial": f"S{port}",
            "smart": smart, "smart_reason": reason, **more}


SLOTS = ["sata:1", "sata:2", "sata:3", "sata:4"]


@pytest.mark.parametrize("reason", ["health_failed", "health_unread", None])
def test_a_red_smart_disk_is_a_known_f1(reason):
    got = never_woke.f1_disk([_disk(1, "ok", None), _disk(2, reason=reason)], SLOTS)
    assert got == {"drawer": 2, "model": "Samsung SSD 870 EVO", "serial": "S2",
                   "reason": reason, "basis": "smart_red", "smart_red": True, "observed": None}   # #1858: and no mark


@pytest.mark.parametrize("reason", ["readonly", "media_readonly", "pending_sectors", "uncorrectable"])
def test_a_red_disk_for_another_reason_is_not_an_f1(reason):
    assert never_woke.f1_disk([_disk(2, reason=reason)], SLOTS) is None


@pytest.mark.parametrize("smart", ["ok", "warn", "unchecked", "failed_last", None])
def test_only_red_smart_counts(smart):
    assert never_woke.f1_disk([_disk(2, smart=smart, reason="health_failed")], SLOTS) is None


def test_nothing_reported_is_no_evidence():
    assert never_woke.f1_disk(None, SLOTS) is None and never_woke.f1_disk([], SLOTS) is None
    assert never_woke.f1_disk("junk", SLOTS) is None and never_woke.f1_disk([5, None], SLOTS) is None


def test_the_lowest_drawer_is_named_and_an_unmapped_one_has_no_number():
    got = never_woke.f1_disk([_disk(4), _disk(2, model="B")], SLOTS)
    assert got["drawer"] == 2 and got["model"] == "B"
    got = never_woke.f1_disk([_disk(9)], SLOTS)
    assert got["drawer"] is None and got["serial"] == "S9"


def test_the_drawer_follows_the_machines_mapping_not_the_port():
    assert never_woke.f1_disk([_disk(3)], ["sata:3", "sata:1"])["drawer"] == 1


def test_for_machine_assembles_the_row():
    waits = {MAC: {"sent_at": "x", "waited_s": 300, "typical_s": 70, "after_s": 180, "resent": 1}}
    row = never_woke.for_machine(waits, MAC, [_disk(2)], SLOTS)
    assert row["state"] == "never_woke" and row["waited_s"] == 300 and row["f1"]["drawer"] == 2
    assert never_woke.for_machine(waits, MAC, [_disk(2, "ok", None)], SLOTS)["f1"] is None
    assert never_woke.for_machine(waits, "00:00:00:00:00:00", [_disk(2)], SLOTS) is None


# ---------- הכלל זהה לחלון הכיבוי בסוכן ----------

def test_the_f1_rule_is_the_one_the_agent_window_uses():
    """`app_f1_disk` בגואי: smart == fail, ובלי סיבה / בריאות נכשלה / לא נקרא — כמו `F1_REASONS`."""
    screens = (ROOT / "native-gui" / "src" / "screens.c").read_text(encoding="utf-8")
    body = screens[screens.index("static int app_f1_disk("):]
    body = body[:body.index("\n}\n")]
    assert 'strcmp(x->smart,"fail")' in body
    assert "!x->cause[0]" in body and '"health_failed"' in body and '"health_unread"' in body
    assert body.count("strcmp(x->cause") == 2, "another reason was added in the agent only"
    assert set(never_woke.F1_REASONS) == {None, "", "health_failed", "health_unread"}


# ---------- דרך GET /api/console/machines ----------

def _set_disks(ctx, mac, disks, seen_epoch):
    ctx.conn.execute(
        "INSERT INTO net_devices (mac, ip, last_seen, disks_json) VALUES (?, '10.0.0.5', ?, ?)"
        " ON CONFLICT(mac) DO UPDATE SET last_seen = excluded.last_seen,"
        " disks_json = excluded.disks_json", (mac, _iso(seen_epoch), json.dumps(disks)))
    ctx.conn.commit()


def _row(client, mac=CLONER1):
    return next(r for r in client.get("/api/console/machines").json() if r["mac"] == mac)


def test_machines_names_the_f1_disk_only_when_one_is_known(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    _set_disks(ctx, CLONER1, [_disk(1, "ok", None), _disk(2)], now - 86400)
    learn_wol.sent(ctx.conn, CLONER1, now=now - 270)
    row = _row(admin)["never_woke"]
    assert row["state"] == "never_woke" and 260 <= row["waited_s"] <= 400
    assert row["f1"]["drawer"] == 2 and row["f1"]["model"] == "Samsung SSD 870 EVO"
    assert row["f1"]["basis"] == "smart_red"
    # אותה מכונה, בלי דיסק F1: אותה הודעה, בלי טענה על F1
    _set_disks(ctx, CLONER1, [_disk(1, "ok", None), _disk(2, "ok", None)], now - 86400)
    row = _row(admin)["never_woke"]
    assert row["state"] == "never_woke" and row["f1"] is None


def test_machines_says_nothing_before_the_wait_and_after_the_machine_speaks(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    assert _row(admin)["never_woke"] is None               # אין הערה בכלל
    learn_wol.sent(ctx.conn, CLONER1, now=now - 60)
    assert _row(admin)["never_woke"] is None               # עוד לא 3 דקות
    learn_wol.sent(ctx.conn, CLONER1, now=now - 59)        # שליחה חוזרת — אותה הערה
    ctx.conn.execute("UPDATE wol_wakes SET sent_epoch = ?, sent_at = ?", (now - 400, _iso(now - 400)))
    ctx.conn.commit()
    assert _row(admin)["never_woke"]["state"] == "never_woke"
    assert room_server["anon"].post("/api/v1/agent/hello", json={
        **hello_body(CLONER1), "disks": []}).status_code == 200
    assert _row(admin)["never_woke"] is None               # hello = הגיעה


def test_machines_marks_unchecked_when_the_wake_table_cannot_be_read(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.conn.execute("ALTER TABLE wol_wakes RENAME TO wol_wakes_gone")
    ctx.conn.commit()
    assert _row(admin)["never_woke"] == {"state": "unchecked"}
