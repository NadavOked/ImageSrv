"""עריכת משתמשים — שינוי תפקיד ואיפוס סיסמה, והמעקות סביבם.

המעקה המרכזי: אי אפשר להישאר בלי מנהל. קונסולה בלי מנהל היא קונסולה
שאי אפשר לתקן מבפנים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def test_resetting_a_password_takes_effect(server):
    admin = server["admin"]
    assert admin.put("/api/console/users/labtech",
                     json={"password": "Brand-new-1!"}).status_code == 200
    assert server["anon"].post(
        "/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}
    ).status_code == 401
    assert server["anon"].post(
        "/api/console/login", json={"username": "labtech", "password": "Brand-new-1!"}
    ).status_code == 200


def test_promoting_a_user_grants_admin_screens(server):
    deploy = server["deploy"]
    assert deploy.get("/api/console/users").status_code == 403
    assert server["admin"].put("/api/console/users/labtech",
                               json={"role": "admin"}).status_code == 200
    # התפקיד נקרא מהטבלה בכל בקשה, ולכן ה-cookie הישן כבר נושא אותו (#91);
    # כניסה מחדש עובדת גם היא (כולל הרשמת MFA של admin חדש, #1085).
    from conftest import complete_console_login
    complete_console_login(deploy, server["ctx"].conn, "labtech", "deploy-pass-1")
    assert deploy.get("/api/console/users").status_code == 200


def test_an_empty_password_field_leaves_it_alone(server):
    admin = server["admin"]
    assert admin.put("/api/console/users/labtech",
                     json={"role": "deploy", "password": ""}).status_code == 200
    assert server["anon"].post(
        "/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}
    ).status_code == 200


def test_a_short_password_is_refused(server):
    r = server["admin"].put("/api/console/users/labtech", json={"password": "short"})
    assert r.status_code == 400


def test_you_cannot_change_your_own_role(server):
    """מנהל שמוריד את עצמו נועל את עצמו החוצה באותו רגע."""
    r = server["admin"].put("/api/console/users/noc", json={"role": "deploy"})
    assert r.status_code == 400


def test_the_last_admin_cannot_be_demoted(server):
    admin = server["admin"]
    admin.post("/api/console/users",
               json={"username": "second", "password": "Second-pass-1!", "role": "admin"})
    # יש שניים — אפשר להוריד אחד.
    assert admin.put("/api/console/users/second", json={"role": "deploy"}).status_code == 200
    # ועכשיו noc הוא האחרון: הורדה שלו נחסמת (גם דרך הכלל של "עצמך").
    assert admin.put("/api/console/users/noc", json={"role": "deploy"}).status_code == 400


def test_the_last_admin_cannot_be_deleted(server):
    admin = server["admin"]
    admin.post("/api/console/users",
               json={"username": "second", "password": "Second-pass-1!", "role": "admin"})
    assert admin.delete("/api/console/users/second").status_code == 200
    # noc נשאר יחיד — מחיקתו הייתה נועלת את הקונסולה.
    admin.post("/api/console/users",
               json={"username": "third", "password": "Third-pass-12!", "role": "deploy"})
    third = server["anon"]
    assert admin.delete("/api/console/users/noc").status_code == 400


def test_editing_users_is_admin_only(server):
    assert server["deploy"].put("/api/console/users/noc",
                                json={"role": "deploy"}).status_code == 403


def test_editing_an_unknown_user_is_a_clean_error(server):
    r = server["admin"].put("/api/console/users/ghost", json={"role": "admin"})
    assert r.status_code == 400


def test_the_edit_reaches_the_journal_in_hebrew(server):
    server["admin"].put("/api/console/users/labtech", json={"role": "admin"})
    rows = server["admin"].get("/api/console/journal").json()
    edit = next(r for r in rows if r["event"] == "user_edit")
    assert edit["label"] == "משתמש עודכן"
    assert "labtech" in edit["text"]


# --- הפרמטרים של הגיבוב באים מהרשומה (#92) -----------------------------------


def rehash_with(server, username: str, password: str, iterations: int) -> None:
    """כותב לרשומה hash אמיתי במספר סבבים אחר — מה שמיגרציה עתידית,
    או שחזור מגיבוי ישן, משאירים בטבלה."""
    import hashlib
    import secrets
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), iterations
    ).hex()
    conn = server["ctx"].conn
    conn.execute("UPDATE users SET pw_hash = ? WHERE username = ?",
                 (f"pbkdf2${iterations}${salt}${digest}", username))
    conn.commit()


def test_a_password_hashed_with_another_iteration_count_still_verifies(server):
    """הבקרה השלילית של #92.

    לפני התיקון `verify` פענח את מספר הסבבים ואז גיבב עם הקבוע של
    הקובץ, ולכן כל רשומה שנוצרה במספר אחר נכשלה בשקט — "שם משתמש או
    סיסמה שגויים" על סיסמה נכונה לחלוטין.
    """
    rehash_with(server, "labtech", "deploy-pass-1", 50_000)
    assert server["anon"].post(
        "/api/console/login",
        json={"username": "labtech", "password": "deploy-pass-1"},
    ).status_code == 200


def test_the_wrong_password_is_still_wrong_at_another_iteration_count(server):
    rehash_with(server, "labtech", "deploy-pass-1", 50_000)
    assert server["anon"].post(
        "/api/console/login",
        json={"username": "labtech", "password": "not-the-password"},
    ).status_code == 401


def test_a_new_password_is_stored_with_the_current_default(server):
    from server.console import users
    server["admin"].put("/api/console/users/labtech",
                        json={"password": "Brand-new-1!"})
    stored = server["ctx"].conn.execute(
        "SELECT pw_hash FROM users WHERE username = 'labtech'"
    ).fetchone()["pw_hash"]
    assert stored.split("$")[1] == str(users._ITERATIONS)


@pytest.mark.parametrize("stored", [
    "scrypt$200000$aabb$ccdd",        # סכימה שאיננו יודעים לאמת
    "pbkdf2$notanumber$aabb$ccdd",    # מספר סבבים שאינו מספר
    "pbkdf2$0$aabb$ccdd",             # אפס סבבים — גיבוב שאינו גיבוב
    "pbkdf2$200000$zzzz$ccdd",        # מלח שאינו hex
    "pbkdf2$200000$aabb$",            # בלי digest
    "justgarbage",
])
def test_a_record_we_cannot_read_is_refused_not_guessed(server, stored):
    """עיקרון 5: "לא הבנו את הרשומה" איננו "ננחש את ברירת המחדל"."""
    from server.console import users
    conn = server["ctx"].conn
    conn.execute("UPDATE users SET pw_hash = ? WHERE username = 'labtech'", (stored,))
    conn.commit()
    assert users.verify(conn, "labtech", "deploy-pass-1") is None
    assert users.verify(conn, "labtech", "") is None


# --- שם משתמש שאפשר גם לאמת אחר כך (#93) -------------------------------------


@pytest.mark.parametrize(
    "name", ["ni|ck", "|", "admin|admin|9999999999", "a\nb", "a\tb"]
)
def test_a_username_the_token_cannot_carry_is_refused(server, name):
    """הבקרה השלילית של #93.

    לפני התיקון החשבון נוצר בהצלחה — ואז לא הצליח להתחבר אף פעם: הטוקן
    `name|role|expiry|signature` יצא עם ארבעה מפרידים או יותר,
    ו-`auth.check` דוחה כל מה שאין בו בדיוק שלושה.
    """
    r = server["admin"].post(
        "/api/console/users",
        json={"username": name, "password": "Some-pass-123!", "role": "deploy"},
    )
    assert r.status_code == 400
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE username = ?", (name.strip(),)
    ).fetchone()["n"] == 0


def test_a_hebrew_username_is_refused_cleanly_instead_of_500_on_login(server):
    """נמצא תוך כדי #93, ותועד ב-#111.

    ה-cookie הוא כותרת HTTP ש-starlette מקודד ב-latin-1. שם בעברית עבר
    את היצירה ואת בדיקת הסיסמה, ואז הפיל את `set_cookie` — כלומר
    **500 על סיסמה נכונה**, בקונסולה שכולה עברית — כלומר הכישלון היה
    ניתן לאבחון וההצלחה לא.

    נדב הכריע ששמות המשתמש באנגלית בלבד, ולכן זה **כלל ולא עקיפה**:
    ‏#111 נסגר כאן ולא בהחלפת פורמט ה-cookie, שהייתה מנתקת פעם אחת את
    כל המחוברים.
    """
    r = server["admin"].post(
        "/api/console/users",
        json={"username": "רינה כהן", "password": "Some-pass-123!", "role": "deploy"},
    )
    assert r.status_code == 400
    assert "אנגליות" in r.json()["detail"]     # הודעה שאומרת מה לעשות


def test_a_created_user_can_actually_sign_in(server):
    """הצד החיובי של #93: שם תקין נוצר — ומתחבר. נקודה מותרת, רווח לא (#1085).
    (‏admin: ‏#1073 — משתמש deploy אינו נכנס לקונסולה, ו-/me סגור בפניו.)"""
    assert server["admin"].post(
        "/api/console/users",
        json={"username": "Rina.C", "password": "Some-pass-123!", "role": "admin"},
    ).status_code == 200
    fresh = server["anon"]
    assert fresh.post(
        "/api/console/login",
        json={"username": "Rina.C", "password": "Some-pass-123!"},
    ).status_code == 200
    assert fresh.get("/api/console/me").json()["username"] == "Rina.C"
