"""#1085 — כניסה: admin/admin + כפייה, נוהל סיסמאות, MFA, הגבלת ניסיונות."""

from __future__ import annotations

import hashlib
import sys
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server import totp, users
from server.app import create_console_app, create_runtime


@pytest.fixture(autouse=True)
def _mfa_required_for_admins(monkeypatch):
    """נדב 18/10: החיוב כבוי כברירת מחדל (‏test_mfa_required_setting.py בודק את שני
    המצבים). הקובץ הזה בודק את זרימת ה-MFA עצמה — הרשמה כפויה, קוד, קודי גיבוי —
    ולכן הוא רץ עם החיוב **דלוק**, כמו שרת שבו המנהל הדליק אותו."""
    from server import db
    monkeypatch.setitem(db.DEFAULT_SETTINGS, "mfa_required_for_admins", "true")


def _fresh(tmp_path, images_root, clock, monkeypatch):
    """אפליקציית הקונסולה (:8081) — MFA והחלפה כפויה. הקיוסק נבדק בנפרד.

    ‏`create_app` המשולב שם את הקיוסק ראשון (#1073), ולכן login שם מדלג
    על MFA. בייצור הם על שני פורטים.
    """
    from server import sender as sender_module
    from test_sender import Recorder
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    rt = create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
    )
    return create_console_app(rt)


def _console_client(server):
    return TestClient(create_console_app(server["app"].state.runtime))


def test_fresh_admin_admin_is_forced_to_change_password(
        tmp_path, images_root, clock, monkeypatch):
    app = _fresh(tmp_path, images_root, clock, monkeypatch)
    c = TestClient(app)
    r = c.post("/api/console/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert r.json() == {"must_change_password": True}
    assert c.get("/api/console/me").status_code == 403
    assert c.get("/api/console/me").json()["detail"] == "password_change_required"
    assert c.get("/api/console/users").status_code == 403
    r = c.post("/api/console/me/password", json={
        "current_password": "admin", "new_password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    me = c.get("/api/console/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["is_builtin"] is True
    assert me.json()["mfa_enabled"] is False
    assert me.json()["must_change_password"] is False


@pytest.mark.parametrize("password, snippet", [
    ("12345678", "אותיות"),
    ("abcdefgh", "מספרים"),
    ("abcd1234", "תו מיוחד"),
    ("short", "8 תווים"),
])
def test_password_policy_four_rules(server, password, snippet):
    r = server["admin"].post("/api/console/users", json={
        "username": "pwtest", "password": password, "role": "deploy",
    })
    assert r.status_code == 400
    assert snippet in r.json()["detail"]


def test_aa12345_bang_is_accepted(server):
    assert server["admin"].post("/api/console/users", json={
        "username": "nadavok", "password": "Aa12345!", "role": "deploy",
    }).status_code == 200


def test_duplicate_username_is_409_case_insensitive(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "DupUser", "password": "Aa12345!", "role": "deploy",
    }).status_code == 200
    r = admin.post("/api/console/users", json={
        "username": "dupuser", "password": "Aa12345!", "role": "deploy",
    })
    assert r.status_code == 409


def test_new_admin_must_enroll_mfa(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "nadav", "password": "Aa12345!", "role": "admin",
    }).status_code == 200
    c = _console_client(server)
    r = c.post("/api/console/login", json={"username": "nadav", "password": "Aa12345!"})
    assert r.status_code == 200
    assert r.json() == {"mfa_enrollment_required": True}
    assert c.get("/api/console/users").status_code == 403
    assert c.get("/api/console/users").json()["detail"] == "mfa_enrollment_required"
    setup = c.post("/api/console/me/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_url"].startswith("otpauth://totp/ImageSrv:")
    code = totp.totp_code(secret)
    assert c.post("/api/console/me/mfa/verify", json={"code": code}).status_code == 200
    en = c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)})
    assert en.status_code == 200, en.text
    assert en.json()["mfa_enabled"] is True
    assert len(en.json()["backup_codes"]) == 8
    assert all(len(x) == 10 for x in en.json()["backup_codes"])
    assert c.get("/api/console/me").status_code == 200
    assert c.get("/api/console/me").json()["mfa_enabled"] is True


