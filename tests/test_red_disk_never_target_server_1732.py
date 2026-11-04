"""#1732 — הצד של השרת והקונסולה: דיסק אדום (SMART נכשל) לעולם אינו יעד.

* `_validate_target_slots` (‏room.py) — סבב חדר מהקונסולה, סבב ממסך משכפל
  (lcpull) והפצה ישירה עוברים שם — דוחה מגירה שה-hello דיווח עליה `smart: fail`.
* שאלת ה-SMART והתשובה מהקונסולה הוסרו ב-#2044 (test_smart_ask_removed_2044.py):
  אין עוד שורה אדומה שאפשר לענות עליה "כתוב".
* בקונסולה אין "כתוב בכל זאת" לאדום ולא נשלח `confirmed`.

הצד של הסוכן: test_red_disk_never_target_1732.py (שם גם פקודות הבקרה השלילית).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body  # noqa: E402
from test_cloner_round_server import now  # noqa: E402,F401  (autouse: the route's clock, past the rate limit)
from test_machine_identity import identity_server  # noqa: E402,F401
from test_server_room import room_server  # noqa: E402,F401

RED = "SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"


# --- השרת ---------------------------------------------------------------------------

def _red_hello(client, mac: str, red_port: int, n: int = 3) -> None:
    """hello של משכפל: n מגירות, ‏`red_port` עם SMART fail (כפי ש-sysinfo.sh שולח)."""
    body = hello_body(mac)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "port": i + 1,
         "model": "Drawer SSD", "serial": f"S{i + 1}", "removable": False, "scheme": "gpt",
         "has_data": False, "smart": "fail" if i + 1 == red_port else "ok"}
        for i in range(n)]
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text


def test_server_room_round_refuses_a_red_drawer(room_server):
    """RED: על הקוד הישן הסבב נפתח (200) על מגירה 2 האדומה."""
    from test_server_room import CLONER1, _open_with_slots
    admin = room_server["admin"]
    _red_hello(room_server["anon"], CLONER1, red_port=2)
    r = _open_with_slots(admin, [{"mac": CLONER1, "ports": [1, 2]}])
    assert r.status_code == 400 and RED in r.json()["detail"] and "דיסק 2" in r.json()["detail"], r.text
    assert admin.get("/api/console/room").json()["round"] is None
    # the healthy drawers alone open (passes in both states)
    assert _open_with_slots(admin, [{"mac": CLONER1, "ports": [1, 3]}]).status_code == 200


def test_server_direct_deploy_refuses_a_red_drawer(room_server):
    """RED: הפצה ישירה ממחשב בנייה — אותה בדיקה (‏_validate_target_slots)."""
    from test_direct_server import _open_direct, _register_build
    from test_server_room import CLONER1
    _register_build(room_server)
    _red_hello(room_server["anon"], CLONER1, red_port=1)
    r = _open_direct(room_server["admin"], [{"mac": CLONER1, "ports": [1]}])
    assert r.status_code == 400 and RED in r.json()["detail"], r.text
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
    assert _open_direct(room_server["admin"], [{"mac": CLONER1, "ports": [2]}]).status_code == 200


def test_server_cloner_round_refuses_its_own_red_drawer(identity_server):
    """RED: "שחזור אימג' מהשרת" ממסך המשכפל (lcpull) — המכונה עצמה מבקשת את
    המגירה האדומה; על הקוד הישן הסבב נפתח."""
    from test_cloner_round_server import MAC1, ask, no_round, register
    srv = identity_server
    register(srv, MAC1, "grp_CLONERS", "1")
    _red_hello(srv["station"], MAC1, red_port=3)
    r = ask(srv)                                    # ports [1, 3]
    assert r.status_code == 400 and r.json()["code"] == "bad_round" and RED in r.json()["error"], r.text
    assert no_round(srv)
    assert ask(srv, ports=[1, 2]).status_code == 200


# --- הקונסולה: אין "כתוב בכל זאת" על אדום --------------------------------------------


def test_console_has_no_write_button_and_no_confirmed_flag_for_red():
    """RED: על הקוד הישן console.js פתח חלון "לכתוב על דיסק אדום?" ושלח `confirmed: true`."""
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert "confirmed: true" not in js
    assert "לכתוב על דיסק אדום?" not in js
    assert RED in js
