"""‏#402 — צד השרת של `disk_probe`: הראיה שהסוכן אוסף לא נזרקת בכניסה.

הסוכן (‏PR #798) כבר שולח ב-hello `disk_probe` — ‏`drives` / `no_disks` /
`no_ports` / `unchecked` (ממשק 2) — וזה מה שמבחין בין "יש פורטי SATA
ולא חובר כונן" (חברו כונן) לבין "הבקר מנוטרל בקושחה" (געו בביוס, כבל לא
יעזור). עד כאן `hello.py` שמר רק `disks_json`, והמפעיל ראה `מגירות=0`
בשני המצבים — אותו קיפול שה-Issue נפתח עליו, שכבה אחת גבוה יותר.

מה נבדק: hello עם השדה → נשמר ב-`net_devices.disk_probe` → מוחזר
ב-`/api/console/net`, ב-`/api/console/machines` וב-`/api/console/room`;
ערך זר/חסר אינו דורס ערך קודם (COALESCE, כמו `disks_json`), ו-`unchecked`
נשאר `unchecked` — לא `no_disks` (עיקרון 5).
"""

from __future__ import annotations

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

CLONER = "aa:bb:cc:00:04:02"


def _register(server) -> None:
    assert server["admin"].post("/api/console/machines", json={
        "mac": CLONER, "name": "shich-402", "group_id": "grp_CLONERS",
    }).status_code == 200


def _hello(server, disk_probe=None, disks: list | None = None) -> None:
    body = hello_body(CLONER)
    body["disks"] = [] if disks is None else disks
    if disk_probe is not None:
        body["disk_probe"] = disk_probe
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def _stored(server) -> str | None:
    row = server["ctx"].conn.execute(
        "SELECT disk_probe FROM net_devices WHERE mac = ?", (CLONER,)).fetchone()
    return row["disk_probe"]


def _net(server) -> dict:
    return next(d for d in server["admin"].get("/api/console/net").json()
                if d["mac"] == CLONER)


def _machine(server) -> dict:
    return next(m for m in server["admin"].get("/api/console/machines").json()
                if m["mac"] == CLONER)


def _room_machine(server) -> dict:
    return next(m for m in server["admin"].get("/api/console/room").json()["machines"]
                if m["mac"] == CLONER)


# ‏#2334: `no_driver` — בקר אחסון בלי מנהל התקן; בלי `DISK_PROBE_VALUES` הוא נזרק ל-None.
@pytest.mark.parametrize("probe", ["no_disks", "no_ports", "unchecked", "no_driver"])
def test_zero_disks_carries_why_to_all_three_console_views(server, probe):
    """שלושה ממצאים שונים על אותו `0` — כל אחד מגיע כמות שהוא לשלושת
    המסכים שמציגים מגירות. ‏`unchecked` חייב להישאר `unchecked`."""
    _register(server)
    _hello(server, disk_probe=probe)
    # ה-API קודם, ב-`.get` — כדי שהבקרה השלילית תיפול על הערך (None ≠ probe)
    # ולא על KeyError/עמודה חסרה, שהם "הטסט לא רץ" ולא "הטסט בדק".
    assert _net(server).get("disk_probe") == probe
    machine = _machine(server)
    assert machine["disks"] == [] and machine.get("disk_probe") == probe
    rm = _room_machine(server)
    assert rm["drawers"] == 0 and rm.get("disk_probe") == probe
    assert _stored(server) == probe


def test_an_old_agent_leaves_disk_probe_null_not_a_guess(server):
    """סוכן ישן לא שולח את השדה — ‏null, לא `no_disks` ולא `unchecked`:
    "לא נשלח" הוא מצב רביעי, והקונסולה מציגה עליו את הטקסט הישן."""
    _register(server)
    _hello(server)
    assert _net(server).get("disk_probe") is None
    assert _machine(server).get("disk_probe") is None
    assert _room_machine(server).get("disk_probe") is None
    assert _stored(server) is None


def test_a_foreign_value_does_not_overwrite_the_last_good_probe(server):
    """ערך שאינו אחד מארבעת הערכים נזנח כמו שדה לא ידוע, והשורה שומרת
    את הממצא הקודם — כמו `disks_json` (#417): דיווח פגום אינו מוחק
    דיווח תקין."""
    _register(server)
    _hello(server, disk_probe="no_ports")
    _hello(server, disk_probe="controller-on-fire")
    assert _net(server).get("disk_probe") == "no_ports"
    _hello(server, disk_probe=42)
    assert _net(server).get("disk_probe") == "no_ports"
    assert _stored(server) == "no_ports"


def test_drives_replaces_an_earlier_no_ports_once_a_disk_shows_up(server):
    """הטכנאי הדליק את הבקר וחיבר כונן: ה-hello הבא אומר `drives`,
    והראיה הישנה אינה נשארת תלויה ליד דיסק שכבר קיים."""
    _register(server)
    _hello(server, disk_probe="no_ports")
    _hello(server, disk_probe="drives", disks=[{
        "dev": "sda", "size_bytes": 256060514304, "model": "Drawer SSD",
        "serial": "S402", "removable": False, "scheme": "gpt",
        "has_data": False, "port": 1,
    }])
    assert _room_machine(server).get("disk_probe") == "drives"
    assert _room_machine(server)["drawers"] == 1
    assert _stored(server) == "drives"