# --- #1150: מסך ההגדרה בלי QR — השרת מחזיר `svg` ----------------------------


def _setup_as_new_admin(server, name: str):
    """admin חדש (לא builtin) → כניסה → `/me/mfa/setup`. מחזיר את גוף התשובה."""
    assert server["admin"].post("/api/console/users", json={
        "username": name, "password": "Aa12345!", "role": "admin",
    }).status_code == 200
    c = _console_client(server)
    r = c.post("/api/console/login", json={"username": name, "password": "Aa12345!"})
    assert r.json() == {"mfa_enrollment_required": True}
    setup = c.post("/api/console/me/mfa/setup")
    assert setup.status_code == 200, setup.text
    return setup.json()


def test_mfa_setup_refresh_keeps_the_pending_secret(server):
    """‏#2101: רענון מסך ההגדרה אחרי שהטלפון סרק את ה-QR קורא שוב ל-setup. כל עוד
    לא אושר — אותו סוד ממתין, והקוד מהאפליקציה (שסרקה את הראשון) מפעיל MFA."""
    assert server["admin"].post("/api/console/users", json={
        "username": "refresh", "password": "Aa12345!", "role": "admin",
    }).status_code == 200
    c = _console_client(server)
    assert c.post("/api/console/login", json={
        "username": "refresh", "password": "Aa12345!"}).json() == {"mfa_enrollment_required": True}
    first = c.post("/api/console/me/mfa/setup").json()
    second = c.post("/api/console/me/mfa/setup").json()
    assert second["secret"] == first["secret"]
    assert second["otpauth_url"] == first["otpauth_url"]
    en = c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(first["secret"])})
    assert en.status_code == 200, en.text
    assert en.json()["mfa_enabled"] is True


def test_mfa_pending_secret_expires(server, monkeypatch):
    """‏#2101: הסוד הממתין מוגבל בזמן — אחרי `PENDING_TTL` בלי אישור, setup מגריל חדש."""
    from server import console_mfa
    first = _setup_as_new_admin(server, "stale")
    later = time.time() + console_mfa.PENDING_TTL + 1
    monkeypatch.setattr(console_mfa, "_now", lambda: later)
    c = _console_client(server)
    c.post("/api/console/login", json={"username": "stale", "password": "Aa12345!"})
    again = c.post("/api/console/me/mfa/setup").json()
    assert again["secret"] != first["secret"]


def _journal_details(server, event: str) -> list[str]:
    rows = server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,)
    ).fetchall()
    return [r["detail"] for r in rows]


def test_mfa_setup_returns_inline_svg_qr(server):
    """‏#1150: ‏`svg` הוא QR של `otpauth_url` — inline (בלי `<?xml`/DOCTYPE),
    כי `console.js` מכניס אותו ישירות ל-`innerHTML` של `.qr`."""
    import qrcode  # noqa: F401 — תלות ייצור (python3-qrcode); חסרה = כישלון, לא דילוג
    body = _setup_as_new_admin(server, "qr")
    svg = body["svg"]
    assert svg.startswith("<svg")
    assert "<path" in svg
    assert "<?xml" not in svg and "DOCTYPE" not in svg
    assert body["otpauth_url"].startswith("otpauth://totp/ImageSrv:qr?")


def test_mfa_setup_svg_encodes_the_otpauth_url(server):
    """התוכן המקודד הוא `otpauth_url` עצמו — לא הסוד לבדו ולא URL אחר.
    ‏`qrcode` אינו מפענח, ולכן משווים למה שאותה ספרייה מייצרת מאותו קלט:
    הפלט דטרמיניסטי (אין אקראיות ב-QR), ו-URL אחר נותן path אחר."""
    import qrcode
    import qrcode.image.svg as qsvg
    body = _setup_as_new_admin(server, "qr2")
    expected = qrcode.make(body["otpauth_url"], image_factory=qsvg.SvgPathImage)
    assert body["svg"] == expected.to_string(encoding="unicode")
    other = qrcode.make(body["otpauth_url"] + "x", image_factory=qsvg.SvgPathImage)
    assert body["svg"] != other.to_string(encoding="unicode")


