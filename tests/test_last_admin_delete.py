"""‏#521 — ארבעת החורים ב"המנהל האחרון לא ניתן למחיקה".

`CLAUDE.md` מונה את התרחיש הזה כקריטי. **לא הייתה לו ראיה.**

``test_the_last_admin_cannot_be_deleted`` ב-``test_server_users.py``
מוחק את ``noc`` בזמן שה-client מחובר **כ-``noc``**, ולכן הוא נעצר
בשומר ``"אי אפשר למחוק את המשתמש המחובר"`` — ושומר המנהל האחרון
לעולם אינו מגיע לריצה. **נמדד ב-07/09:** מחיקת כל בלוק השומר
מ-``console_api.py`` השאירה את ``test_server_users.py`` על ‏26
עוברים, כולל אותו טסט. הוא שומר לגיטימי על "המשתמש המחובר",
ושמו הוא מה שהסתיר את הפער.

שלושת החורים בקוד עצמו:

* ‏(א) ``admin_count`` סופר גם מנהלים **חסומים**, בעוד ``set_disabled``
  משתמש ב-``active_admin_count``. ה-docstring של ``set_disabled``
  טוען *"שני שומרים, ושניהם על אותו היגיון כמו במחיקה"* — ולא.
* ‏(ב) הבדיקה נעשתה **מחוץ** ל-``_write_lock`` שהמחיקה בתוכו: TOCTOU.
* ‏(ג) ה-DELETE לא ספר שורה — ‏200 ורישום ביומן על שם שלא היה.

**הטסטים אינם מניחים כמה מנהלים יש בזרע.** הם מודדים, ואז מורידים
למצב הנדרש. ‏fixture שישתנה לא יהפוך אותם לירוקים-לשווא.
"""

from __future__ import annotations

import pytest

from server.console import users


def _reduce_to_one_active_admin(conn, keep: str) -> None:
    """מוחק כל מנהל פעיל פרט ל-``keep``, עד שנשאר אחד.

    לא מניח שמות ולא כמויות — קורא את המצב ופועל לפיו.

    ‏#1262: המנהל שנוצר בהתקנה אינו נמחק לעולם, ולכן "נשאר מנהל פעיל
    אחד" אפשרי רק ב-DB שאין בו builtin (התקנה ישנה). הדגל מוסר כאן כדי
    לבדוק את שומר המנהל האחרון **לבדו**; שומר ה-builtin נבדק ב-
    ``test_builtin_admin_protected.py``.
    """
    conn.execute("UPDATE users SET is_builtin = 0")
    conn.commit()
    while True:
        rows = conn.execute(
            "SELECT username FROM users"
            " WHERE role = 'admin' AND disabled_at IS NULL AND username != ?",
            (keep,),
        ).fetchall()
        if not rows:
            break
        users.delete(conn, rows[0]["username"], by="test")
    assert users.active_admin_count(conn) == 1


# --- (א) מנהל חסום אינו "מי שינהל" -------------------------------------


def test_a_disabled_admin_does_not_count_as_the_one_who_would_remain(server):
    """‏``admin_count`` סופר חסומים; ``active_admin_count`` לא.

    זה החור שהיה מתיר למחוק את המנהל הפעיל האחרון כל עוד יש עוד
    אחד **חסום** ברשימה.
    """
    conn = server["ctx"].conn
    _reduce_to_one_active_admin(conn, keep="noc")

    users.create(conn, "sleeper", "sleeper-pass-1", "admin", by="test", check_policy=False)
    users.set_disabled(conn, "sleeper", True, by="test")

    assert users.admin_count(conn) == 2          # noc + sleeper
    assert users.active_admin_count(conn) == 1   # noc בלבד

    # ‏`admin_count` היה אומר 2 ומתיר את המחיקה.
    with pytest.raises(ValueError, match="המנהל האחרון"):
        users.delete(conn, "noc", by="test")
    assert users.active_admin_count(conn) == 1


# --- (ב) התנאי הוא חלק מהכתיבה, לא קריאה שלפניה -------------------------


def test_deleting_admins_one_after_another_always_leaves_one(server):
    """כל מחיקה תובעת את השארית **באותה** כתיבה.

    עד ‏#521 השומר היה ``SELECT`` שרץ לפני ה-DELETE, ובלי נעילה
    ביניהם. שתי מחיקות שכל אחת ראתה "יש שניים" הותירו אפס.
    """
    conn = server["ctx"].conn
    _reduce_to_one_active_admin(conn, keep="noc")
    users.create(conn, "alpha", "alpha-pass-123", "admin", by="test", check_policy=False)
    users.create(conn, "beta", "beta-pass-1234", "admin", by="test", check_policy=False)
    assert users.active_admin_count(conn) == 3

    users.delete(conn, "alpha", by="test")
    users.delete(conn, "beta", by="test")
    assert users.active_admin_count(conn) == 1

    with pytest.raises(ValueError, match="המנהל האחרון"):
        users.delete(conn, "noc", by="test")
    assert users.active_admin_count(conn) == 1


# --- (ג) DELETE שלא מחק אינו הצלחה --------------------------------------


def test_deleting_a_user_that_never_existed_is_404_and_not_journalled(server):
    """‏200 על מחיקה שלא קרתה הוא בדיוק הדפוס של עיקרון 5."""
    conn = server["ctx"].conn
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'user_delete'"
    ).fetchone()["n"]

    assert server["admin"].delete("/api/console/users/ghost").status_code == 404

    after = conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'user_delete'"
    ).fetchone()["n"]
    assert after == before, "היומן רשם מחיקה שלא קרתה"


# --- (ד) המסלול שלא נבדק מעולם ------------------------------------------


def test_the_last_active_admin_is_refused_through_the_console_route(server):
    """**זה המסלול שהיה חסר.**

    לא "המשתמש המחובר" — ‏`noc` מוריד את עצמו ל-deploy קודם, ואז
    ``solo`` הוא המנהל הפעיל היחיד. הבקשה עוברת דרך ה-endpoint
    ומקבלת ‏400 מהשומר, לא מהשומר האחר.
    """
    conn = server["ctx"].conn
    admin = server["admin"]
    _reduce_to_one_active_admin(conn, keep="noc")

    admin.post("/api/console/users", json={
        "username": "solo", "password": "Solo-pass-1234!", "role": "admin"})
    assert users.active_admin_count(conn) == 2

    # מוחקים את `noc` -- מותר, כי `solo` יישאר. עכשיו `solo` הוא היחיד.
    users.delete(conn, "noc", by="test")
    assert users.active_admin_count(conn) == 1

    with pytest.raises(ValueError, match="המנהל האחרון"):
        users.delete(conn, "solo", by="test")
    assert users.active_admin_count(conn) == 1
