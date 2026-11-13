"""#1814 — קוד MFA נדחה כששעון השרת סוטה: ההודעה אומרת זאת, ולא רק "קוד שגוי".

TOTP מקבל ±30 שניות. שרת שהשעון שלו סוטה בדקות דוחה כל קוד נכון, והמנהל חושב
שהקוד שלו שגוי. כש-`timesync_status` (אותו hook של שורת "שעון" בבריאות) מראה
סטייה מ-30 ש' או "לא מסונכרן" — ההודעה מוסיפה "ייתכן שהשעון בשרת לא מדויק", ושורת
יומן `mfa_clock_skew` נרשמת. מדידה שנכשלה או שעון תקין — ההודעה נשארת כמו שהייתה.

מה שלא כאן: מסלול שחזור מה-DCUI (הכרעת נדב).

בקרה שלילית: `server/console/mfa.py`, `api.py` ו-`app.py` מ-origin/round/1106d.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.app import create_app, create_console_app
from server.console import totp

PW = "Aa12345!"
HINT = "ייתכן שהשעון בשרת לא מדויק"


@pytest.fixture()
def srv(tmp_path, images_root, clock, monkeypatch):
    from server.console import users
    from server.rounds import sender as sender_module
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    clock_state = {"value": {"synchronized": True, "offset_ms": 3.0}}

    def timesync_status():
        value = clock_state["value"]
        if isinstance(value, Exception):
            raise value
        return value

    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
        health_hooks={"interfaces": lambda: [], "timesync_status": timesync_status},
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    yield {"app": app, "ctx": ctx, "admin": admin, "clock": clock_state}
    ctx.sender.stop()


def _client(srv):
    return TestClient(create_console_app(srv["app"].state.runtime))


def _new_admin(srv, name):
    assert srv["admin"].post("/api/console/users", json={
        "username": name, "password": PW, "role": "admin"}).status_code == 200
    c = _client(srv)
    r = c.post("/api/console/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _login_challenge(srv, name):
    """משתמש עם MFA פעיל (שעון תקין בזמן ההרשמה), ו-challenge חדש לכניסה."""
    c = _client(srv)
    assert c.post("/api/console/login", json={"username": name, "password": PW}).status_code == 200
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    srv["ctx"].conn.execute("UPDATE users SET mfa_last_step = NULL WHERE username = ?", (name,))
    srv["ctx"].conn.commit()
    r = _client(srv).post("/api/console/login", json={"username": name, "password": PW})
    assert r.json()["mfa_required"] is True, r.text
    return r.json()["challenge"]


def _skew_rows(srv):
    return [r["detail"] for r in srv["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'mfa_clock_skew'")]


def _login_wrong(srv, name):
    challenge = _login_challenge(srv, name)
    return _client(srv).post("/api/console/login/mfa", json={"challenge": challenge, "code": "000000"})


def test_login_wrong_code_names_the_clock_when_it_is_minutes_off(srv):
    srv["admin"].post("/api/console/users", json={"username": "m1", "password": PW, "role": "admin"})
    srv["clock"]["value"] = {"synchronized": True, "offset_ms": 190_000.0}
    r = _login_wrong(srv, "m1")
    assert r.status_code == 401
    assert "קוד שגוי" in r.text and HINT in r.text, r.text
    rows = _skew_rows(srv)
    assert len(rows) == 1 and "off by 190s" in rows[0], rows


def test_login_wrong_code_names_an_unsynchronized_clock(srv):
    srv["admin"].post("/api/console/users", json={"username": "m2", "password": PW, "role": "admin"})
    srv["clock"]["value"] = {"synchronized": False, "offset_ms": None}
    r = _login_wrong(srv, "m2")
    assert HINT in r.text, r.text
    assert "not synchronized" in _skew_rows(srv)[0]


@pytest.mark.parametrize("value", [
    {"synchronized": True, "offset_ms": 3.0},          # שעון תקין
    {"synchronized": True, "offset_ms": 29_000.0},     # בתוך החלון
    {"synchronized": None, "offset_ms": None},         # לא נמדד
    RuntimeError("chronyc"),                           # המדידה עצמה נכשלה
])
def test_a_healthy_or_unmeasured_clock_leaves_the_message_as_it_was(srv, value):
    """לא מאשימים שעון בלי ראיה: מנהל לא יחפש תקלה שאולי אינה שם."""
    srv["admin"].post("/api/console/users", json={"username": "m3", "password": PW, "role": "admin"})
    srv["clock"]["value"] = value
    r = _login_wrong(srv, "m3")
    assert r.status_code == 401
    assert "קוד שגוי — היכנס מחדש" in r.text
    assert HINT not in r.text
    assert _skew_rows(srv) == []


def test_setup_verify_and_enable_name_the_clock_too(srv):
    c = _new_admin(srv, "m4")
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    good = totp.totp_code(secret)
    srv["clock"]["value"] = {"synchronized": True, "offset_ms": -600_000.0}
    v = c.post("/api/console/me/mfa/verify", json={"code": _wrong(good)})
    assert v.status_code == 401 and HINT in v.text, v.text
    e = c.post("/api/console/me/mfa/enable", json={"code": _wrong(good)})
    assert e.status_code == 401 and HINT in e.text, e.text
    assert len(_skew_rows(srv)) == 2


def test_setup_verify_with_a_healthy_clock_is_unchanged(srv):
    c = _new_admin(srv, "m5")
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    v = c.post("/api/console/me/mfa/verify", json={"code": _wrong(totp.totp_code(secret))})
    assert v.status_code == 401 and HINT not in v.text
    assert "קוד שגוי, נסה שוב" in v.text


def test_the_journal_line_reads_in_hebrew():
    from server.console.journal_he_detail import he_detail
    assert he_detail("mfa_clock_skew", "code rejected (login) while the server clock is off by 190s") \
        == "קוד נדחה בכניסה בזמן שהשעון בשרת סוטה ב-190 שניות"
    assert he_detail("mfa_clock_skew", "code rejected (enable) while the server clock is not synchronized") \
        == "קוד נדחה בהפעלת MFA בזמן שהשעון בשרת אינו מסונכרן"
    assert he_detail("mfa_clock_skew", "code rejected (other) while the server clock is off by 5s") \
        == "קוד נדחה (other) בזמן שהשעון בשרת סוטה ב-5 שניות"
