"""#2757 — עוגיית הקונסולה מתחדשת בשימוש פעיל; חיבור לא-פעיל או מבוטל לא.

עד כאן ה-expiry נקבע פעם אחת בכניסה (12 שעות), ומפעיל שעבד ברציפות נזרק באמצע עבודה.
השעון של ``auth`` מוזרק (``auth.time``) -- שעון ``presence`` נשאר אמיתי, כך שהעוגייה
"בת 7 שעות" אבל הפעילות האחרונה שלה היא עכשיו, בדיוק מפעיל פעיל.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.console import auth, presence, session_renewal, users, viewer_sessions

SEVEN_HOURS = 7 * 3600     # נותרו 5 שעות מתוך 12: מתחת למחצית, ולכן מתחדשת
ONE_HOUR = 3600            # נותרו 11: עדיין לא
GRACE_PLUS = auth.GRACE_SECONDS + 5


@pytest.fixture()
def skew(monkeypatch):
    """הקדמת שעון ``auth`` בלבד. ``skew["s"] = 7 * 3600`` = העוגייה בת 7 שעות."""
    t = {"s": 0.0}
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: time.time() + t["s"]))
    monkeypatch.setattr(presence, "_written", {})
    monkeypatch.setattr(auth, "_grace", {})
    return t


def _client(server, user="noc", role="admin", purpose=""):
    token = auth.issue(server["ctx"].conn, user, role, purpose=purpose)
    return TestClient(server["app"], cookies={auth.COOKIE_NAME: token}), token


def _session_cookies(response) -> list[str]:
    return [c for c in response.headers.get_list("set-cookie")
            if c.startswith(auth.COOKIE_NAME + "=")]


def _value(set_cookie: str) -> str:
    return set_cookie.split(";")[0].partition("=")[2]


def _attrs(set_cookie: str) -> set[str]:
    return {p.strip().lower() for p in set_cookie.split(";")[1:]}


def _eve(server) -> None:
    users.create(server["ctx"].conn, "eve", "Aa12345!pw", "admin", by="test", check_policy=False)


def _expiry(token: str) -> int:
    return int(token.rpartition("|")[0].split("|")[2])


# ---------- חיבור פעיל מתחדש ----------


def test_an_active_cookie_near_expiry_is_renewed(server, skew):
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    r = client.get("/api/console/me")
    assert r.status_code == 200, r.text
    renewed = _session_cookies(r)
    assert len(renewed) == 1, r.headers.get_list("set-cookie")
    new = _value(renewed[0])
    assert _expiry(new) >= _expiry(old) + SEVEN_HOURS - 5
    assert auth.read_session(server["ctx"].conn, new).username == "noc"


def test_the_renewed_cookie_has_exactly_the_login_flags(server, skew):
    client, _ = _client(server)
    skew["s"] = SEVEN_HOURS
    renewed = _session_cookies(client.get("/api/console/me"))[0]
    from starlette.responses import Response
    login = Response()
    auth.attach_cookie(login, server["ctx"].conn, "noc", "admin", None)
    assert _attrs(renewed) == _attrs(login.headers["set-cookie"])
    assert {"httponly", "samesite=lax", f"max-age={auth.TTL_SECONDS}"} <= _attrs(renewed)
    assert "secure" not in _attrs(renewed)


def test_the_secure_flag_follows_tls_in_a_renewal_too():
    secure = session_renewal.cookie_header("v", object()).decode().lower()
    plain = session_renewal.cookie_header("v", None).decode().lower()
    assert "secure" in {p.strip() for p in secure.split(";")}
    assert "secure" not in {p.strip() for p in plain.split(";")}


def test_a_fresh_cookie_is_not_renewed(server, skew):
    client, _ = _client(server)
    skew["s"] = ONE_HOUR
    r = client.get("/api/console/me")
    assert r.status_code == 200
    assert _session_cookies(r) == []


def test_the_renewed_cookie_keeps_the_session_alive_past_the_old_expiry(server, skew):
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    assert client.get("/api/console/me").status_code == 200       # מתחדשת
    skew["s"] = 13 * 3600 + GRACE_PLUS                            # אחרי התפוגה המקורית
    assert _expiry(old) < time.time() + skew["s"]
    assert client.get("/api/console/me").status_code == 200       # החדשה תקפה



def test_a_second_request_in_flight_with_the_old_cookie_is_not_kicked(server, skew):
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    assert _session_cookies(client.get("/api/console/me"))
    straggler = TestClient(server["app"], cookies={auth.COOKIE_NAME: old})
    r = straggler.get("/api/console/me")
    assert r.status_code == 200, r.text
    assert _session_cookies(r) == []      # וגם לא מתחדשת פעם שנייה


def test_the_old_cookie_dies_after_the_grace(server, skew):
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    assert _session_cookies(client.get("/api/console/me"))
    skew["s"] += GRACE_PLUS
    assert auth.check(server["ctx"].conn, old) is None
    assert client.get("/api/console/me").status_code == 200       # החדשה עדיין תקפה


def test_whos_connected_shows_one_session_not_two(server, skew):
    client, old = _client(server)
    assert client.get("/api/console/me").status_code == 200       # שורה לישנה
    since = [r for r in presence.active(server["ctx"].conn, old) if r["current"]][0]["since"]
    skew["s"] = SEVEN_HOURS
    new = _value(_session_cookies(client.get("/api/console/me"))[0])
    rows = presence.active(server["ctx"].conn, new)
    mine = [r for r in rows if r["current"]]
    assert len(mine) == 1 and mine[0]["since"] == since
    assert not [r for r in rows if r["sid"] == presence._hash(old)[:presence.SID_LEN]]


def test_a_monitor_bridge_follows_the_renewal_and_dies_with_the_new_cookie(server, skew):
    import asyncio
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    loop = asyncio.new_event_loop()
    try:
        async def scenario():
            task = asyncio.current_task()
            viewer_sessions.register(old, "noc", task)
            new = _value(_session_cookies(client.get("/api/console/me"))[0])
            assert viewer_sessions.open_by_hash() == {presence._hash(new): 1}
            assert viewer_sessions.revoke(new) is True       # logout של החדשה סוגר אותו
            assert viewer_sessions.was_revoked(task)
        loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_a_websocket_works_with_the_renewed_cookie(server, skew):
    from starlette.websockets import WebSocketDisconnect
    client, _ = _client(server)
    skew["s"] = SEVEN_HOURS
    new = _value(_session_cookies(client.get("/api/console/me"))[0])
    ws = TestClient(server["app"], cookies={auth.COOKIE_NAME: new})
    with ws.websocket_connect("/api/console/monitor/aa:bb:cc:dd:ee:01") as sock:
        with pytest.raises(WebSocketDisconnect) as caught:
            sock.receive_bytes()
    assert caught.value.code != 4401


def test_a_password_change_keeps_its_own_cookie(server, skew):
    """ה-endpoint מנפיק עוגייה בעצמו (epoch חדש) -- החידוש לא דורס אותה בישנה."""
    _eve(server)
    client, _ = _client(server, "eve")
    skew["s"] = SEVEN_HOURS
    r = client.post("/api/console/me/password",
                    json={"current_password": "Aa12345!pw", "new_password": "Bb12345!pw"})
    assert r.status_code == 200, r.text
    cookies = _session_cookies(r)
    assert len(cookies) == 1
    assert auth.read_session(server["ctx"].conn, _value(cookies[0])).username == "eve"


# ---------- ניתוק בחוסר פעילות נשאר כמו שהיה ----------


def test_an_idle_cookie_is_not_renewed_and_still_falls(server, skew, monkeypatch):
    client, _ = _client(server)
    assert client.get("/api/console/me").status_code == 200
    skew["s"] = SEVEN_HOURS
    later = time.time() + 600 + presence.TOUCH_SECONDS + 1
    monkeypatch.setattr(presence, "_now", lambda: later)
    r = client.get("/api/console/me")
    assert r.status_code == 401, r.text
    assert _session_cookies(r) == []


# ---------- מה שלא מתחדש ----------


def test_a_kiosk_cookie_is_not_renewed(server, skew):
    client, _ = _client(server, purpose=auth.PURPOSE_KIOSK)
    skew["s"] = SEVEN_HOURS
    r = client.get("/api/console/me")
    assert r.status_code == 200, r.text
    assert _session_cookies(r) == []


def test_a_password_change_cookie_is_not_renewed(server, skew):
    _eve(server)
    client, _ = _client(server, "eve", purpose=auth.PURPOSE_PWCHANGE)
    skew["s"] = SEVEN_HOURS
    r = client.post("/api/console/me/password",
                    json={"current_password": "wrong", "new_password": "Bb12345!pw"})
    assert r.status_code == 400, r.text      # עבר את השער, ונדחה בשל הסיסמה
    assert _session_cookies(r) == []


def test_an_mfa_enrollment_cookie_is_not_renewed(server, skew):
    _eve(server)
    client, _ = _client(server, "eve", purpose=auth.PURPOSE_MFAENROLL)
    skew["s"] = SEVEN_HOURS
    r = client.post("/api/console/me/mfa/setup")
    assert r.status_code == 200, r.text
    assert _session_cookies(r) == []


def test_logout_is_not_renewed_and_revokes(server, skew):
    client, old = _client(server)
    skew["s"] = SEVEN_HOURS
    r = client.post("/api/console/logout")
    assert r.status_code == 200, r.text
    cookies = _session_cookies(r)
    assert len(cookies) == 1 and _value(cookies[0]) in ("", '""')    # מחיקה, לא חידוש
    assert auth.check(server["ctx"].conn, old) is None


@pytest.mark.parametrize("how", ["disabled", "deleted", "password_reset", "epoch_bumped"])
def test_a_cancelled_user_is_not_renewed(server, skew, how):
    """החלק הקריטי: חידוש אינו עוקף ביטול. הטוקן החדש נושא את ה-epoch של הישן."""
    _eve(server)
    conn = server["ctx"].conn
    client, old = _client(server, "eve")
    skew["s"] = SEVEN_HOURS
    if how == "disabled":
        users.set_disabled(conn, "eve", True, by="noc")
    elif how == "deleted":
        users.delete(conn, "eve", by="noc")
    elif how == "password_reset":
        users.reset_password(conn, "eve", by="noc")
    else:
        users.bump_auth_epoch(conn, "eve")
    r = client.get("/api/console/me")
    assert r.status_code == 401, r.text
    assert _session_cookies(r) == []


def test_a_revoked_cookie_is_not_renewed(server, skew):
    client, old = _client(server)
    assert client.get("/api/console/me").status_code == 200
    skew["s"] = SEVEN_HOURS
    auth.revoke(server["ctx"].conn, old)
    r = client.get("/api/console/me")
    assert r.status_code == 401, r.text
    assert _session_cookies(r) == []


def test_a_demoted_user_is_renewed_with_the_role_from_the_table(server, skew):
    _eve(server)
    client, _ = _client(server, "eve")
    skew["s"] = SEVEN_HOURS
    users.update(server["ctx"].conn, "eve", by="noc", role="deploy")
    r = client.get("/api/console/me")
    assert r.status_code == 403, r.text            # deploy אינו נכנס לקונסולה (#1073)
    assert _session_cookies(r) == []               # ושם לא מחדשים לו כלום


def test_a_cancellation_between_validation_and_delivery_is_not_bypassed(
        server, skew, monkeypatch):
    """שינוי סיסמה שקורה אחרי שהעוגייה אומתה ולפני שהחדשה יוצאת -- אין עוגייה חדשה."""
    _eve(server)
    conn = server["ctx"].conn
    client, old = _client(server, "eve")
    skew["s"] = SEVEN_HOURS
    real = session_renewal.propose

    def propose_then_cancel(request, c, session, token):
        real(request, c, session, token)
        users.bump_auth_epoch(conn, "eve")

    monkeypatch.setattr(session_renewal, "propose", propose_then_cancel)
    r = client.get("/api/console/me")
    assert _session_cookies(r) == []
    assert client.get("/api/console/me").status_code == 401
    assert auth.check(conn, old) is None


def test_the_agent_port_does_not_renew(server, skew):
    """לאפליקציית הסוכן אין middleware -- טוקן שלא היה נמסר היה מנתק את המפעיל."""
    from server.app import create_agent_app
    _, token = _client(server)
    skew["s"] = SEVEN_HOURS
    agent = TestClient(create_agent_app(server["app"].state.runtime),
                       cookies={auth.COOKIE_NAME: token})
    r = agent.get("/api/console/images")
    assert r.status_code == 200, r.text
    assert _session_cookies(r) == []
    assert auth.check(server["ctx"].conn, token) is not None     # והישנה לא בוטלה