def test_mfa_setup_without_qrcode_package_is_named_not_silent(server, monkeypatch):
    """‏python3-qrcode חסר → ‏`svg == ""` ורשומת יומן שאומרת זאת בשם — לא
    קריסה של ההרשמה ולא "הצלחה" שקטה (עיקרון 5). הסוד וה-URL עדיין חוזרים."""
    monkeypatch.setitem(sys.modules, "qrcode", None)
    monkeypatch.setitem(sys.modules, "qrcode.image.svg", None)
    body = _setup_as_new_admin(server, "noqr")
    assert body["svg"] == ""
    assert body["secret"] and body["otpauth_url"].startswith("otpauth://")
    details = _journal_details(server, "mfa_setup")
    assert details and "python3-qrcode חסר בשרת" in details[-1], details


def test_two_step_login_and_wrong_code(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "mfa2", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "mfa2", "Aa12345!")
    c2 = _console_client(server)
    r = c2.post("/api/console/login", json={"username": "mfa2", "password": "Aa12345!"})
    assert r.json()["mfa_required"] is True
    challenge = r.json()["challenge"]
    assert c2.get("/api/console/me").status_code == 401
    bad = c2.post("/api/console/login/mfa", json={"challenge": challenge, "code": "000000"})
    assert bad.status_code == 401
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'mfa2'"
    ).fetchone()["mfa_secret"]
    # ‏#1120: קוד שגוי צרך את ה-challenge — כניסה מחדש מנפיקה חדש
    r = c2.post("/api/console/login", json={"username": "mfa2", "password": "Aa12345!"})
    challenge = r.json()["challenge"]
    ok = c2.post("/api/console/login/mfa", json={
        "challenge": challenge, "code": totp.totp_code(secret),
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["username"] == "mfa2"


def test_same_totp_code_twice_is_rejected(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "replay", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "replay", "Aa12345!")
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'replay'"
    ).fetchone()["mfa_secret"]
    code = totp.totp_code(secret)
    c2 = _console_client(server)
    ch1 = c2.post("/api/console/login", json={
        "username": "replay", "password": "Aa12345!",
    }).json()["challenge"]
    assert c2.post("/api/console/login/mfa", json={
        "challenge": ch1, "code": code,
    }).status_code == 200
    c3 = _console_client(server)
    ch2 = c3.post("/api/console/login", json={
        "username": "replay", "password": "Aa12345!",
    }).json()["challenge"]
    again = c3.post("/api/console/login/mfa", json={"challenge": ch2, "code": code})
    assert again.status_code == 401


def test_backup_code_is_single_use(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "bak", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    body = complete_console_login(c, server["ctx"].conn, "bak", "Aa12345!")
    codes = body["backup_codes"]
    c2 = _console_client(server)
    ch = c2.post("/api/console/login", json={
        "username": "bak", "password": "Aa12345!",
    }).json()["challenge"]
    assert c2.post("/api/console/login/mfa", json={
        "challenge": ch, "code": codes[0],
    }).status_code == 200
    c3 = _console_client(server)
    ch = c3.post("/api/console/login", json={
        "username": "bak", "password": "Aa12345!",
    }).json()["challenge"]
    assert c3.post("/api/console/login/mfa", json={
        "challenge": ch, "code": codes[0],
    }).status_code == 401


def test_old_remember_cookie_does_not_skip_mfa(server):
    _, secret = _mfa_user(server, "rem")
    raw = "former-remember-token"
    server["ctx"].conn.execute(
        "INSERT INTO trusted_browsers"
        " (token_hash, username, expires_at, created_at, ua) VALUES (?, ?, ?, ?, ?)",
        (hashlib.sha256(raw.encode()).hexdigest(), "rem", time.time() + 3600, "now", "test"),
    )
    server["ctx"].conn.commit()
    c = _console_client(server)
    c.cookies.set("imagectl_trusted", raw)
    login = c.post("/api/console/login", json={"username": "rem", "password": "Aa12345!"})
    assert login.status_code == 200
    assert login.json().get("mfa_required") is True
    assert login.json().get("challenge")
    verified = c.post("/api/console/login/mfa", json={
        "challenge": login.json()["challenge"],
        "code": totp.totp_code(secret),
        "remember_browser": True,
    })
    assert verified.status_code == 200
    assert "imagectl_trusted" not in verified.cookies


def test_builtin_cannot_setup_mfa(tmp_path, images_root, clock, monkeypatch):
    app = _fresh(tmp_path, images_root, clock, monkeypatch)
    c = TestClient(app)
    c.post("/api/console/login", json={"username": "admin", "password": "admin"})
    c.post("/api/console/me/password", json={
        "current_password": "admin", "new_password": "Aa12345!",
    })
    r = c.post("/api/console/me/mfa/setup")
    assert r.status_code == 403
    assert r.json()["detail"] == "builtin_no_mfa"


def test_admin_disable_mfa_forces_reenrollment(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "off", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "off", "Aa12345!")
    assert admin.post("/api/console/users/off/mfa/disable").status_code == 200
    c2 = _console_client(server)
    r = c2.post("/api/console/login", json={"username": "off", "password": "Aa12345!"})
    assert r.json().get("mfa_enrollment_required") is True


def test_five_failures_delay_and_ten_lock(server, monkeypatch):
    from server import login_guard
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    anon = server["anon"]
    body = {"username": "labtech", "password": "wrong"}
    for _ in range(5):
        assert anon.post("/api/console/login", json=body).status_code == 401
    delayed = anon.post("/api/console/login", json=body)
    assert delayed.status_code == 429
    row = server["ctx"].conn.execute("SELECT key, failures FROM login_attempts").fetchone()
    assert row["failures"] == 5
    server["ctx"].conn.execute(
        "UPDATE login_attempts SET failures = 9, last_at = ?", (t["now"] - 100,),
    )
    server["ctx"].conn.commit()
    t["now"] += 1
    assert anon.post("/api/console/login", json=body).status_code == 401
    locked = anon.post("/api/console/login", json=body)
    assert locked.status_code == 403
    assert "15 דקות" in locked.json()["detail"]
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "login_lockout" in events


def test_revoke_disconnects_a_live_session(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "cut", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "cut", "Aa12345!")
    assert c.get("/api/console/me").status_code == 200
    assert admin.post("/api/console/sessions/revoke",
                      json={"username": "cut"}).status_code == 200
    assert c.get("/api/console/me").status_code == 401


def test_kiosk_login_skips_mfa(server):
    from server.app import create_agent_app
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "kioskadm", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "kioskadm", "Aa12345!")
    kiosk = TestClient(create_agent_app(server["app"].state.runtime))
    r = kiosk.post("/api/console/login", json={
        "username": "kioskadm", "password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    assert r.json().get("username") == "kioskadm"
    assert "mfa_required" not in r.json()


def test_agent_login_without_mfa_and_must_change(server):
    from conftest import setup_classroom
    ids = setup_classroom(server)
    mac = ids["mac1"]
    # deploy — בלי MFA
    r = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "deploy-pass-1", "mac": mac,
    })
    # identity gate may refuse from TestClient without lease; skip if so
    if r.status_code == 403 and r.json().get("code", "").startswith("identity"):
        pytest.skip("שומר הזהות דולק בפיקסטורה הזו")
    assert r.status_code == 200, r.text
    users.change_own_password(server["ctx"].conn, "labtech",
                              "deploy-pass-1", "Aa12345!")
    # force must_change
    server["ctx"].conn.execute(
        "UPDATE users SET must_change_password = 1 WHERE username = 'labtech'")
    server["ctx"].conn.commit()
    r = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "Aa12345!", "mac": mac,
    })
    if r.status_code == 403 and r.json().get("code", "").startswith("identity"):
        pytest.skip("שומר הזהות דולק בפיקסטורה הזו")
    assert r.status_code == 403
    assert "החלף סיסמה בקונסולה קודם" in r.json()["error"]


