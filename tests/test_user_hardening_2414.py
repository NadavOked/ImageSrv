"""#2414 — שלושה ליקויי הקשחה בניהול משתמשים.

1. ‏`PUT /users/{username}`: השומרים "אי אפשר לשנות את התפקיד של עצמך" ו"אי אפשר
   לחסום את המשתמש המחובר" השוו שם עם רגישות לרישיות, ו-`users.lookup` מוצא בלי.
2. ‏`POST /me/password` לא עבר דרך `login_guard` — session גנוב ניחש את הסיסמה
   הנוכחית בלי הגבלה.
3. ‏`console_idle_seconds` נאכף רק בדפדפן; העוגייה נשארה תקפה 12 שעות.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.console import auth, login_guard, presence, users
from server.db import set_setting


def _role(server, name):
    return users.lookup(server["ctx"].conn, name)["role"]


# ---------- 1. השוואת שם בלי תלות ברישיות ----------


def test_own_role_cannot_be_changed_through_another_case(server):
    r = server["admin"].put("/api/console/users/NOC", json={"role": "deploy"})
    assert r.status_code == 400, r.text
    assert "של עצמך" in r.json()["detail"]
    assert _role(server, "noc") == "admin"


def test_own_account_cannot_be_disabled_through_another_case(server):
    r = server["admin"].put("/api/console/users/Noc", json={"disabled": True})
    assert r.status_code == 400, r.text
    assert users.lookup(server["ctx"].conn, "noc")["disabled_at"] is None


def test_another_user_is_still_editable_in_any_case(server):
    """השומר הוא על "עצמך" בלבד — שם אחר, בכל רישיות, עדיין נערך."""
    r = server["admin"].put("/api/console/users/LABTECH", json={"disabled": True})
    assert r.status_code == 200, r.text
    assert users.lookup(server["ctx"].conn, "labtech")["disabled_at"] is not None


# ---------- 2. ‏/me/password תחת login_guard ----------


@pytest.fixture()
def guard_clock(monkeypatch):
    t = {"now": 3_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    return t


def test_wrong_current_password_is_counted_and_locks(server, guard_clock):
    admin = server["admin"]
    for n in range(login_guard.LOCK_AFTER):
        guard_clock["now"] += 600          # מעבר להשהיה — רק הספירה נבדקת
        r = admin.post("/api/console/me/password", json={
            "current_password": f"wrong-{n}", "new_password": "Bb12345!",
        })
        assert r.status_code == 400, (n, r.text)
    guard_clock["now"] += 1
    locked = admin.post("/api/console/me/password", json={
        "current_password": "admin-pass-123", "new_password": "Bb12345!",
    })
    assert locked.status_code == 403, locked.text
    assert locked.headers.get("Retry-After")
    assert users.verify(server["ctx"].conn, "noc", "admin-pass-123") == "admin"


def test_a_policy_violation_is_not_a_guess(server, guard_clock):
    """סיסמה נוכחית נכונה וסיסמה חדשה חלשה — 400, אבל לא כשל שנספר."""
    r = server["admin"].post("/api/console/me/password", json={
        "current_password": "admin-pass-123", "new_password": "short",
    })
    assert r.status_code == 400
    assert server["ctx"].conn.execute(
        "SELECT 1 FROM login_attempts WHERE key LIKE 'noc|%'").fetchone() is None


def test_successful_change_clears_the_counter(server, guard_clock):
    admin = server["admin"]
    for n in range(3):
        guard_clock["now"] += 600
        admin.post("/api/console/me/password", json={
            "current_password": f"wrong-{n}", "new_password": "Bb12345!",
        })
    ok = admin.post("/api/console/me/password", json={
        "current_password": "admin-pass-123", "new_password": "Bb12345!",
    })
    assert ok.status_code == 200, ok.text
    assert server["ctx"].conn.execute(
        "SELECT 1 FROM login_attempts WHERE key LIKE 'noc|%'").fetchone() is None


# ---------- 3. ניתוק בחוסר פעילות בשרת ----------


@pytest.fixture()
def idle_clock(monkeypatch):
    t = {"now": time.time()}
    monkeypatch.setattr(presence, "_now", lambda: t["now"])
    monkeypatch.setattr(presence, "_written", {})
    return t


def _idle_limit(server) -> int:
    return 600 + presence.TOUCH_SECONDS


def test_an_idle_cookie_is_refused_and_stays_refused(server, idle_clock):
    admin = server["admin"]
    token = admin.cookies.get(auth.COOKIE_NAME)
    assert admin.get("/api/console/me").status_code == 200
    idle_clock["now"] += _idle_limit(server) + 1
    r = admin.get("/api/console/me")
    assert r.status_code == 401, r.text
    # מבוטלת ולא רק נדחית: הבקשה הבאה אינה יוצרת לה שורה חדשה ומחיה אותה.
    assert admin.get("/api/console/me").status_code == 401
    assert auth.check(server["ctx"].conn, token) is None


def test_activity_keeps_the_cookie_alive(server, idle_clock):
    admin = server["admin"]
    for _ in range(4):
        assert admin.get("/api/console/me").status_code == 200
        idle_clock["now"] += 600
    assert admin.get("/api/console/me").status_code == 200


def test_a_cookie_that_was_never_used_also_idles_out(server, idle_clock):
    """עוגייה שהונפקה ולא נשלחה אין לה שורה — הפעילות האחרונה היא ההנפקה."""
    c = TestClient(server["app"])
    complete_console_login(c, server["ctx"].conn, "noc", "admin-pass-123")
    idle_clock["now"] += _idle_limit(server) + 1
    assert c.get("/api/console/me").status_code == 401


def test_idle_zero_means_off_on_the_server_too(server, idle_clock):
    set_setting(server["ctx"].conn, "console_idle_seconds", "0")
    admin = server["admin"]
    assert admin.get("/api/console/me").status_code == 200
    idle_clock["now"] += 6 * 3600
    assert admin.get("/api/console/me").status_code == 200


def test_logout_of_an_idle_cookie_still_works(server, idle_clock):
    """דפדפן שחזר משינה שולח logout — הוא מתנתק, ולא "הניתוק לא אושר"."""
    admin = server["admin"]
    token = admin.cookies.get(auth.COOKIE_NAME)
    assert admin.get("/api/console/me").status_code == 200
    idle_clock["now"] += _idle_limit(server) + 1
    assert admin.post("/api/console/logout").status_code == 200
    assert auth.check(server["ctx"].conn, token) is None


# ---------- סקירת האבטחה על הענף: השער אחד לכל הדרכים ----------
# עד כאן חוסר הפעילות נבדק ב-`console_only` בלבד. שלושה מסלולים עקפו אותו: ראוטר
# המוניטור (מחוץ ל-console_only), כל WebSocket (`auth.check`), ופורט הסוכן. עכשיו
# הבדיקה ב-`auth.read_session`, שכולם עוברים בו.


def _idle_out(server, idle_clock):
    admin = server["admin"]
    token = admin.cookies.get(auth.COOKIE_NAME)
    assert admin.get("/api/console/me").status_code == 200
    idle_clock["now"] += _idle_limit(server) + 3600
    return admin, token


def test_an_idle_cookie_is_refused_on_the_monitor_router_too(server, idle_clock):
    admin, token = _idle_out(server, idle_clock)
    assert admin.get("/api/console/monitor/settings").status_code == 401
    assert admin.put("/api/console/monitor/settings", json={"enabled": True}).status_code == 401
    assert auth.check(server["ctx"].conn, token) is None


def test_an_idle_cookie_cannot_open_a_websocket(server, idle_clock):
    from starlette.websockets import WebSocketDisconnect
    _, token = _idle_out(server, idle_clock)
    ws = TestClient(server["app"], cookies={auth.COOKIE_NAME: token})
    with ws.websocket_connect("/api/console/monitor/aa:bb:cc:dd:ee:01") as sock:
        with pytest.raises(WebSocketDisconnect) as caught:
            sock.receive_bytes()
    assert caught.value.code == 4401
    assert auth.check(server["ctx"].conn, token) is None


def test_a_websocket_handshake_is_activity(server, idle_clock):
    """מוניטור בחלון נפרד: הלחיצה של ה-WebSocket נספרת כפעילות, כמו בקשת HTTP."""
    from starlette.websockets import WebSocketDisconnect
    admin = server["admin"]
    token = admin.cookies.get(auth.COOKIE_NAME)
    assert admin.get("/api/console/me").status_code == 200
    idle_clock["now"] += 600
    ws = TestClient(server["app"], cookies={auth.COOKIE_NAME: token})
    with ws.websocket_connect("/api/console/monitor/aa:bb:cc:dd:ee:01") as sock:
        with pytest.raises(WebSocketDisconnect) as caught:
            sock.receive_bytes()
    assert caught.value.code != 4401
    idle_clock["now"] += 600
    assert admin.get("/api/console/me").status_code == 200


def test_an_idle_console_cookie_is_refused_on_the_agent_port(server, idle_clock):
    from server.app import create_agent_app
    _, token = _idle_out(server, idle_clock)
    agent = TestClient(create_agent_app(server["app"].state.runtime),
                       cookies={auth.COOKIE_NAME: token})
    assert agent.get("/api/console/images").status_code == 401
    assert agent.post("/api/console/folders", json={"name": "idle-created"}).status_code == 401


def test_a_kiosk_session_is_not_cut_by_the_console_idle(server, idle_clock):
    """הגואי של מחשב הבנייה מחזיק את עוגיית הקיוסק לאורך פעולה שלמה בלי בקשות —
    סבב של 34 דקות ואז סגירה. היא פטורה (‏#1340: הקונסולה ממילא מסרבת לה)."""
    from server.app import create_agent_app
    agent = TestClient(create_agent_app(server["app"].state.runtime))
    r = agent.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text
    token = agent.cookies.get(auth.COOKIE_NAME)
    assert auth.read_session(server["ctx"].conn, token).purpose == auth.PURPOSE_KIOSK
    idle_clock["now"] += 3 * 3600
    assert agent.get("/api/console/images").status_code == 200


def test_a_password_change_cookie_idles_out_from_its_issue(server, idle_clock):
    server["admin"].post("/api/console/users", json={"username": "pwc", "password": "Aa12345!", "role": "admin"})
    temp = server["admin"].post("/api/console/users/pwc/reset-password").json()["new_password"]
    from server.app import create_console_app   # המשולב נכנס דרך הקיוסק, שמסרב להחלפה כפויה
    c = TestClient(create_console_app(server["app"].state.runtime))
    assert c.post("/api/console/login", json={"username": "pwc", "password": temp}).json() == {
        "must_change_password": True}
    idle_clock["now"] += _idle_limit(server) + 1
    r = c.post("/api/console/me/password", json={"current_password": temp, "new_password": "Bb12345!"})
    assert r.status_code == 401, r.text
    assert users.verify(server["ctx"].conn, "pwc", temp) == "admin"


def test_own_account_cannot_be_deleted_through_another_case(server):
    """השומר מזהה "עצמך" כמו ב-edit_user. (‏`users.delete` עצמו משווה שם מדויק, ולכן
    עד כאן התשובה הייתה 404 "לא קיים" — לא מחיקה; זה הסירוב הנכון, בשמו.)"""
    server["admin"].post("/api/console/users", json={"username": "admin2", "password": "Aa12345!", "role": "admin"})
    r = server["admin"].delete("/api/console/users/NOC")
    assert r.status_code == 400, r.text
    assert "המחובר" in r.json()["detail"]
    assert users.lookup(server["ctx"].conn, "noc") is not None
    assert server["admin"].delete("/api/console/users/admin2").status_code == 200
