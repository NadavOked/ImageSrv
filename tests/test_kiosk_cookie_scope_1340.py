"""#1340 (סקירת אבטחה 17/10): עוגיית הקיוסק אינה עוגיית קונסולה.

הכניסה בפורט הסוכן (‎:8080, ‏``/api/console/login`` מה-allowlist של
``kiosk.py``) מדלגת על MFA במכוון — מחשב הבנייה נכנס בסיסמה בלבד (#1075).
אבל העוגייה שהיא הנפיקה הייתה חתומה באותו סוד ובלי שום סימון, ולכן תקפה
גם בקונסולה (‎:8081): מי שיודע סיסמה של מנהל עם MFA — בלי הקוד — נכנס
דרך 8080, לוקח את העוגייה, ומקבל קונסולת ניהול מלאה. וגם: העוגייה עוברת
ב-HTTP בוילן ההפצה, וכל מי שמסניף אותה שם קיבל עוגיית ניהול.

התיקון: עוגייה שהקיוסק מנפיק נושאת ``purpose=kiosk``. הקונסולה מסרבת לה
(401), ופורט הסוכן ממשיך לקבל אותה בכל מה שמחשב הבנייה עושה.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.console import auth
from server.app import create_agent_app, create_console_app


def _mfa_admin(server, name="kioskadm"):
    """מנהל שאינו מובנה, עם MFA פעיל — כמו כל מנהל אמיתי אחרי #1085."""
    assert server["admin"].post("/api/console/users", json={
        "username": name, "password": "Aa12345!", "role": "admin",
    }).status_code == 200
    console = TestClient(create_console_app(server["app"].state.runtime))
    complete_console_login(console, server["ctx"].conn, name, "Aa12345!")
    return name


def _kiosk_login(server, name):
    kiosk = TestClient(create_agent_app(server["app"].state.runtime))
    r = kiosk.post("/api/console/login", json={"username": name, "password": "Aa12345!"})
    assert r.status_code == 200, r.text
    token = kiosk.cookies.get(auth.COOKIE_NAME)
    assert token, "הקיוסק לא הנפיק עוגייה"
    return kiosk, token


def test_kiosk_cookie_is_refused_by_the_console(server):
    """סיסמה בלבד ב-8080 → העוגייה **אינה** פותחת את 8081."""
    name = _mfa_admin(server)
    _kiosk, token = _kiosk_login(server, name)
    console = TestClient(create_console_app(server["app"].state.runtime))
    console.cookies.set(auth.COOKIE_NAME, token)
    for path in ("/api/console/me", "/api/console/users", "/api/console/settings"):
        r = console.get(path)
        assert r.status_code == 401, (path, r.status_code, r.text)
    r = console.post("/api/console/users", json={
        "username": "evil", "password": "Aa12345!", "role": "admin"})
    assert r.status_code == 401, r.text
    assert server["ctx"].conn.execute(
        "SELECT 1 FROM users WHERE username='evil'").fetchone() is None


def test_kiosk_cookie_is_refused_on_console_admin_routes_outside_console_only(server):
    """המוניטור נכלל בקונסולה בלי ``console_only`` (#904) — גם שם 401."""
    name = _mfa_admin(server, "kioskadm2")
    _kiosk, token = _kiosk_login(server, name)
    console = TestClient(create_console_app(server["app"].state.runtime))
    console.cookies.set(auth.COOKIE_NAME, token)
    r = console.get("/api/console/monitor/settings")
    assert r.status_code == 401, r.text


def test_kiosk_cookie_still_works_on_the_agent_port(server):
    """מה שמחשב הבנייה עושה בעוגייה הזו — ממשיך לעבוד בפורט הסוכן."""
    name = _mfa_admin(server, "kioskadm3")
    kiosk, _token = _kiosk_login(server, name)
    assert kiosk.get("/api/console/images").status_code == 200
    assert kiosk.get("/api/console/folders").status_code == 200
    assert kiosk.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200


def test_console_cookie_is_not_marked_kiosk(server):
    """הכניסה בקונסולה עצמה לא השתנתה: עוגייה מלאה, בלי purpose."""
    name = _mfa_admin(server, "kioskadm4")
    console = TestClient(create_console_app(server["app"].state.runtime))
    complete_console_login(console, server["ctx"].conn, name, "Aa12345!")
    found = auth.read_session(server["ctx"].conn, console.cookies.get(auth.COOKIE_NAME))
    assert found is not None and found.purpose == ""
    assert console.get("/api/console/users").status_code == 200


def test_a_deploy_kiosk_cookie_gets_the_1073_refusal_in_the_console(server):
    """‏#1626, "גמור": ‏deploy שנכנס בקיוסק מקבל בקונסולה את אותו 403 של #1073 —
    לא 401 ולא 200 — גם בנתיב המוניטור שמחוץ ל-``console_only``. לפני התיקון
    המוניטור ענה שם 403 אחר ("פעולה למנהל בלבד"), כי העוגייה הייתה מלאה."""
    kiosk = TestClient(create_agent_app(server["app"].state.runtime))
    r = kiosk.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    assert r.status_code == 200, r.text
    console = TestClient(create_console_app(server["app"].state.runtime))
    console.cookies.set(auth.COOKIE_NAME, kiosk.cookies.get(auth.COOKIE_NAME))
    for path in ("/api/console/users", "/api/console/monitor/settings"):
        r = console.get(path)
        assert r.status_code == 403, (path, r.status_code, r.text)
        assert r.json()["detail"] == auth.DEPLOY_NO_CONSOLE_HE, (path, r.text)
