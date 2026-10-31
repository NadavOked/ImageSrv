"""‏#1262 — המנהל שנוצר בהתקנה (``is_builtin=1``) הוא דלת החירום.

הכרעת נדב (10/10): אי אפשר למחוק אותו, להשבית אותו או להוריד אותו
מתפקיד מנהל. הוא גם בכוונה בלי MFA (‏``builtin_no_mfa``), ולכן הוא
הדרך חזרה כשכל השאר ננעלו.

עד #1262 השומר היחיד היה "המנהל הפעיל האחרון" — וכל עוד היה מנהל
פעיל נוסף, ה-builtin נמחק בשקט. **כל הטסטים כאן רצים כשיש מנהל פעיל
נוסף** (``noc`` של הפיקסטורה), כי זה בדיוק המצב שבו השומר הישן שותק.

ה-builtin בפיקסטורה הוא ``admin`` — זה ש-``ensure_admin`` יוצר
בהפעלה הראשונה של ``create_app``. הטסטים מוודאים זאת במפורש ולא מניחים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.console import users  # noqa: E402

BUILTIN = "admin"


def _row(conn, name):
    return conn.execute(
        "SELECT role, disabled_at, is_builtin FROM users WHERE username = ?",
        (name,),
    ).fetchone()


def _journal_count(conn, event):
    return conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = ?", (event,)
    ).fetchone()["n"]


@pytest.fixture()
def env(server):
    conn = server["ctx"].conn
    row = _row(conn, BUILTIN)
    assert row is not None and row["is_builtin"] == 1, "אין builtin בפיקסטורה"
    # השומר הישן ("המנהל הפעיל האחרון") חייב להיות שקט כאן — אחרת הטסט
    # היה עובר בגללו ולא בגלל #1262.
    assert users.active_admin_count(conn) >= 2
    return server


# --- מחיקה ----------------------------------------------------------------


def test_deleting_the_builtin_is_refused_even_with_another_active_admin(env):
    conn = env["ctx"].conn
    before = _journal_count(conn, "user_delete")
    with pytest.raises(ValueError, match="נוצר בהתקנה"):
        users.delete(conn, BUILTIN, by="test")
    assert _row(conn, BUILTIN) is not None, "ה-builtin נמחק"
    assert _journal_count(conn, "user_delete") == before


def test_deleting_the_builtin_through_the_console_is_409_with_a_named_reason(env):
    conn = env["ctx"].conn
    r = env["admin"].delete(f"/api/console/users/{BUILTIN}")
    assert r.status_code == 409, r.text
    assert "נוצר בהתקנה" in r.json()["detail"]
    assert _row(conn, BUILTIN) is not None


def test_a_disabled_builtin_is_still_not_deletable(env):
    """השומר הוא ``is_builtin`` ב-WHERE של ה-DELETE — לא "מנהל פעיל".

    השומר הישן מתיר למחוק מנהל **חסום** תמיד. builtin חסום (מצב ישן
    מלפני #1262, או כתיבה ידנית ל-DB) עדיין דלת החירום.
    """
    conn = env["ctx"].conn
    conn.execute("UPDATE users SET disabled_at = '2026-01-01T00:00:00Z'"
                 " WHERE username = ?", (BUILTIN,))
    conn.commit()
    with pytest.raises(ValueError, match="נוצר בהתקנה"):
        users.delete(conn, BUILTIN, by="test")
    assert _row(conn, BUILTIN) is not None


def test_the_three_refusals_stay_distinct(env):
    """לא קיים / המנהל האחרון / builtin — שלוש תשובות, לא אחת מקופלת."""
    conn = env["ctx"].conn
    assert env["admin"].delete("/api/console/users/ghost").status_code == 404
    assert env["admin"].delete(f"/api/console/users/{BUILTIN}").status_code == 409

    with pytest.raises(ValueError, match="לא קיים") as err:
        users.delete(conn, "ghost", by="test")
    assert "נוצר בהתקנה" not in str(err.value)


# --- השבתה ----------------------------------------------------------------


def test_disabling_the_builtin_is_refused(env):
    conn = env["ctx"].conn
    with pytest.raises(ValueError, match="נוצר בהתקנה"):
        users.set_disabled(conn, BUILTIN, True, by="test")
    assert _row(conn, BUILTIN)["disabled_at"] is None


def test_disabling_the_builtin_through_the_console_is_409(env):
    conn = env["ctx"].conn
    r = env["admin"].put(f"/api/console/users/{BUILTIN}", json={"disabled": True})
    assert r.status_code == 409, r.text
    assert "נוצר בהתקנה" in r.json()["detail"]
    assert _row(conn, BUILTIN)["disabled_at"] is None


def test_a_disabled_builtin_can_still_be_enabled(env):
    """השחרור אינו נחסם — רק הכיוון שנועל."""
    conn = env["ctx"].conn
    conn.execute("UPDATE users SET disabled_at = '2026-01-01T00:00:00Z'"
                 " WHERE username = ?", (BUILTIN,))
    conn.commit()
    users.set_disabled(conn, BUILTIN, False, by="test")
    assert _row(conn, BUILTIN)["disabled_at"] is None


# --- הורדת תפקיד ----------------------------------------------------------


def test_demoting_the_builtin_is_refused(env):
    conn = env["ctx"].conn
    with pytest.raises(ValueError, match="נוצר בהתקנה"):
        users.update(conn, BUILTIN, by="test", role="deploy")
    assert _row(conn, BUILTIN)["role"] == "admin"


def test_demoting_the_builtin_through_the_console_is_409_and_changes_nothing(env):
    """תפקיד וסיסמה באותה בקשה: הסירוב לפני הכתיבה, ולכן גם הסיסמה לא זזה."""
    conn = env["ctx"].conn
    pw_before = conn.execute("SELECT pw_hash FROM users WHERE username = ?",
                             (BUILTIN,)).fetchone()["pw_hash"]
    r = env["admin"].put(f"/api/console/users/{BUILTIN}",
                         json={"role": "deploy", "password": "Brand-new-1!"})
    assert r.status_code == 409, r.text
    assert "נוצר בהתקנה" in r.json()["detail"]
    assert _row(conn, BUILTIN)["role"] == "admin"
    assert conn.execute("SELECT pw_hash FROM users WHERE username = ?",
                        (BUILTIN,)).fetchone()["pw_hash"] == pw_before


def test_the_builtins_password_can_still_be_changed(env):
    """השרת עדיין מקבל סיסמה חדשה ל-builtin — זה מה שעושים לדלת חירום שדלפה.

    בקונסולה זה "איפוס סיסמה…" בתפריט שלו (POST reset-password), או "שנה
    סיסמה" ב"החשבון שלי" כשהוא המחובר (#1268); ה-PUT הזה הוא ה-API.
    """
    r = env["admin"].put(f"/api/console/users/{BUILTIN}",
                         json={"password": "Brand-new-1!"})
    assert r.status_code == 200, r.text
    assert users.verify(env["ctx"].conn, BUILTIN, "Brand-new-1!") == "admin"


# --- מנהל רגיל: עדיין ניתן לכל השלוש (לא שומר-יתר) -------------------------


def _regular_admin(env, name="tech2"):
    assert env["admin"].post("/api/console/users", json={
        "username": name, "password": "Tech-pass-123!", "role": "admin",
    }).status_code == 200
    assert _row(env["ctx"].conn, name)["is_builtin"] == 0
    return name


def test_a_regular_admin_can_still_be_demoted_disabled_and_deleted(env):
    conn = env["ctx"].conn
    name = _regular_admin(env)
    admin = env["admin"]
    assert admin.put(f"/api/console/users/{name}", json={"role": "deploy"}).status_code == 200
    assert _row(conn, name)["role"] == "deploy"
    assert admin.put(f"/api/console/users/{name}", json={"role": "admin"}).status_code == 200
    assert admin.put(f"/api/console/users/{name}", json={"disabled": True}).status_code == 200
    assert _row(conn, name)["disabled_at"] is not None
    assert admin.delete(f"/api/console/users/{name}").status_code == 200
    assert _row(conn, name) is None
