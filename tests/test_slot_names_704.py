"""‏#704 — שם לכל חיבור של כונן במחשב השיכפול.

**הבקשה של נדב (11/10):** "אני רוצה בהגדרות של מחשב השיכפול שיראה לי
לאיזה חריצים מחוברים כוננים ... ואז אני אתן שם לכל התקן, נגיד דיסקים
1-3". ותוספת באותו יום: "נגיד מחר מחובר לי למחשב NVMe ואני רוצה לקרוא
לו 'דיסק מיוחד'".

**מפתח השם — החיבור, לא הדיסק:**

- כונן שהסוכן דיווח לו `port` (‏SATA, ‏`ataN`) → ‏`sata:<port>`. דיסק
  שהוחלף באותו חריץ **שומר את השם** — זה כל העניין.
- כונן בלי `port` (‏NVMe, ‏USB, בקר SCSI) → ‏`serial:<serial>`. הסוכן אינו
  מדווח היום שום מזהה יציב של חיבור ל-NVMe (אין כתובת PCI ב-hello), ולכן
  השם צמוד **לכונן** — הקונסולה אומרת את זה ליד השדה, לא מסתירה.
- בלי `port` ובלי `serial` → אין מפתח, ואין שם (לא ממציאים מזהה).

**השם הוא תווית בלבד.** הבחירה, ‏`target_slots` והמספור נשארים לפי
`port` (הכרעת נדב 09/09: ‏SATA 0 → דיסק 1); בלי שם התצוגה כמו היום.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from conftest import hello_body
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 — fixture

NAMES = "/api/console/machines/{}/slot-names"


def _disk(dev, serial, port=None, size=256060514304):
    d = {"dev": dev, "size_bytes": size, "model": "SSD", "serial": serial,
         "removable": False, "scheme": "gpt", "has_data": False}
    if port is not None:
        d["port"] = port
    return d


def _hello(client, mac, disks):
    body = hello_body(mac)
    body["disks"] = disks
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, mac, slot, name):
    return client.put(NAMES.format(mac), json={"slot": slot, "name": name})


def _machine(client, mac):
    return next(m for m in client.get("/api/console/machines").json() if m["mac"] == mac)


def _room_machine(client, mac):
    return next(m for m in client.get("/api/console/room").json()["machines"] if m["mac"] == mac)


def _journal(ctx, event):
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- שמירה וקריאה ------------------------------------------------------------

def test_admin_names_a_sata_slot_and_machines_returns_it(room_server):
    admin = room_server["admin"]
    r = _put(admin, CLONER1, "sata:1", "דיסק מערכת")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "slot": "sata:1", "name": "דיסק מערכת"}
    assert _machine(admin, CLONER1)["slot_names"] == {"sata:1": "דיסק מערכת"}
    # מכונה בלי שמות — אובייקט ריק, לא null ולא חסר: "אין שם" הוא ממצא.
    assert _machine(admin, CLONER2)["slot_names"] == {}


def test_the_name_is_trimmed_and_a_rename_replaces(room_server):
    admin = room_server["admin"]
    assert _put(admin, CLONER1, "sata:2", "  דיסק 2  ").json()["name"] == "דיסק 2"
    assert _put(admin, CLONER1, "sata:2", "Disk B").status_code == 200
    assert _machine(admin, CLONER1)["slot_names"] == {"sata:2": "Disk B"}


def test_null_clears_back_to_the_default(room_server):
    admin = room_server["admin"]
    _put(admin, CLONER1, "sata:1", "דיסק 1")
    r = _put(admin, CLONER1, "sata:1", None)
    assert r.status_code == 200 and r.json()["name"] is None
    assert _machine(admin, CLONER1)["slot_names"] == {}


def test_only_admin_can_name(room_server):
    assert _put(room_server["deploy"], CLONER1, "sata:1", "x").status_code == 403
    assert _put(room_server["anon"], CLONER1, "sata:1", "x").status_code == 401
    assert _machine(room_server["admin"], CLONER1)["slot_names"] == {}


@pytest.mark.parametrize("name", [
    "", "   ",                         # ריק
    "א" * 25,                          # ארוך מ-24
    "a|b", "a\nb", "a\tb", "<b>", 'a"b', "a'b", "a;b", "a\\b", "a&b",
    5, ["x"],                          # לא מחרוזת
])
def test_bad_names_are_refused(room_server, name):
    r = _put(room_server["admin"], CLONER1, "sata:1", name)
    assert r.status_code == 400, (name, r.text)
    assert _machine(room_server["admin"], CLONER1)["slot_names"] == {}


def test_allowed_characters(room_server):
    for name in ("דיסק 1", "Disk-2", "NVMe (מהיר)", "מגירה #3", "1.5TB/A", "א" * 24):
        assert _put(room_server["admin"], CLONER1, "sata:3", name).status_code == 200, name


@pytest.mark.parametrize("slot", [
    "sata:0", "sata:33", "sata:-1", "sata:1x", "sata:", "port:1", "1", "",
    "serial:", "serial:a|b", "serial:a b", "serial:" + "x" * 81, None, 1,
])
def test_bad_slot_keys_are_refused(room_server, slot):
    r = _put(room_server["admin"], CLONER1, slot, "x")
    assert r.status_code == 400, (slot, r.text)


def test_unknown_fields_and_bad_body_are_refused(room_server):
    admin = room_server["admin"]
    assert admin.put(NAMES.format(CLONER1),
                     json={"slot": "sata:1", "name": "x", "order": 1}).status_code == 400
    assert admin.put(NAMES.format(CLONER1), json=["sata:1"]).status_code == 400
    assert admin.put(NAMES.format(CLONER1), json={"name": "x"}).status_code == 400


def test_unregistered_machine_is_404(room_server):
    assert _put(room_server["admin"], "aa:bb:cc:00:00:99", "sata:1", "x").status_code == 404
    assert _put(room_server["admin"], "not-a-mac", "sata:1", "x").status_code == 404


def test_naming_is_journaled_with_who(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    _put(admin, CLONER1, "sata:1", "דיסק 1")
    _put(admin, CLONER1, "sata:1", None)
    rows = ctx.conn.execute("SELECT user, detail FROM journal WHERE event = 'machine_slot_name'"
                            " ORDER BY id").fetchall()
    assert [r["user"] for r in rows] == ["noc", "noc"]
    assert CLONER1 in rows[0]["detail"] and "sata:1" in rows[0]["detail"]
    assert "דיסק 1" in rows[0]["detail"] and "cleared" in rows[1]["detail"]


# --- השם הולך עם החריץ, לא עם הדיסק -----------------------------------------

def test_the_name_follows_the_slot_when_the_disk_is_replaced(room_server):
    admin = room_server["admin"]
    _put(admin, CLONER1, "sata:2", "האמצעית")
    _hello(admin, CLONER1, [_disk("sda", "OLD-1", 2)])
    d = _room_machine(admin, CLONER1)["drawer_list"][0]
    assert d["port"] == 2 and d["slot_name"] == "האמצעית"
    # הדיסק הוחלף באותה מגירה — סידורי אחר, ושם ההתקן אחר (sdb).
    _hello(admin, CLONER1, [_disk("sdb", "NEW-2", 2)])
    d = _room_machine(admin, CLONER1)["drawer_list"][0]
    assert d["serial"] == "NEW-2" and d["slot_name"] == "האמצעית"


def test_a_disk_moved_to_another_slot_takes_that_slots_name(room_server):
    admin = room_server["admin"]
    _put(admin, CLONER1, "sata:1", "עליונה")
    _put(admin, CLONER1, "sata:3", "תחתונה")
    _hello(admin, CLONER1, [_disk("sda", "S-1", 3)])
    assert _room_machine(admin, CLONER1)["drawer_list"][0]["slot_name"] == "תחתונה"


def test_an_nvme_without_port_is_named_by_serial(room_server):
    admin = room_server["admin"]
    _hello(admin, CLONER1, [_disk("sda", "SATA-1", 1), _disk("nvme0n1", "NV-77", None)])
    assert _put(admin, CLONER1, "serial:NV-77", "דיסק מיוחד").status_code == 200
    drawers = {d["dev"]: d for d in _room_machine(admin, CLONER1)["drawer_list"]}
    assert drawers["nvme0n1"]["slot_name"] == "דיסק מיוחד"
    assert drawers["sda"]["slot_name"] is None          # SATA בלי שם — תצוגה כמו היום


def test_a_serial_name_does_not_cover_a_disk_with_a_port(room_server):
    """מפתח אחד לכל כונן: יש `port` → ‏`sata:N` בלבד. שם לפי סידורי של
    כונן SATA היה הולך עם הדיסק, וזה בדיוק מה שהבקשה פוסלת."""
    admin = room_server["admin"]
    _put(admin, CLONER1, "serial:S-1", "לפי סידורי")
    _hello(admin, CLONER1, [_disk("sda", "S-1", 1)])
    assert _room_machine(admin, CLONER1)["drawer_list"][0]["slot_name"] is None


def test_room_carries_the_names_of_empty_slots_too(room_server):
    admin = room_server["admin"]
    _put(admin, CLONER1, "sata:3", "ריקה")
    _hello(admin, CLONER1, [_disk("sda", "S-1", 1)])
    rm = _room_machine(admin, CLONER1)
    assert rm["slot_names"] == {"sata:3": "ריקה"}
    assert _room_machine(admin, CLONER2)["slot_names"] == {}


def test_hello_answer_carries_this_machines_names(room_server):
    admin = room_server["admin"]
    _put(admin, CLONER1, "sata:1", "דיסק 1")
    _put(admin, CLONER2, "sata:1", "של השני")
    answer = _hello(admin, CLONER1, [_disk("sda", "S-1", 1)])
    assert answer["slot_names"] == {"sata:1": "דיסק 1"}


def test_names_do_not_change_target_selection(room_server):
    """השם הוא תווית: ‏`selected` ו-`port` של המגירה לא זזים בגללו."""
    admin = room_server["admin"]
    _hello(admin, CLONER1, [_disk("sda", "S-1", 1), _disk("sdb", "S-2", 2)])
    before = _room_machine(admin, CLONER1)["drawer_list"]
    _put(admin, CLONER1, "sata:2", "דיסק ראשון")
    after = _room_machine(admin, CLONER1)["drawer_list"]
    strip = lambda ds: [{k: v for k, v in d.items() if k != "slot_name"} for d in ds]  # noqa: E731
    assert strip(before) == strip(after)


# --- מחזור החיים של המכונה ---------------------------------------------------

def test_mac_edit_moves_the_names_and_removal_drops_them(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    _put(admin, CLONER1, "sata:1", "דיסק 1")
    new = "aa:bb:cc:00:00:31"
    assert admin.put(f"/api/console/machines/{CLONER1}", json={"mac": new}).status_code == 200
    assert _machine(admin, new)["slot_names"] == {"sata:1": "דיסק 1"}
    assert admin.delete(f"/api/console/machines/{new}").status_code == 200
    assert ctx.conn.execute("SELECT COUNT(*) FROM machine_slot_names").fetchone()[0] == 0


def test_migration_keeps_names_and_adds_the_table_to_an_old_db(tmp_path: Path):
    from server import db                                   # noqa: PLC0415
    path = tmp_path / "imagesrv.db"
    database = db.connect(path)
    conn = database.connection
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'G', 'cloner')")
    conn.execute("INSERT INTO machines (mac, suffix, group_id, added_at)"
                 " VALUES ('aa:bb:cc:00:00:21', '01', 'g', 'now')")
    conn.execute("INSERT INTO machine_slot_names (mac, slot, name, set_at, set_by)"
                 " VALUES ('aa:bb:cc:00:00:21', 'sata:1', 'דיסק 1', 'now', 'noc')")
    conn.commit()
    conn.close()
    # התקנה ישנה: אין טבלה בכלל — העלייה יוצרת אותה בלי לגעת בשאר.
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE machine_slot_names")
    raw.commit()
    raw.close()
    conn = db.connect(path).connection
    assert conn.execute("SELECT COUNT(*) FROM machine_slot_names").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM machines").fetchone()[0] == 1
    conn.execute("INSERT INTO machine_slot_names (mac, slot, name, set_at, set_by)"
                 " VALUES ('aa:bb:cc:00:00:21', 'sata:2', 'דיסק 2', 'now', 'noc')")
    conn.commit()
    conn.close()
    # עלייה נוספת: השורה נשמרת (CREATE TABLE IF NOT EXISTS אינו נוגע בה).
    conn = db.connect(path).connection
    assert [tuple(r) for r in conn.execute("SELECT slot, name FROM machine_slot_names")] == [
        ("sata:2", "דיסק 2")]


# --- היומן והדוחות -----------------------------------------------------------

def test_disk_failure_journal_names_the_slot(room_server):
    from server.disks import disk_failures                         # noqa: PLC0415
    admin, ctx = room_server["admin"], room_server["ctx"]
    _put(admin, CLONER1, "sata:2", "האמצעית")
    _hello(admin, CLONER1, [_disk("sdb", "S-2", 2)])
    disk_failures.record(ctx.conn, "ses_1", CLONER1,
                         {"dev": "sdb", "serial": "S-2", "port": 2, "ata_log": []})
    assert 'name="האמצעית"' in _journal(ctx, "disk_failure")[-1]


def test_disk_failure_journal_without_a_name_is_as_before(room_server):
    from server.disks import disk_failures                         # noqa: PLC0415
    ctx = room_server["ctx"]
    disk_failures.record(ctx.conn, "ses_1", CLONER1,
                         {"dev": "sdb", "serial": "S-2", "port": 2, "ata_log": []})
    assert _journal(ctx, "disk_failure")[-1] == (
        f"{CLONER1} sdb serial=S-2 port=2 cause=unclassified")


def test_disk_decision_journal_and_session_view_name_the_slot(room_server):
    from server.disks import disk_events                           # noqa: PLC0415
    admin, ctx = room_server["admin"], room_server["ctx"]
    _put(admin, CLONER1, "serial:NV-1", "דיסק מיוחד")
    assert disk_events.ingest(ctx.conn, {
        "session_id": "ses_1", "mac": CLONER1, "disk": "nvme0n1", "serial": "NV-1",
        "smart": {"verdict": "fail"}, "decision": "skip"})["ok"]
    assert 'name="דיסק מיוחד"' in _journal(ctx, "disk_decision")[-1]
    view = disk_events.for_session(ctx.conn, "ses_1")
    assert view[0]["slot_name"] == "דיסק מיוחד"


def test_per_drawer_drivers_journal_names_the_slot_from_the_reported_disks(room_server):
    """דיווח ההתקדמות של מגירה נושא רק `dev` — החריץ נגזר מהדיסקים שהמכונה
    דיווחה ב-hello (sdb הוא חריץ 2 כאן, לא "דיסק 2" לפי האות)."""
    from server.disks import slot_names                            # noqa: PLC0415
    admin, ctx = room_server["admin"], room_server["ctx"]
    _put(admin, CLONER1, "sata:3", "תחתונה")
    _hello(admin, CLONER1, [_disk("sda", "S-1", 1), _disk("sdb", "S-3", 3)])
    assert slot_names.label(ctx.conn, CLONER1, dev="sdb") == "תחתונה"
    assert slot_names.label(ctx.conn, CLONER1, dev="sda") is None
    assert slot_names.label(ctx.conn, CLONER1, dev="sdz") is None
    assert slot_names.label(ctx.conn, CLONER2, dev="sdb") is None
