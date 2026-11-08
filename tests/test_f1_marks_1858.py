"""‏#1858 (מוקאפ 07/11, נדב) — "נצפה F1 בהדלקה" לכל דיסק, לפי מספר סידורי.

ההכרעות של נדב:

* **נשמר לכל דיסק לפי serial** (לא לפי מגירה): דיסק חדש בחריץ נקי, דיסק שהוחלף נשאר מסומן.
* **ידנית** — "סמן F1 לדיסק…" בהודעת "לא הגיע" ובתפריט השורה בעמוד "דיסקים"; "נקה סימון" שם. בלי הקלדת מילה.
* **אוטומטית** — מכונה שהוצגה "לא הגיע · ייתכן F1" וחזרה לרשת מסומנת לבד.
* דיסק שנצפה בו F1 **אדום** ומדולג בשיכפול, והתגית אומרת "נצפה F1" ולא "SMART תקול".
* בלי סימון — הכול כמו היום.

כאן: השרת (טבלה, ‏API, ‏hello, "לא הגיע", סימון אוטומטי). הסוכן — `test_f1_mark_agent_1858.py`, הגואי —
`test_f1_screens_1858.py`, הקונסולה — `f1_marks_1858.test.cjs`.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from server import db
from server.disks import disk_sets, f1_marks
from server.learn import never_woke, wol as learn_wol

from conftest import hello_body
from test_server_room import CLONER1, room_server  # noqa: F401

MAC = "78:ac:c0:9b:11:c2"
SLOTS = ["sata:1", "sata:2", "sata:3", "sata:4"]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _disk(port, smart="ok", reason=None, model="Samsung SSD 870 EVO", **more):
    return {"dev": f"sd{chr(96 + port)}", "port": port, "model": model, "serial": f"S{port}",
            "smart": smart, "smart_reason": reason, "size_bytes": 500_107_862_016, "removable": False, **more}


RED = dict(smart="fail", reason="health_failed")


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02', 'grp_CLONERS', 'x')", (MAC,))
    c.commit()
    return c


def _seen(ctx, mac, disks, seen_epoch):
    ctx.conn.execute(
        "INSERT INTO net_devices (mac, ip, last_seen, disks_json) VALUES (?, '10.0.0.5', ?, ?)"
        " ON CONFLICT(mac) DO UPDATE SET last_seen = excluded.last_seen, disks_json = excluded.disks_json",
        (mac, _iso(seen_epoch), json.dumps(disks)))
    ctx.conn.commit()


def _row(client, mac=CLONER1):
    return next(r for r in client.get("/api/console/machines").json() if r["mac"] == mac)


def _hello(client, disks, mac=CLONER1):
    r = client.post("/api/v1/agent/hello", json={**hello_body(mac), "disks": disks})
    assert r.status_code == 200, r.text
    return r.json()


def _disks_api(admin):
    return {d["serial"]: d for d in admin.get("/api/console/disks").json()["disks"]}


def _event(ctx, serial, verdict, reason=None):
    ctx.conn.execute(
        "INSERT OR REPLACE INTO disk_events (session_id, mac, disk, port, serial, verdict, reason, updated_at)"
        " VALUES ('ses_a', ?, 'sdb', 2, ?, ?, ?, '2026-10-23T22:15:00+00:00')", (CLONER1, serial, verdict, reason))
    ctx.conn.commit()


def _journal(ctx, event):
    return [dict(r) for r in ctx.conn.execute("SELECT * FROM journal WHERE event = ? ORDER BY id", (event,))]


# ---------- הטבלה: לפי serial, סימון אחד לדיסק ----------

def test_the_schema_carries_the_table_and_the_version_moved():
    assert db.SCHEMA_VERSION >= 13


def test_a_database_on_12_gains_the_marks_table_and_keeps_its_rows(tmp_path):
    path = tmp_path / "imagesrv.db"
    conn = db.connect(path)
    conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source) VALUES ('S9', 'x', 'manual')")
    conn.commit()
    conn.execute("DROP TABLE disk_f1_marks")
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    conn.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14   # round/1107c: #1920 = 13, #1858 = 14
    assert f1_marks.all_marks(conn) == {}                                  # the table is back, empty
    conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source) VALUES ('S9', 'x', 'auto')")
    with pytest.raises(Exception):                                         # one mark per serial
        conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source) VALUES ('S9', 'y', 'manual')")
    conn.rollback()
    with pytest.raises(Exception):                                         # only the two sources
        conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source) VALUES ('S8', 'y', 'guess')")


def test_mark_and_clear_by_serial(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _seen(ctx, CLONER1, [_disk(1), _disk(2)], time.time())
    got = admin.post("/api/console/disks/S2/f1", json={"mac": CLONER1})
    assert got.status_code == 200, got.text
    mark = got.json()["f1"]
    assert mark["source"] == "manual" and mark["mac"] == CLONER1 and mark["by"] == "noc"
    assert len(mark["date"]) == 10
    assert set(f1_marks.all_marks(ctx.conn)) == {"S2"}                      # S1 לא סומן; לפי serial בלבד
    assert admin.delete("/api/console/disks/S2/f1").status_code == 200
    assert f1_marks.all_marks(ctx.conn) == {}
    assert [j["detail"] for j in _journal(ctx, "disk_f1_marked")][0].startswith("S2 ")
    assert len(_journal(ctx, "disk_f1_cleared")) == 1


def test_a_second_mark_keeps_the_first_and_says_so(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _seen(ctx, CLONER1, [_disk(1)], time.time())
    assert admin.post("/api/console/disks/S1/f1", json={}).status_code == 200
    first = f1_marks.all_marks(ctx.conn)["S1"]
    again = admin.post("/api/console/disks/S1/f1", json={})
    assert again.status_code == 409 and "כבר מסומן" in again.json()["detail"]
    assert f1_marks.all_marks(ctx.conn)["S1"] == first
    assert len(_journal(ctx, "disk_f1_marked")) == 1


def test_a_serial_the_server_never_saw_is_refused(room_server):
    admin = room_server["admin"]
    got = admin.post("/api/console/disks/NEVER-SEEN/f1", json={})
    assert got.status_code == 404 and "אינו מוכר" in got.json()["detail"]
    assert admin.delete("/api/console/disks/NEVER-SEEN/f1").status_code == 404


def test_only_an_admin_marks_or_clears(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _seen(ctx, CLONER1, [_disk(1)], time.time())
    for client in (deploy, anon):
        assert client.post("/api/console/disks/S1/f1", json={}).status_code in (401, 403)
        assert client.delete("/api/console/disks/S1/f1").status_code in (401, 403)
    assert f1_marks.all_marks(ctx.conn) == {}


def test_a_bad_mac_is_a_400_not_a_mark(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _seen(ctx, CLONER1, [_disk(1)], time.time())
    assert admin.post("/api/console/disks/S1/f1", json={"mac": 7}).status_code == 400
    assert f1_marks.all_marks(ctx.conn) == {}


# ---------- הצגה: דף הדיסקים ----------

def test_a_marked_disk_is_red_and_says_f1_not_smart(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _seen(ctx, CLONER1, [_disk(1), _disk(2)], time.time())
    disk_sets.note_hello(ctx.conn, CLONER1, [_disk(1), _disk(2)])
    _event(ctx, "S2", "ok")
    assert _disks_api(admin)["S2"]["state"] == "ok" and _disks_api(admin)["S2"]["f1"] is None
    admin.post("/api/console/disks/S2/f1", json={"mac": CLONER1})
    got = _disks_api(admin)
    assert got["S2"]["state"] == "red" and got["S2"]["reason"] == "f1_observed"
    assert got["S2"]["f1"]["source"] == "manual" and got["S2"]["f1"]["date"]
    assert got["S1"]["f1"] is None and got["S1"]["state"] != "red"          # רק הדיסק המסומן
    admin.delete("/api/console/disks/S2/f1")
    assert _disks_api(admin)["S2"]["state"] == "ok" and _disks_api(admin)["S2"]["f1"] is None


def test_a_disk_red_for_its_own_smart_keeps_its_reason_and_gains_the_mark(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    _seen(ctx, CLONER1, [_disk(2, **RED)], time.time())
    disk_sets.note_hello(ctx.conn, CLONER1, [_disk(2, **RED)])
    _event(ctx, "S2", "fail", "health_failed")
    admin.post("/api/console/disks/S2/f1", json={})
    d = _disks_api(admin)["S2"]
    assert d["state"] == "red" and d["reason"] == "health_failed" and d["f1"]["source"] == "manual"


def test_the_disks_list_does_not_hide_a_failed_read_of_the_marks(room_server):
    """קריאה שנכשלה היא 503, לא רשימה בלי סימונים (עיקרון 5)."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    ctx.conn.execute("ALTER TABLE disk_f1_marks RENAME TO disk_f1_marks_gone")
    ctx.conn.commit()
    got = admin.get("/api/console/disks")
    assert got.status_code == 503 and "F1" in got.json()["detail"]


