"""נדב 18/10: "תכלס לא צריך לזה MFA. אם בנוי, אפשר להפעיל או לא".

עד rc6 כל מנהל שאינו המקומי (is_builtin) חויב לרשום MFA בכניסה ובהחלפת סיסמה.
עכשיו ‏`mfa_required_for_admins` (הגדרות ← אבטחה), כבוי כברירת מחדל:

* כבוי — אין `mfa_enrollment_required`, לא בכניסה ולא בהחלפת סיסמה; מי שהפעיל MFA
  ממשיך להתבקש לקוד; ב"החשבון שלי" אפשר להפעיל, ולבטל עם קוד (‏/me/mfa/disable).
* דלוק — בדיוק מה שהיה (‏test_login_mfa.py רץ כולו כך).
* שדרוג — שרת קיים עם מנהל כזה מקבל "false", ושורת יומן אומרת את זה.

בקרה שלילית: ‏server/console/console_login.py ו-server/console/console_api.py (התנאי הישן), ו-db.py
(בלי ברירת המחדל) מוחזרים יחד — הטסטים של "כבוי" נופלים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.console import console_mfa, totp
from server import db
from server.app import create_console_app


def _console(server) -> TestClient:
    return TestClient(create_console_app(server["app"].state.runtime))


def _new_admin(server, name: str) -> None:
    assert server["admin"].post("/api/console/users", json={
        "username": name, "password": "Aa12345!", "role": "admin",
    }).status_code == 200


def _switch(server, on: bool) -> None:
    r = server["admin"].post("/api/console/settings",
                             json={console_mfa.SETTING: "true" if on else "false"})
    assert r.status_code == 200, r.text


# --- כבוי (ברירת המחדל) ----------------------------------------------------------


def test_the_switch_is_off_by_default_and_is_a_setting(server):
    assert db.DEFAULT_SETTINGS[console_mfa.SETTING] == "false"
    assert server["admin"].get("/api/console/settings").json()[console_mfa.SETTING] == "false"


def test_off_a_new_admin_logs_in_without_enrolling(server):
    _new_admin(server, "dana")
    c = _console(server)
    r = c.post("/api/console/login", json={"username": "dana", "password": "Aa12345!"})
    assert r.status_code == 200, r.text
    assert "mfa_enrollment_required" not in r.json() and r.json()["username"] == "dana"
    assert c.get("/api/console/users").status_code == 200, "a full session"


def test_off_a_password_change_does_not_demand_enrollment(server):
    _new_admin(server, "eli")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "eli", "Aa12345!")
    r = c.post("/api/console/me/password", json={
        "current_password": "Aa12345!", "new_password": "Bb12345!"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert c.get("/api/console/users").status_code == 200


def test_off_an_admin_who_turned_mfa_on_is_still_asked_for_the_code(server):
    _new_admin(server, "gal")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "gal", "Aa12345!")
    setup = c.post("/api/console/me/mfa/setup").json()
    assert c.post("/api/console/me/mfa/enable",
                  json={"code": totp.totp_code(setup["secret"])}).status_code == 200
    again = _console(server).post("/api/console/login",
                                  json={"username": "gal", "password": "Aa12345!"})
    assert again.json().get("mfa_required") is True and again.json().get("challenge")


def test_off_mfa_can_be_turned_off_for_oneself_with_a_code_only(server):
    _new_admin(server, "hila")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "hila", "Aa12345!")
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    bad = c.post("/api/console/me/mfa/disable", json={"current_password": "Aa12345!", "code": "000000"})
    assert bad.status_code == 401 and c.get("/api/console/me").json()["mfa_enabled"] is True
    no_pw = c.post("/api/console/me/mfa/disable", json={"code": totp.totp_code(secret)})
    assert no_pw.status_code == 401, "the code alone is not enough -- the current password too"
    # the code that enabled it was already used once -- it is the next step's code that counts
    server["ctx"].conn.execute("UPDATE users SET mfa_last_step = NULL WHERE username = 'hila'")
    server["ctx"].conn.commit()
    ok = c.post("/api/console/me/mfa/disable",
                json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert ok.status_code == 200, ok.text
    assert c.get("/api/console/me").json()["mfa_enabled"] is False
    r = _console(server).post("/api/console/login", json={"username": "hila", "password": "Aa12345!"})
    assert r.json()["username"] == "hila", "the next login is the password alone"
    events = [row["event"] for row in server["ctx"].conn.execute("SELECT event FROM journal")]
    assert "mfa_disable_failed" in events and "mfa_disabled" in events


# --- דלוק: בדיוק מה שהיה -----------------------------------------------------------


def test_on_a_new_admin_must_enroll_and_cannot_turn_it_off_alone(server):
    _switch(server, True)
    _new_admin(server, "yoav")
    c = _console(server)
    r = c.post("/api/console/login", json={"username": "yoav", "password": "Aa12345!"})
    assert r.json() == {"mfa_enrollment_required": True}
    assert c.get("/api/console/users").json()["detail"] == "mfa_enrollment_required"
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    refused = c.post("/api/console/me/mfa/disable",
                     json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert refused.status_code == 409 and "הגדרות" in refused.json()["detail"]


def test_on_a_password_change_demands_enrollment_as_before(server):
    _new_admin(server, "rina")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "rina", "Aa12345!")   # off: a full session
    _switch(server, True)
    r = c.post("/api/console/me/password", json={
        "current_password": "Aa12345!", "new_password": "Bb12345!"})
    assert r.json() == {"ok": True, "mfa_enrollment_required": True}


def test_the_builtin_admin_and_deploy_users_are_never_asked(server):
    _switch(server, True)
    conn = server["ctx"].conn
    assert console_mfa.enrollment_required(conn, "admin", {"is_builtin": False, "mfa_enabled": False})
    assert not console_mfa.enrollment_required(conn, "admin", {"is_builtin": True, "mfa_enabled": False})
    assert not console_mfa.enrollment_required(conn, "deploy", {"is_builtin": False, "mfa_enabled": False})
    assert not console_mfa.enrollment_required(conn, "admin", {"is_builtin": False, "mfa_enabled": True})


# --- שדרוג ------------------------------------------------------------------------


def test_an_upgrade_turns_the_requirement_off_and_says_so(tmp_path):
    """שרת שהיה בו מנהל כזה ואין לו עדיין ההגדרה: אחרי העלייה "false", ושורת יומן."""
    path = tmp_path / "imagesrv.db"
    conn = db.connect(path)
    conn.execute("INSERT INTO users (username, pw_hash, role, created_at, is_builtin)"
                 " VALUES ('old', 'x', 'admin', '2026-09-01T00:00:00+00:00', 0)")
    conn.execute("DELETE FROM settings WHERE key = ?", (console_mfa.SETTING,))
    conn.commit()
    conn.close()
    again = db.connect(path)
    assert db.get_setting(again, console_mfa.SETTING) == "false"
    notes = [r["detail"] for r in again.execute(
        "SELECT detail FROM journal WHERE event = 'setting_change'")]
    assert notes == [db.MFA_UPGRADE_NOTE], notes
    again.close()
    third = db.connect(path)                     # once -- not on every start
    assert len(list(third.execute(
        "SELECT 1 FROM journal WHERE event = 'setting_change'"))) == 1
    third.close()


def test_a_fresh_install_writes_no_upgrade_line(tmp_path):
    conn = db.connect(tmp_path / "imagesrv.db")
    assert db.get_setting(conn, console_mfa.SETTING) == "false"
    assert list(conn.execute("SELECT 1 FROM journal WHERE event = 'setting_change'")) == []
    conn.close()


def test_guessing_the_code_on_a_stolen_session_is_limited_like_the_login(server, monkeypatch):
    """סקירת האבטחה על 7534e0e7: ‏/me/mfa/disable לא הגביל ניסיונות — session גנוב
    יכול לנחש קוד עד שיצליח. אותם ספים כמו ‏/login/mfa: אחרי 10 כשלונות נעילה, ובזמן
    הנעילה גם סיסמה וקוד נכונים נדחים; MFA נשאר מופעל."""
    from server.console import login_guard
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    _new_admin(server, "tal")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "tal", "Aa12345!")
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    server["ctx"].conn.execute("UPDATE users SET mfa_last_step = NULL WHERE username = 'tal'")
    server["ctx"].conn.commit()
    for i in range(login_guard.LOCK_AFTER):
        t["now"] += 3600                         # past every delay: only the count matters
        r = c.post("/api/console/me/mfa/disable",
                   json={"current_password": "Aa12345!", "code": f"{i:06d}"})
        assert r.status_code == 401, (i, r.text)
    t["now"] += 1
    locked = c.post("/api/console/me/mfa/disable",
                    json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert locked.status_code == 403 and "ננעל" in locked.json()["detail"], locked.text
    assert c.get("/api/console/me").json()["mfa_enabled"] is True
    # after the lock, the right password and code do it -- and clear the count
    t["now"] += login_guard.LOCK_SECONDS + 1
    ok = c.post("/api/console/me/mfa/disable",
                json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert ok.status_code == 200, ok.text


def test_after_five_failures_the_next_try_waits(server, monkeypatch):
    from server.console import login_guard
    t = {"now": 2_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    _new_admin(server, "noa")
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "noa", "Aa12345!")
    secret = c.post("/api/console/me/mfa/setup").json()["secret"]
    assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    for _ in range(login_guard.DELAY_AFTER):
        assert c.post("/api/console/me/mfa/disable",
                      json={"current_password": "wrong", "code": "000000"}).status_code == 401
    waited = c.post("/api/console/me/mfa/disable",
                    json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert waited.status_code == 429 and waited.headers.get("Retry-After"), waited.text


# --- נדב 18/10: "דרוש אימות דו-שלבי" למשתמש, ו"איפוס אימות דו-שלבי" למשתמש אחר ----------


def _create(server, name: str, *, required: bool) -> None:
    assert server["admin"].post("/api/console/users", json={
        "username": name, "password": "Aa12345!", "role": "admin", "mfa_required": required,
    }).status_code == 200


def _row(server, name: str) -> dict:
    return next(u for u in server["admin"].get("/api/console/users").json() if u["username"] == name)


def test_a_flagged_admin_must_enroll_even_with_the_switch_off(server):
    _create(server, "flag", required=True)
    assert _row(server, "flag")["mfa_required"] is True
    r = _console(server).post("/api/console/login", json={"username": "flag", "password": "Aa12345!"})
    assert r.json() == {"mfa_enrollment_required": True}, "the user's flag alone demands it"
    _create(server, "plain", required=False)
    r = _console(server).post("/api/console/login", json={"username": "plain", "password": "Aa12345!"})
    assert r.json()["username"] == "plain"


def test_the_flag_is_set_and_cleared_in_edit_and_journaled(server):
    _create(server, "edit", required=False)
    admin = server["admin"]
    assert admin.put("/api/console/users/edit", json={"mfa_required": True}).status_code == 200
    assert _row(server, "edit")["mfa_required"] is True
    assert admin.put("/api/console/users/edit", json={"mfa_required": "yes"}).status_code == 400
    assert admin.put("/api/console/users/edit", json={"mfa_required": False}).status_code == 200
    assert _row(server, "edit")["mfa_required"] is False
    details = [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'user_edit'")]
    assert "edit mfa_required=true" in details and "edit mfa_required=false" in details


def test_a_flagged_admin_cannot_turn_mfa_off_alone(server):
    _create(server, "lock", required=True)
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, "lock", "Aa12345!")   # enrolls (flag)
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'lock'").fetchone()["mfa_secret"]
    r = c.post("/api/console/me/mfa/disable",
               json={"current_password": "Aa12345!", "code": totp.totp_code(secret)})
    assert r.status_code == 409 and r.json()["detail"] == console_mfa.SELF_DISABLE_FLAGGED_HE


def _enrolled(server, name: str, *, required: bool) -> TestClient:
    _create(server, name, required=required)
    c = _console(server)
    complete_console_login(c, server["ctx"].conn, name, "Aa12345!")
    if not required:
        secret = c.post("/api/console/me/mfa/setup").json()["secret"]
        assert c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)}).status_code == 200
    assert _row(server, name)["mfa_enabled"] is True
    return c


@pytest.mark.parametrize("required", [False, True])
def test_an_admin_resets_another_users_mfa(server, required):
    """הסוד, ההפעלה וקודי הגיבוי נמחקים; ההפעלות שלו מנותקות; יומן `mfa_reset`; בכניסה
    הבאה — רישום אם האימות חובה לו, אחרת סיסמה בלבד."""
    name = f"reset{int(required)}"
    victim = _enrolled(server, name, required=required)
    assert victim.get("/api/console/me").status_code == 200
    conn = server["ctx"].conn
    assert conn.execute("SELECT COUNT(*) AS n FROM mfa_backup_codes WHERE username = ?",
                        (name,)).fetchone()["n"] > 0
    r = server["admin"].post(f"/api/console/users/{name}/mfa/disable")
    assert r.status_code == 200, r.text
    row = conn.execute("SELECT mfa_enabled, mfa_secret FROM users WHERE username = ?", (name,)).fetchone()
    assert (row["mfa_enabled"], row["mfa_secret"]) == (0, None)
    assert conn.execute("SELECT COUNT(*) AS n FROM mfa_backup_codes WHERE username = ?",
                        (name,)).fetchone()["n"] == 0
    assert victim.get("/api/console/me").status_code == 401, "his sessions are revoked"
    j = conn.execute("SELECT user, detail FROM journal WHERE event = 'mfa_reset'").fetchall()
    actor = server["admin"].get("/api/console/me").json()["username"]
    assert [(x["user"], x["detail"]) for x in j] == [(actor, name)], "who reset whom"
    nxt = _console(server).post("/api/console/login", json={"username": name, "password": "Aa12345!"})
    if required:
        assert nxt.json() == {"mfa_enrollment_required": True}
    else:
        assert nxt.json()["username"] == name


def test_resetting_your_own_mfa_here_is_refused_with_the_way_to_do_it(server):
    me = server["admin"].get("/api/console/me").json()["username"]
    r = server["admin"].post(f"/api/console/users/{me}/mfa/disable")
    assert r.status_code == 400 and r.json()["detail"] == console_mfa.RESET_SELF_HE
    assert "החשבון שלי" in r.json()["detail"]


def test_the_journal_says_who_reset_whom_in_hebrew(server):
    from server.console.journal_he import EVENTS_HE
    assert EVENTS_HE["mfa_reset"] == "אימות דו-שלבי אופס למשתמש"