def test_ensure_admin_creates_forced_builtin_and_does_not_touch_existing(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "a.db")
    assert users.ensure_admin(conn) == "admin"
    info = users.flags(conn, "admin")
    assert info["is_builtin"] is True
    assert info["must_change_password"] is True
    assert users.verify(conn, "admin", "admin") == "admin"
    assert users.ensure_admin(conn) is None
    users.change_own_password(conn, "admin", "admin", "Aa12345!")
    assert users.ensure_admin(conn) is None
    assert users.verify(conn, "admin", "Aa12345!") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is False

    conn2 = connect(tmp_path / "b.db")
    users.create(conn2, "nadav", "Aa12345!", "admin", by="t")
    assert users.ensure_admin(conn2) is None
    assert users.lookup(conn2, "admin") is None
    assert users.ensure_admin(conn2, password="Custom1!") is None
    assert users.lookup(conn2, "admin") is None


def test_ensure_admin_with_password_skips_force(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "c.db")
    assert users.ensure_admin(conn, password="Custom1!") is None
    info = users.flags(conn, "admin")
    assert info["is_builtin"] is True
    assert info["must_change_password"] is False
    assert users.verify(conn, "admin", "Custom1!") == "admin"


def test_reset_password_forces_change(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "rst", "password": "Aa12345!", "role": "admin",
    })
    r = admin.post("/api/console/users/rst/reset-password")
    assert r.status_code == 200
    new = r.json()["new_password"]
    c = _console_client(server)
    body = c.post("/api/console/login", json={"username": "rst", "password": new}).json()
    assert body.get("must_change_password") is True


