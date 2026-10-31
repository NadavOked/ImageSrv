"""ה-cookie של הקונסולה — מה שהחתימה מוכיחה, ומה שהיא לא.

שני הכשלים שהקובץ הזה שומר עליהם הם אותו כשל בשתי צורות: ערך שנקרא
ולא נבדק. ב-#90 זה הסוד עצמו (חסר → בייט `0x00` ידוע → כל אחד חותם
לעצמו admin), וב-#91 זה התפקיד (נלקח מהמטען, לא מהטבלה, ולכן מנהל
שהורד או נמחק נשאר מנהל 12 שעות).
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient       # noqa: E402


def forge(secret: bytes, username: str, role: str, seconds: int = 3600) -> str:
    """טוקן חתום בסוד נתון — מה שתוקף היה בונה."""
    payload = f"{username}|{role}|{int(time.time()) + seconds}"
    signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def loud_client(server) -> TestClient:
    """לקוח שמחזיר 500 במקום להעיף את החריגה — כדי לבדוק *שהבקשה
    נכשלה*, ולא רק שהיא לא הצליחה."""
    return TestClient(server["app"], raise_server_exceptions=False)


# --- #90: סוד חסר -----------------------------------------------------------

def drop_secret(server) -> None:
    from server.console import auth
    server["ctx"].conn.execute(
        "DELETE FROM settings WHERE key = ?", (auth.SECRET_KEY,)
    )
    server["ctx"].conn.commit()


def test_a_missing_secret_does_not_become_the_key_0x00(server):
    """הבקרה השלילית של #90.

    לפני התיקון `_secret` החזיר `bytes.fromhex("00")`, ולכן הטוקן
    שנבנה כאן — בלי לדעת שום סוד — התקבל כמנהל מלא.
    """
    drop_secret(server)
    anon = loud_client(server)
    anon.cookies.set("imagesrv_session", forge(b"\x00", "mallory", "admin"))
    assert anon.get("/api/console/users").status_code != 200


def test_a_missing_secret_fails_loudly_and_does_not_look_like_logged_out(server):
    """‏401 ("לא מחובר") היה קיפול של "לא הצלחנו לקרוא את הסוד" לתוך
    "אין כאן משתמש" — שני מצבים שונים, עיקרון 5."""
    drop_secret(server)
    admin = loud_client(server)
    admin.cookies.set("imagesrv_session", forge(b"\x00", "noc", "admin"))
    assert admin.get("/api/console/users").status_code == 500


def test_a_missing_secret_refuses_to_issue_a_new_cookie(server):
    drop_secret(server)
    r = loud_client(server).post(
        "/api/console/login", json={"username": "noc", "password": "admin-pass-123"}
    )
    assert r.status_code == 500
    assert "imagesrv_session" not in r.cookies


@pytest.mark.parametrize("value", ["", "   ", "zznothex", "00", "aabbccdd"])
def test_a_corrupt_or_short_secret_is_refused(server, value):
    """‏hex לא תקין, וסוד קצר מדי שהוא שריד ולא סוד."""
    from server.console import auth
    from server.db import set_setting
    set_setting(server["ctx"].conn, auth.SECRET_KEY, value)
    with pytest.raises(auth.SecretUnusable):
        auth.assert_secret(server["ctx"].conn)


def test_a_healthy_install_has_a_secret_that_passes_the_startup_check(server):
    from server.console import auth
    auth.assert_secret(server["ctx"].conn)          # ראיה חיובית, לא היעדר שגיאה


def test_the_seeded_secret_is_long_enough_to_be_one(server):
    from server.console import auth
    from server.db import get_setting
    raw = get_setting(server["ctx"].conn, auth.SECRET_KEY)
    assert len(bytes.fromhex(raw)) >= auth.MIN_SECRET_BYTES


# --- #91: התפקיד מהטבלה, לא מהטוקן ------------------------------------------

def test_a_demoted_admin_loses_the_admin_screens_at_once(server):
    """הבקרה השלילית של #91, הצורה הראשונה.

    לפני התיקון ה-cookie הישן נשא `role=admin` והמסכים נשארו פתוחים
    עד סוף ה-TTL — שתים-עשרה שעות אחרי שהתפקיד ירד.
    """
    from conftest import complete_console_login
    admin, second = server["admin"], TestClient(server["app"])
    assert admin.post(
        "/api/console/users",
        json={"username": "second", "password": "Second-pass-1!", "role": "admin"},
    ).status_code == 200
    complete_console_login(second, server["ctx"].conn, "second", "Second-pass-1!")
    assert second.get("/api/console/users").status_code == 200

    assert admin.put("/api/console/users/second",
                     json={"role": "deploy"}).status_code == 200
    assert second.get("/api/console/users").status_code == 403


def test_a_deleted_user_stops_being_anyone(server):
    """הצורה השנייה, החמורה: חשבון שאיננו — והטוקן שלו עדיין חתום."""
    admin, second = server["admin"], TestClient(server["app"])
    from conftest import complete_console_login
    admin.post("/api/console/users",
               json={"username": "second", "password": "Second-pass-1!", "role": "admin"})
    complete_console_login(second, server["ctx"].conn, "second", "Second-pass-1!")
    assert second.get("/api/console/me").status_code == 200

    assert admin.delete("/api/console/users/second").status_code == 200
    assert second.get("/api/console/me").status_code == 401


def test_a_promoted_user_gets_the_admin_screens_without_signing_in_again(server):
    """אותו תיקון בכיוון המתיר — התפקיד הנוכחי, לא הצילום."""
    deploy = server["deploy"]
    assert deploy.get("/api/console/users").status_code == 403
    assert server["admin"].put("/api/console/users/labtech",
                               json={"role": "admin"}).status_code == 200
    assert deploy.get("/api/console/users").status_code == 200


def test_a_token_signed_for_a_user_who_never_existed_is_refused(server):
    """חתימה תקפה על שם שאינו בטבלה — למשל אחרי שחזור DB ישן."""
    from server.db import get_setting
    secret = bytes.fromhex(get_setting(server["ctx"].conn, "console_secret"))
    ghost = TestClient(server["app"])
    ghost.cookies.set("imagesrv_session", forge(secret, "ghost", "admin"))
    assert ghost.get("/api/console/me").status_code == 401


def test_the_role_in_the_payload_is_ignored_even_when_signed(server):
    """הליבה: הטוקן אומר admin, הטבלה אומרת deploy. הטבלה מנצחת."""
    from server.db import get_setting
    secret = bytes.fromhex(get_setting(server["ctx"].conn, "console_secret"))
    client = TestClient(server["app"])
    client.cookies.set("imagesrv_session", forge(secret, "labtech", "admin"))
    # ‏#1073: הטבלה אומרת deploy — ול-deploy אין קונסולה. הטוקן החתום "admin"
    # לא פותח אפילו את /me, ובוודאי לא את /users.
    me = client.get("/api/console/me")
    assert me.status_code == 403
    assert "מחשב הבנייה" in me.json()["detail"]
    assert client.get("/api/console/users").status_code == 403


def test_an_expired_token_is_still_refused(server):
    """התיקון לא ביטל את התפוגה — בקרה שהשאילתה החדשה לא עוקפת אותה."""
    from server.db import get_setting
    secret = bytes.fromhex(get_setting(server["ctx"].conn, "console_secret"))
    client = TestClient(server["app"])
    client.cookies.set("imagesrv_session", forge(secret, "noc", "admin", seconds=-60))
    assert client.get("/api/console/me").status_code == 401


def test_a_tampered_signature_is_still_refused(server):
    client = TestClient(server["app"])
    client.cookies.set("imagesrv_session", forge(b"not-the-secret", "noc", "admin"))
    assert client.get("/api/console/me").status_code == 401


def test_the_last_admin_still_cannot_be_demoted_or_deleted(server):
    """תרחיש ה-QA שהתיקון היה יכול לגעת בו, ולא נגע."""
    admin = server["admin"]
    assert admin.put("/api/console/users/noc",
                     json={"role": "deploy"}).status_code == 400
    assert admin.delete("/api/console/users/noc").status_code == 400
    assert admin.get("/api/console/users").status_code == 200
