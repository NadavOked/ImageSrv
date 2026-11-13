"""#2470 — `/me/mfa/verify` ו-`/me/mfa/enable` מוגבלים, ו"קודי גיבוי חדשים" דורשים סיסמה.

עד כאן שני הנתיבים לא עברו ב-`login_guard`, ו-`enable` על MFA פעיל הנפיק קודי גיבוי
חדשים מקוד TOTP בלבד: מי שמחזיק עוגייה גנובה ניחש קוד בלי הגבלה, פסל את קודי הגיבוי
של הבעלים וקיבל חדשים. עכשיו — אותו מונה ואותם ספים כמו `/login/mfa`, וכש-MFA פעיל
`enable` דורש את הסיסמה הנוכחית וקוד שלא נוצל (כמו `/me/mfa/disable`).

בקרה שלילית: `server/console/mfa.py` מ-origin/main → נופלים כל הטסטים חוץ מהאחרון (זרימת ההרשמה, שלא השתנתה).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_console_app
from server.console import login_guard, totp

PW = "Aa12345!"


@pytest.fixture()
def clocks(monkeypatch):
    """שעון קפוא ל-TOTP ול-login_guard: ההשהיה אחרי 5 כשלונות לא חולפת מעצמה."""
    t = {"now": 1_700_000_000.0}
    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: t["now"]))
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    return t


def _admin(server, name):
    """מנהל חדש מחובר לקונסולה (החיוב למנהלים כבוי — ברירת המחדל), אחרי setup."""
    assert server["admin"].post("/api/console/users", json={
        "username": name, "password": PW, "role": "admin"}).status_code == 200
    c = TestClient(create_console_app(server["app"].state.runtime))
    r = c.post("/api/console/login", json={"username": name, "password": PW})
    assert r.status_code == 200 and r.json().get("username") == name, r.text
    setup = c.post("/api/console/me/mfa/setup")
    assert setup.status_code == 200, setup.text
    return c, setup.json()["secret"]


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _backup_hashes(server, name):
    return sorted(r["code_hash"] for r in server["ctx"].conn.execute(
        "SELECT code_hash FROM mfa_backup_codes WHERE username = ?", (name,)))


def test_verify_is_rate_limited_like_login_mfa(server, clocks):
    c, secret = _admin(server, "v2470")
    good = totp.totp_code(secret, when=clocks["now"])
    for _ in range(login_guard.DELAY_AFTER):
        assert c.post("/api/console/me/mfa/verify", json={"code": _wrong(good)}).status_code == 401
    r = c.post("/api/console/me/mfa/verify", json={"code": good})
    assert r.status_code == 429, r.text
    assert r.headers.get("Retry-After")


def test_enable_is_rate_limited_and_locks_after_ten(server, clocks):
    c, secret = _admin(server, "e2470")
    good = totp.totp_code(secret, when=clocks["now"])
    for n in range(login_guard.LOCK_AFTER):
        clocks["now"] += 3600   # ההשהיה חולפת — נשאר רק מניין הכשלונות
        good = totp.totp_code(secret, when=clocks["now"])
        assert c.post("/api/console/me/mfa/enable", json={"code": _wrong(good)}).status_code == 401, n
    r = c.post("/api/console/me/mfa/enable", json={"code": good})
    assert r.status_code == 403, r.text        # נעילה — גם קוד נכון נדחה
    assert server["ctx"].conn.execute(
        "SELECT mfa_enabled FROM users WHERE username = 'e2470'").fetchone()[0] == 0


def test_new_backup_codes_need_the_current_password(server, clocks):
    c, secret = _admin(server, "b2470")
    en = c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret, when=clocks["now"])})
    assert en.status_code == 200, en.text
    before = _backup_hashes(server, "b2470")
    clocks["now"] += 30                        # step חדש — הקוד של ההפעלה נצרך (#2414)
    code = totp.totp_code(secret, when=clocks["now"])
    # עוגייה + קוד בלבד (מה שהיה מספיק עד כאן) — סירוב, והקודים הקיימים נשארים.
    r = c.post("/api/console/me/mfa/enable", json={"code": code})
    assert r.status_code == 401, r.text
    assert "backup_codes" not in r.json()
    r = c.post("/api/console/me/mfa/enable", json={"current_password": "wrong", "code": code})
    assert r.status_code == 401, r.text
    assert _backup_hashes(server, "b2470") == before
    events = [row[0] for row in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE user = 'b2470'")]
    assert events.count("mfa_backup_codes_failed") == 2
    # סיסמה וקוד נכונים — קודים חדשים, והישנים נפסלו.
    r = c.post("/api/console/me/mfa/enable", json={"current_password": PW, "code": code})
    assert r.status_code == 200, r.text
    assert len(r.json()["backup_codes"]) == 8
    assert _backup_hashes(server, "b2470") != before


def test_new_backup_codes_refuse_a_spent_code(server, clocks):
    """הקוד שהפעיל את ה-MFA נצרך (#2414) — הוא אינו מנפיק קודי גיבוי באותו step."""
    c, secret = _admin(server, "r2470")
    code = totp.totp_code(secret, when=clocks["now"])
    assert c.post("/api/console/me/mfa/enable", json={"code": code}).status_code == 200
    r = c.post("/api/console/me/mfa/enable", json={"current_password": PW, "code": code})
    assert r.status_code == 401, r.text


def test_new_backup_codes_are_rate_limited(server, clocks):
    c, secret = _admin(server, "l2470")
    assert c.post("/api/console/me/mfa/enable",
                  json={"code": totp.totp_code(secret, when=clocks["now"])}).status_code == 200
    clocks["now"] += 30
    for _ in range(login_guard.DELAY_AFTER):
        assert c.post("/api/console/me/mfa/enable",
                      json={"current_password": "guess", "code": "000000"}).status_code == 401
    r = c.post("/api/console/me/mfa/enable", json={
        "current_password": PW, "code": totp.totp_code(secret, when=clocks["now"])})
    assert r.status_code == 429, r.text


def test_enrollment_still_works_and_success_clears_the_counter(server, clocks):
    """הזרימה של הקונסולה: verify ואז enable באותו קוד — בלי סיסמה, כמו קודם.
    טעות אחת לפני כן אינה נשארת במונה אחרי הפעלה מוצלחת."""
    c, secret = _admin(server, "ok2470")
    good = totp.totp_code(secret, when=clocks["now"])
    assert c.post("/api/console/me/mfa/verify", json={"code": _wrong(good)}).status_code == 401
    assert c.post("/api/console/me/mfa/verify", json={"code": good}).status_code == 200
    en = c.post("/api/console/me/mfa/enable", json={"code": good})
    assert en.status_code == 200, en.text
    assert en.json()["mfa_enabled"] is True
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE key LIKE 'ok2470|%'").fetchone()[0] == 0