def test_deploy_with_mfa_still_refused_on_console_before_mfa(server):
    """#1073 לפני #1085: deploy עם MFA מופעל מקבל deploy_no_console, לא mfa_required."""
    from server import auth
    conn = server["ctx"].conn
    conn.execute(
        "UPDATE users SET mfa_enabled = 1, mfa_secret = 'MFRGGZDFMY' "
        "WHERE username = 'labtech'"
    )
    conn.commit()
    c = _console_client(server)
    r = c.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1",
    })
    assert r.status_code == 403, r.text
    assert r.json() == {"error": "deploy_no_console",
                        "message_he": auth.DEPLOY_NO_CONSOLE_HE}
    assert "mfa_required" not in r.json()
    assert auth.COOKIE_NAME not in r.cookies


def test_password_change_session_is_not_blocked_by_console_only(server):
    """#1085 + #1073: session מוגבלת להחלפת סיסמה עוברת את console_only."""
    conn = server["ctx"].conn
    users.create(conn, "pwadmin", "Aa12345!", "admin", by="test",
                 is_builtin=True, must_change_password=True)
    c = _console_client(server)
    r = c.post("/api/console/login", json={
        "username": "pwadmin", "password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"must_change_password": True}
    assert c.get("/api/console/me").status_code == 403
    assert c.get("/api/console/me").json()["detail"] == "password_change_required"
    changed = c.post("/api/console/me/password", json={
        "current_password": "Aa12345!", "new_password": "Bb12345!",
    })
    assert changed.status_code == 200, changed.text
    assert c.get("/api/console/me").status_code == 200
    assert c.get("/api/console/me").json()["username"] == "pwadmin"


# ---------- #1120 — ארבע הקשחות אחרי סקירה R28 ----------

def _mfa_user(server, name):
    """יוצר admin עם MFA רשום ומחזיר את הסוד — מבנה משותף לטסטים של #1120."""
    server["admin"].post("/api/console/users", json={
        "username": name, "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, name, "Aa12345!")
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = ?", (name,),
    ).fetchone()["mfa_secret"]
    return c, secret


def _challenge(server, name):
    c = _console_client(server)
    r = c.post("/api/console/login", json={"username": name, "password": "Aa12345!"})
    return c, r


def test_1120_wrong_mfa_code_consumes_the_challenge(server):
    """קוד שגוי פוסל את ה-challenge — הקוד הנכון על אותו challenge נדחה."""
    _, secret = _mfa_user(server, "burn")
    c, r = _challenge(server, "burn")
    challenge = r.json()["challenge"]
    bad = c.post("/api/console/login/mfa", json={"challenge": challenge, "code": "000000"})
    assert bad.status_code == 401
    assert "היכנס מחדש" in bad.json()["detail"]
    again = c.post("/api/console/login/mfa", json={
        "challenge": challenge, "code": totp.totp_code(secret),
    })
    assert again.status_code == 401, again.text
    assert "imagectl_session" not in again.cookies
    # אחרי כניסה מחדש — challenge חדש עובד
    c2, r2 = _challenge(server, "burn")
    ok = c2.post("/api/console/login/mfa", json={
        "challenge": r2.json()["challenge"], "code": totp.totp_code(secret),
    })
    assert ok.status_code == 200, ok.text


def test_1120_ten_mfa_failures_lock_the_account(server, monkeypatch):
    """10 קודים שגויים ב-/login/mfa → נעילה 15 דקות, גם על /login."""
    from server import login_guard
    t = {"now": 2_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    _mfa_user(server, "brute")
    for n in range(1, 11):
        t["now"] += 600          # מעבר להשהיה האקספוננציאלית — רק הספירה נבדקת
        c, r = _challenge(server, "brute")
        assert r.status_code == 200, (n, r.text)
        bad = c.post("/api/console/login/mfa", json={
            "challenge": r.json()["challenge"], "code": "000000",
        })
        assert bad.status_code == 401, (n, bad.text)
    row = server["ctx"].conn.execute(
        "SELECT failures, locked_until FROM login_attempts WHERE key LIKE 'brute|%'"
    ).fetchone()
    assert row is not None, "אף כשל ב-/login/mfa לא נספר"
    assert row["failures"] == 10
    assert row["locked_until"] > t["now"]
    t["now"] += 1
    locked = _challenge(server, "brute")[1]
    assert locked.status_code == 403
    assert "15 דקות" in locked.json()["detail"]
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "login_lockout" in events


def test_1120_five_mfa_failures_delay_and_success_clears(server, monkeypatch):
    """5 כשלים → 429 עם Retry-After ב-/login/mfa; TOTP נכון מאפס את המונה."""
    from server import login_guard
    t = {"now": 3_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    _, secret = _mfa_user(server, "slow")
    for _ in range(5):
        t["now"] += 600
        c, r = _challenge(server, "slow")
        assert c.post("/api/console/login/mfa", json={
            "challenge": r.json()["challenge"], "code": "000000",
        }).status_code == 401
    # challenge שהונפק לפני הכשל החמישי — עדיין תקף, אבל המפתח בהשהיה
    c, r = _challenge(server, "slow")   # /login עצמו: failures=5, ready_at=last_at+1
    assert r.status_code == 429
    t["now"] += 600
    c, r = _challenge(server, "slow")
    assert r.status_code == 200, r.text
    ok = c.post("/api/console/login/mfa", json={
        "challenge": r.json()["challenge"], "code": totp.totp_code(secret),
    })
    assert ok.status_code == 200, ok.text
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts WHERE key LIKE 'slow|%'"
    ).fetchone()["n"] == 0


def test_1120_password_change_revokes_old_session(server):
    """החלפה עצמית: ה-session הישן מתבטל; המחליף ממשיך."""
    _, secret = _mfa_user(server, "chg")
    old, r = _challenge(server, "chg")
    assert old.post("/api/console/login/mfa", json={
        "challenge": r.json()["challenge"], "code": totp.totp_code(secret),
    }).status_code == 200
    assert old.get("/api/console/me").status_code == 200
    # session שנייה של אותו משתמש מחליפה סיסמה
    server["ctx"].conn.execute("UPDATE users SET mfa_last_step = NULL WHERE username = 'chg'")
    server["ctx"].conn.commit()
    me, r = _challenge(server, "chg")
    assert me.post("/api/console/login/mfa", json={
        "challenge": r.json()["challenge"], "code": totp.totp_code(secret),
    }).status_code == 200
    changed = me.post("/api/console/me/password", json={
        "current_password": "Aa12345!", "new_password": "Bb12345!",
    })
    assert changed.status_code == 200, changed.text
    assert me.get("/api/console/me").status_code == 200      # המחליף לא נותק
    assert old.get("/api/console/me").status_code == 401     # הישן — כן


def test_1120_admin_reset_revokes_sessions(server):
    _, secret = _mfa_user(server, "rsv")
    live, r = _challenge(server, "rsv")
    assert live.post("/api/console/login/mfa", json={
        "challenge": r.json()["challenge"], "code": totp.totp_code(secret),
    }).status_code == 200
    assert live.get("/api/console/me").status_code == 200
    assert server["admin"].post("/api/console/users/rsv/reset-password").status_code == 200
    assert live.get("/api/console/me").status_code == 401


def test_1120_password_longer_than_128_is_rejected_by_name(server):
    r = server["admin"].post("/api/console/users", json={
        "username": "longpw", "password": "Aa1!" + "x" * 125, "role": "deploy",
    })
    assert r.status_code == 400
    assert "128" in r.json()["detail"]
    assert server["admin"].post("/api/console/users", json={
        "username": "longpw", "password": "Aa1!" + "x" * 124, "role": "deploy",
    }).status_code == 200


def test_1120_a_challenge_issued_before_the_lock_dies_with_the_lock(server, monkeypatch):
    """#1120: ‏`/login/mfa` בודק את הנעילה בעצמו (`login_guard.check`), ולא
    סומך על כך ש-`/login` כבר בדק. challenge שהונפק **לפני** הנעילה עדיין
    תקף בזמן — ובלי הבדיקה, מי שמחזיק אותו נכנס לחשבון נעול עם קוד נכון.
    עד כאן אף טסט לא הפיל את הסרת הבדיקה (בקרה רטרואקטיבית, 24/10): שאר
    טסטי #1120 עוברים דרך `/login`, שנועל לפני שה-challenge נוצר."""
    from server import login_guard
    t = {"now": 4_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    _, secret = _mfa_user(server, "early")
    holder, r = _challenge(server, "early")
    assert r.status_code == 200, r.text
    challenge = r.json()["challenge"]
    for n in range(10):                       # ננעל מאותה כתובת, בסיסמה שגויה
        t["now"] += 600                       # מעבר להשהיה — רק הספירה נבדקת
        bad = _console_client(server).post("/api/console/login", json={
            "username": "early", "password": "Wrong123!"})
        assert bad.status_code == 401, (n, bad.text)
    t["now"] += 1
    late = holder.post("/api/console/login/mfa", json={
        "challenge": challenge, "code": totp.totp_code(secret),
    })
    assert late.status_code == 403, late.text
    assert "15 דקות" in late.json()["detail"]
    assert "imagectl_session" not in late.cookies


def test_1243_idle_default_is_600_on_every_login_path(server):
    """#1243: ברירת המחדל של הניתוק בחוסר פעילות היא 600 שניות — ב-DB
    טרי, וגם בגיבוי (`or …`) של שלושת המסלולים שמחזירים `idle_seconds`:
    ‏`/login` (console_login), ‏`/login/mfa` (console_mfa) ו-`/me`
    (console_api). שלושה עותקים של אותו מספר — הטסט תופס סחיפה של כל אחד."""
    from server.db import DEFAULT_SETTINGS, get_setting      # noqa: PLC0415
    conn = server["ctx"].conn
    assert DEFAULT_SETTINGS["console_idle_seconds"] == "600"
    assert get_setting(conn, "console_idle_seconds") == "600"
    _, secret = _mfa_user(server, "idle6")
    # בלי השורה — רק הגיבוי שבקוד קובע.
    conn.execute("DELETE FROM settings WHERE key = 'console_idle_seconds'")
    conn.commit()
    c, r = _challenge(server, "idle6")
    mfa = c.post("/api/console/login/mfa", json={
        "challenge": r.json()["challenge"], "code": totp.totp_code(secret),
    })
    assert mfa.status_code == 200, mfa.text
    assert mfa.json()["idle_seconds"] == 600
    assert c.get("/api/console/me").json()["idle_seconds"] == 600
    users.create(conn, "idlebuiltin", "x", "admin", by="test",
                 is_builtin=True, check_policy=False)
    plain = _console_client(server).post("/api/console/login", json={
        "username": "idlebuiltin", "password": "x",
    })
    assert plain.json()["idle_seconds"] == 600, plain.text