# ---------- הצגה: מחשבים ו"לא הגיע" ----------

def test_machines_names_the_marked_disk_as_the_known_f1_and_flags_it_on_the_disk(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    _seen(ctx, CLONER1, [_disk(1), _disk(2)], now - 86400)
    learn_wol.sent(ctx.conn, CLONER1, now=now - 400)
    assert _row(admin)["never_woke"]["f1"] is None                           # בלי סימון ובלי SMART אדום: כמו היום
    admin.post("/api/console/disks/S2/f1", json={"mac": CLONER1})
    row = _row(admin)
    f1 = row["never_woke"]["f1"]
    assert f1["basis"] == "observed" and f1["serial"] == "S2" and f1["drawer"] == 2 and f1["smart_red"] is False
    assert f1["observed"]["source"] == "manual" and f1["observed"]["date"]
    assert [d.get("f1_observed") is not None for d in row["disks"]] == [False, True]   # הקונסולה לא מציעה לסמן שוב


def test_a_marked_disk_wins_over_a_smart_red_one_and_both_proofs_are_said(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    _seen(ctx, CLONER1, [_disk(1, **RED), _disk(3, **RED)], now - 86400)
    learn_wol.sent(ctx.conn, CLONER1, now=now - 400)
    assert _row(admin)["never_woke"]["f1"]["serial"] == "S1"                 # המגירה הנמוכה, SMART בלבד
    admin.post("/api/console/disks/S3/f1", json={})
    f1 = _row(admin)["never_woke"]["f1"]
    assert f1["serial"] == "S3" and f1["basis"] == "observed" and f1["smart_red"] is True and f1["drawer"] == 3


def test_a_mark_on_a_disk_no_longer_in_the_machine_names_nothing(room_server):
    """דיסק שהוחלף נשאר מסומן, אבל ה"לא הגיע" של המכונה אינו מזכיר דיסק שאינו בה."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    _seen(ctx, CLONER1, [_disk(1), _disk(2)], now - 86400)
    admin.post("/api/console/disks/S2/f1", json={})
    _seen(ctx, CLONER1, [_disk(1), _disk(2, serial="NEW-DISK")], now - 86400)   # הוחלף בדיסק חדש באותו חריץ
    learn_wol.sent(ctx.conn, CLONER1, now=now - 400)
    assert _row(admin)["never_woke"]["f1"] is None
    assert f1_marks.all_marks(ctx.conn)["S2"]["source"] == "manual"


def test_f1_disk_unit_observed_first_then_smart_by_drawer():
    marks = {"S4": {"at": "x", "date": "2026-11-05", "source": "auto"}}
    got = never_woke.f1_disk([_disk(1, **RED), _disk(4)], SLOTS, marks)
    assert got["serial"] == "S4" and got["basis"] == "observed" and got["smart_red"] is False
    assert got["observed"] == {"at": "x", "date": "2026-11-05", "source": "auto"}
    assert never_woke.f1_disk([_disk(1, **RED), _disk(4)], SLOTS)["serial"] == "S1"          # בלי סימונים: כמו היום
    assert never_woke.f1_disk([_disk(1, **RED), _disk(4)], SLOTS, None)["basis"] == "smart_red"
    assert never_woke.f1_disk([_disk(1), _disk(4)], SLOTS, {"OTHER": marks["S4"]}) is None  # סימון של דיסק אחר
    assert never_woke.f1_disk([_disk(4, serial=None)], SLOTS, marks) is None                # בלי serial אין זהות


def test_an_unreadable_marks_table_adds_no_proof(room_server):
    """הסימונים לא נקראו: אין ראיה שנייה -- ובכל זאת לא 'אין F1' בשקט; ה"לא הגיע" נשאר כמו שהוא."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    now = time.time()
    _seen(ctx, CLONER1, [_disk(2)], now - 86400)
    admin.post("/api/console/disks/S2/f1", json={})
    learn_wol.sent(ctx.conn, CLONER1, now=now - 400)
    ctx.conn.execute("ALTER TABLE disk_f1_marks RENAME TO disk_f1_marks_gone")
    ctx.conn.commit()
    assert f1_marks.all_marks(ctx.conn) is None
    got = _row(admin)
    assert got["never_woke"]["state"] == "never_woke" and got["never_woke"]["f1"] is None


# ---------- hello: מה הסוכן מקבל ----------

def test_hello_carries_the_marks_of_the_disks_the_machine_sent(room_server):
    ctx, admin, anon = room_server["ctx"], room_server["admin"], room_server["anon"]
    _seen(ctx, CLONER1, [_disk(1), _disk(2)], time.time())
    assert _hello(anon, [_disk(1), _disk(2)])["f1_observed"] == []            # [] = אין
    admin.post("/api/console/disks/S2/f1", json={})
    ctx.conn.execute("INSERT INTO disk_f1_marks (serial, observed_at, source, marked_by, mac)"
                     " VALUES ('NOT-HERE', 'x', 'manual', '', NULL)")
    ctx.conn.commit()
    got = _hello(anon, [_disk(1), _disk(2)])["f1_observed"]
    assert [g["serial"] for g in got] == ["S2"]                               # רק דיסק שהמכונה שלחה
    assert got[0]["source"] == "manual" and len(got[0]["date"]) == 10


def test_hello_omits_the_field_when_the_read_failed_not_an_empty_list(room_server):
    """חסר = הסוכן משאיר את מה שקיבל; [] = 'אין' ומנקה. קריאה שנכשלה אינה 'אין'."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    ctx.conn.execute("ALTER TABLE disk_f1_marks RENAME TO disk_f1_marks_gone")
    ctx.conn.commit()
    assert "f1_observed" not in _hello(anon, [_disk(1)])


# ---------- סימון אוטומטי: מכונה שהוצגה "ייתכן F1" וחזרה ----------

def _wait_for_f1(ctx, now, silent_for=400):
    _seen(ctx, CLONER1, [_disk(1), _disk(2, **RED)], now - 86400)
    learn_wol.sent(ctx.conn, CLONER1, now=now - silent_for)


def test_a_machine_shown_as_maybe_f1_that_comes_back_is_marked_automatically(room_server):
    ctx, admin, anon = room_server["ctx"], room_server["admin"], room_server["anon"]
    now = time.time()
    _wait_for_f1(ctx, now)
    assert _row(admin)["never_woke"]["f1"]["basis"] == "smart_red"          # זה מה שהוצג
    _hello(anon, [_disk(1), _disk(2, **RED)])                                # ...וחזרה
    mark = f1_marks.all_marks(ctx.conn)
    assert set(mark) == {"S2"} and mark["S2"]["source"] == "auto" and mark["S2"]["mac"] == CLONER1
    assert [j["detail"] for j in _journal(ctx, "disk_f1_marked")][0].endswith("סומן אוטומטית: המכונה לא הגיעה אחרי WoL וחזרה")
    assert _row(admin)["never_woke"] is None                                 # הגיעה
    assert _disks_api(admin)["S2"]["f1"]["source"] == "auto"


def test_the_automatic_mark_is_taken_before_the_hello_refreshes_what_was_seen(room_server):
    """הדיסק הוא זה שהקונסולה הציגה (מה-hello הקודם): hello חדש שהדיסק האדום כבר איננו בו אינו מבטל את הסימון."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _wait_for_f1(ctx, time.time())
    _hello(anon, [_disk(1)])
    assert set(f1_marks.all_marks(ctx.conn)) == {"S2"}


def test_a_late_arrival_is_marked_even_after_the_answer_window(room_server):
    """"לא הגיע" אינו פג אחרי ANSWER_S: מכונה שעמדה שעה על F1 ושמישהו לחץ -- היא הוצגה כך, וחזרה."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _wait_for_f1(ctx, time.time(), silent_for=learn_wol.ANSWER_S + 3600)
    _hello(anon, [_disk(1), _disk(2, **RED)])
    assert set(f1_marks.all_marks(ctx.conn)) == {"S2"}


@pytest.mark.parametrize("why,setup", [
    ("no wake was sent", lambda ctx, now: _seen(ctx, CLONER1, [_disk(2, **RED)], now - 86400)),
    ("under three minutes", lambda ctx, now: (_seen(ctx, CLONER1, [_disk(2, **RED)], now - 86400),
                                              learn_wol.sent(ctx.conn, CLONER1, now=now - 150))),
    ("the disks read healthy", lambda ctx, now: (_seen(ctx, CLONER1, [_disk(1), _disk(2)], now - 86400),
                                                 learn_wol.sent(ctx.conn, CLONER1, now=now - 400))),
    ("a red disk for another reason", lambda ctx, now: (
        _seen(ctx, CLONER1, [_disk(2, smart="fail", reason="readonly")], now - 86400),
        learn_wol.sent(ctx.conn, CLONER1, now=now - 400))),
    ("the machine was seen after the send", lambda ctx, now: (
        learn_wol.sent(ctx.conn, CLONER1, now=now - 400), _seen(ctx, CLONER1, [_disk(2, **RED)], now - 100))),
])
def test_no_automatic_mark_unless_the_console_was_showing_maybe_f1(room_server, why, setup):
    ctx, anon = room_server["ctx"], room_server["anon"]
    setup(ctx, time.time())
    _hello(anon, [_disk(1), _disk(2, **RED)])
    assert f1_marks.all_marks(ctx.conn) == {}, why
    assert _journal(ctx, "disk_f1_marked") == []


def test_a_machine_with_no_stored_disks_is_not_marked(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    learn_wol.sent(ctx.conn, CLONER1, now=time.time() - 400)
    ctx.conn.execute("DELETE FROM net_devices WHERE mac = ?", (CLONER1,))
    ctx.conn.commit()
    _hello(anon, [_disk(2, **RED)])
    assert f1_marks.all_marks(ctx.conn) == {}


def test_an_already_marked_disk_is_not_marked_again_and_the_first_mark_stays(room_server):
    ctx, admin, anon = room_server["ctx"], room_server["admin"], room_server["anon"]
    now = time.time()
    _wait_for_f1(ctx, now)
    admin.post("/api/console/disks/S2/f1", json={})
    before = f1_marks.all_marks(ctx.conn)["S2"]
    _hello(anon, [_disk(1), _disk(2, **RED)])
    assert f1_marks.all_marks(ctx.conn)["S2"] == before and before["source"] == "manual"
    assert len(_journal(ctx, "disk_f1_marked")) == 1


def test_the_second_hello_marks_nothing_new(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _wait_for_f1(ctx, time.time())
    _hello(anon, [_disk(1), _disk(2, **RED)])
    first = f1_marks.all_marks(ctx.conn)
    ctx.conn.execute("DELETE FROM disk_f1_marks")                           # נוקה ידנית אחרי ההגעה
    ctx.conn.commit()
    _hello(anon, [_disk(1), _disk(2, **RED)])
    assert first and f1_marks.all_marks(ctx.conn) == {}, "the wake was already consumed by the first arrival"


def test_a_failure_while_marking_never_fails_the_hello(room_server, monkeypatch):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _wait_for_f1(ctx, time.time())

    def boom(*_a, **_k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(f1_marks, "mark", boom)
    assert _hello(anon, [_disk(1), _disk(2, **RED)])["known"] is True
    assert f1_marks.all_marks(ctx.conn) == {}
