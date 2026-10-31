"""#1732 — הצד של השרת והקונסולה: דיסק אדום (SMART נכשל) לעולם אינו יעד.

* `_validate_target_slots` (‏room.py) — סבב חדר מהקונסולה, סבב ממסך משכפל
  (lcpull) והפצה ישירה עוברים שם — דוחה מגירה שה-hello דיווח עליה `smart: fail`.
* שאלת SMART עם `verdict: fail` אינה נרשמת (`well_formed_ask`), ו-`rescue` על
  שורה אדומה ישנה נדחה גם עם `confirmed: true` של קונסולה ישנה.
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


@pytest.fixture()
def ask_server(tmp_path, images_root):
    from fastapi.testclient import TestClient

    from server.console import users
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", wol_send=lambda _p: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:31", "name": "01", "group_id": "grp_CLONERS"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "anon": TestClient(app)}
    ctx.sender.stop()


CLONER = "aa:bb:cc:00:00:31"


def _ask_hello(srv, verdict: str):
    r = srv["anon"].post("/api/v1/agent/hello", json={
        **hello_body(CLONER), "joining": False,
        "ask": {"nonce": "3-2", "port": 2, "verdict": verdict, "reason": "pending_sectors"}})
    assert r.status_code == 200
    return r.json()


def _row(srv):
    return srv["ctx"].conn.execute("SELECT * FROM smart_asks WHERE mac = ?", (CLONER,)).fetchone()


def test_server_a_red_question_is_never_recorded(ask_server):
    """RED: על הקוד הישן שאלה עם verdict=fail נרשמה והוצגה בקונסולה עם "כתוב בכל זאת…"."""
    _ask_hello(ask_server, "fail")
    assert _row(ask_server) is None
    _ask_hello(ask_server, "failed_last")             # orange is still a question (both states)
    assert _row(ask_server)["verdict"] == "failed_last"


def test_server_refuses_rescue_on_a_red_row_even_with_confirmation(ask_server):
    """RED: שורה אדומה שנשארה מלפני העדכון (או סוכן ישן) — "כתוב בכל זאת" עם
    `confirmed: true` מהקונסולה (ישנה) נדחה ב-400 עם המשפט; על הקוד הישן 200."""
    conn = ask_server["ctx"].conn
    conn.execute("INSERT INTO smart_asks (mac, nonce, port, verdict, reason, asked_at)"
                 " VALUES (?, '3-2', 2, 'fail', 'pending_sectors', '2026-10-18T10:00:00+00:00')", (CLONER,))
    conn.commit()
    r = ask_server["admin"].post("/api/console/room/answer", json={
        "mac": CLONER, "nonce": "3-2", "answer": "rescue", "confirmed": True})
    assert r.status_code == 400 and r.json()["detail"] == RED, r.text
    assert _row(ask_server)["answer"] is None
    skip = ask_server["admin"].post("/api/console/room/answer", json={
        "mac": CLONER, "nonce": "3-2", "answer": "skip"})
    assert skip.status_code == 200, skip.text           # skipping it is always fine (both states)


# --- הקונסולה: אין "כתוב בכל זאת" על אדום --------------------------------------------


def test_console_has_no_write_button_and_no_confirmed_flag_for_red():
    """RED: על הקוד הישן console.js פתח חלון "לכתוב על דיסק אדום?" ושלח `confirmed: true`."""
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert "confirmed: true" not in js
    assert "לכתוב על דיסק אדום?" not in js
    assert RED in js
