"""חוזה ה-disk_event בצד השרת (#652).

הסוכן מכריע ליד המכונה; השרת שומר את בריאות ה-SMART וההכרעה לצפייה
(#380) ולריבוט-החלפה. **השרת אינו מכריע מכאן** — `unchecked` נשמר כמו
כל verdict אחר, כי לא-נבדק איננו כשל (עיקרון 5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import db
from server.disks import disk_events
from test_server_room import CLONER1, room_server  # noqa: F401


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


def event(**over) -> dict:
    base = {
        "session_id": "ses1", "mac": "b4:2e:99:07:1a:c4", "disk": "sda",
        "port": 2, "serial": "S1",
        "smart": {"verdict": "warn", "reason": "reallocated",
                  "realloc": 8, "pending": 0, "uncorrectable": 0, "crc": 0},
        "write_state": "rescue", "decision": "rescue",
    }
    base.update(over)
    return base


# --- ingest: שמירה, ולידציה, ו-upsert ---------------------------------------

def test_ingest_stores_the_event(conn):
    assert disk_events.ingest(conn, event())["ok"] is True
    (ev,) = disk_events.for_session(conn, "ses1")
    assert ev["verdict"] == "warn"
    assert ev["decision"] == "rescue"
    assert ev["realloc"] == 8
    # ‏disk_number נגזר מ-port -> "דיסק 2" בקונסולה.
    assert ev["disk_number"] == 2


def test_unchecked_is_stored_not_rejected(conn):
    # הכלל הקריטי גם בשרת: unchecked הוא verdict תקף, לא כשל.
    ev = event(smart={"verdict": "unchecked", "reason": "no_smart"},
               write_state="pending", decision=None)
    assert disk_events.ingest(conn, ev)["ok"] is True
    assert disk_events.for_session(conn, "ses1")[0]["verdict"] == "unchecked"


def test_a_later_event_overwrites_the_earlier_one(conn):
    disk_events.ingest(conn, event(smart={"verdict": "warn", "reason": "reallocated"},
                                   write_state="wait_decision", decision=None))
    disk_events.ingest(conn, event(smart={"verdict": "warn", "reason": "reallocated"},
                                   write_state="rescue", decision="rescue"))
    rows = disk_events.for_session(conn, "ses1")
    assert len(rows) == 1                        # אותו (סבב, מכונה, דיסק)
    assert rows[0]["decision"] == "rescue"       # האחרון ניצח


def test_failed_last_is_a_verdict_the_server_keeps(conn):
    """‏#872: דיסק שנכשל בשיכפול הקודם (הסימון של #845) נכנס לשער כ-verdict
    ‏`failed_last`, והמפעיל בחר "המשך" (= דלג). האירוע חייב להישמר כמות
    שהוא — הקונסולה מציגה "נכשל בשיכפול הקודם · דולג", לא `bad_verdict`.
    בקרה שלילית: על main הוא נדחה ולא נשמר."""
    ev = event(smart={"verdict": "failed_last", "reason": "failmark"},
               write_state="skipped", decision="skip")
    assert disk_events.ingest(conn, ev) == {"ok": True}
    row = disk_events.for_session(conn, "ses1")[0]
    assert row["verdict"] == "failed_last" and row["decision"] == "skip"


def test_bad_verdict_is_its_own_error_not_a_silent_pass(conn):
    ev = event(smart={"verdict": "green"})       # verdict לא-מוכר
    result = disk_events.ingest(conn, ev)
    assert result["ok"] is False and result["code"] == "bad_verdict"
    assert disk_events.for_session(conn, "ses1") == []


def test_missing_fields_are_refused(conn):
    assert disk_events.ingest(conn, {"mac": "aa", "disk": "sda"})["code"] == "bad_event"


def test_malformed_mac_is_refused(conn):
    assert disk_events.ingest(conn, event(mac="not-a-mac"))["code"] == "bad_mac"


def test_two_disks_on_one_machine_are_separate_rows(conn):
    disk_events.ingest(conn, event(disk="sda", port=1))
    disk_events.ingest(conn, event(disk="sdb", port=2))
    rows = disk_events.for_session(conn, "ses1")
    assert {r["disk"] for r in rows} == {"sda", "sdb"}


# --- דרך ה-HTTP + התצוגה ב-#380 ---------------------------------------------

def test_endpoint_accepts_a_valid_event_and_refuses_a_bad_one(server):
    anon = server["anon"]
    ok = anon.post("/api/v1/agent/disk-event", json=event())
    assert ok.status_code == 200 and ok.json()["ok"] is True
    bad = anon.post("/api/v1/agent/disk-event", json=event(smart={"verdict": "x"}))
    assert bad.status_code == 400


def test_disk_events_reach_the_session_view(server):
    from conftest import setup_classroom, hello_body
    lab = setup_classroom(server)
    admin, anon = server["admin"], server["anon"]
    assert admin.post("/api/console/sessions", json={
        "group_id": lab["group"], "image_id": "img_7f3a91",
        "prefix": "LAB1", "expected_clients": 1,
    }).status_code == 200
    # המכונה מצטרפת (hello), ואז מדווחת disk_event; המבט-על מציג אותו.
    anon.post("/api/v1/agent/hello", json={**hello_body(lab["mac1"]), "joining": True})
    sid = admin.get("/api/console/overview").json()["session"]["id"]
    anon.post("/api/v1/agent/disk-event", json=event(
        session_id=sid, mac=lab["mac1"], disk="sda", port=1,
        smart={"verdict": "warn", "reason": "crc", "crc": 120},
        write_state="rescue", decision="rescue"))
    member = admin.get("/api/console/overview").json()["session"]["members"][0]
    assert member["mac"] == lab["mac1"]
    (disk,) = member["disks"]
    assert disk["verdict"] == "warn"
    assert disk["reason"] == "crc"
    assert disk["disk_number"] == 1
    assert disk["decision"] == "rescue"


def test_room_drawer_list_carries_smart_for_before_start_health(room_server):
    """דרישת נדב #5: בריאות פר-מגירה מה-hello מגיעה לתצוגת החדר *לפני*
    start. ‏smart_probe_idle של הסוכן מדווח אותה ב-hello, ו-room.drawer_list
    מעביר אותה הלאה כדי שהמפעיל יראה "מגירה N: SMART אזהרה"."""
    from server.rounds import room
    anon, ctx = room_server["anon"], room_server["ctx"]
    body = {
        "schema": 1, "mac": CLONER1, "all_macs": [CLONER1], "ip": "10.44.12.50",
        "hostname_current": None, "uuid": "U1", "firmware": "uefi",
        "secure_boot": True, "agent_version": "0.1.0", "memory_bytes": 8 << 30,
        "disks": [
            {"dev": "sda", "size_bytes": 256060514304, "model": "D", "serial": "S1",
             "removable": False, "scheme": "gpt", "has_data": False, "port": 1,
             "smart": "warn"},
            {"dev": "sdb", "size_bytes": 256060514304, "model": "D", "serial": "S2",
             "removable": False, "scheme": "gpt", "has_data": False, "port": 2,
             "smart": "unchecked"},
        ],
    }
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    drawers = {d["dev"]: d for d in room.drawer_list(ctx.conn, CLONER1, set())}
    assert drawers["sda"]["smart"] == "warn"
    assert drawers["sdb"]["smart"] == "unchecked"


def test_room_view_carries_size_and_defaults_drawer_count(room_server):
    """‏#710: הגריד הראה דיסק אחד לכל מחשב וקיבולת 0. הסיבה: drawer_list
    לא העביר size_bytes, ו-drawer_count נפל ל-null (‏#704 טרם נבנה) →
    guistate נפל ל-1 → הפרסר דחה פורט>1. כאן: size_bytes עובר, ותצוגת
    החדר נופלת לברירת מחדל של הפורט הגבוה (3) כשהעמודה NULL."""
    from server.rounds import room
    anon, ctx = room_server["anon"], room_server["ctx"]
    body = {
        "schema": 1, "mac": CLONER1, "all_macs": [CLONER1], "ip": "10.44.12.50",
        "hostname_current": None, "uuid": "U1", "firmware": "uefi",
        "secure_boot": True, "agent_version": "0.1.0", "memory_bytes": 8 << 30,
        # שני דיסקים בפורטים 1 ו-3 (פורט 2 ריק) — ה-max הוא 3.
        "disks": [
            {"dev": "sda", "size_bytes": 256060514304, "model": "D", "serial": "S1",
             "removable": False, "scheme": "gpt", "has_data": False, "port": 1,
             "smart": "passed"},
            {"dev": "sdb", "size_bytes": 500107862016, "model": "D", "serial": "S2",
             "removable": False, "scheme": "gpt", "has_data": False, "port": 3,
             "smart": "passed"},
        ],
    }
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    drawers = {d["dev"]: d for d in room.drawer_list(ctx.conn, CLONER1, set())}
    assert drawers["sda"]["size_bytes"] == 256060514304
    assert drawers["sdb"]["size_bytes"] == 500107862016
    machine = next(m for m in room.status_view(ctx)["machines"] if m["mac"] == CLONER1)
    assert machine["drawer_count"] == 3   # ברירת מחדל = הפורט הגבוה, לא 1/None
