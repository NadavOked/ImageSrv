"""חסימת משתמש — ומה שהופך אותה לאמיתית: היא חלה **מיד** (#186).

חסימה אינה מחיקה, ושתיהן נחוצות:

* **מחיקה** מוציאה את השם מהטבלה. שורות היומן ממשיכות להזכיר אותו,
  ולכן "מי עשה את זה" הופך לשם שאין מאחוריו כלום.
* **חסימה** משאירה את הרשומה, הפיכה, ומתאימה למצב האמיתי — טכנאי
  שעזב לתקופה, חשד לדליפת סיסמה, חשבון שצריך להשעות עד בירור.

**המבחן היחיד שחשוב הוא התזמון.** ‏`auth.check` קורא את התפקיד מהטבלה
בכל בקשה, בלי מטמון, וזה בדיוק התיקון של #91:

> התפקיד שבמטען הוא צילום מרגע הכניסה, ומנהל שהורד ל-deploy או שנמחק
> נשאר מנהל עד סוף ה-TTL... **אין כאן מטמון: מטמון היה מחזיר בדיוק את
> החלון שהתיקון סוגר.**

חסימה שנבדקת רק בכניסה הייתה פותחת מחדש את אותו חלון — עד 12 שעות
שבהן משתמש חסום ממשיך לעבוד. לכן הבדיקה כאן היא על **סשן שכבר פתוח**.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:                                    # pragma: no cover
    TestClient = None


def a_second_admin(server, username="tech2", password="Tech-pass-123!"):
    """מנהל נוסף, כדי שחסימה לא תיחסם על "המנהל האחרון"."""
    from conftest import complete_console_login
    assert server["admin"].post("/api/console/users", json={
        "username": username, "password": password, "role": "admin",
    }).status_code == 200
    client = TestClient(server["app"])
    complete_console_login(client, server["ctx"].conn, username, password)
    return client, username, password


def test_a_blocked_user_loses_an_open_session_immediately(server):
    """הבקרה השלילית של #186, והנקודה כולה.

    לפני התיקון לא הייתה חסימה בכלל; אחריו — היא חייבת לחול על סשן
    פתוח ולא רק על הכניסה הבאה, אחרת זה #91 מחדש.
    """
    victim, name, _ = a_second_admin(server)
    assert victim.get("/api/console/users").status_code == 200      # עובד לפני

    assert server["admin"].put(f"/api/console/users/{name}",
                               json={"disabled": True}).status_code == 200

    r = victim.get("/api/console/users")
    assert r.status_code in (401, 403), f"סשן פתוח שרד חסימה: {r.status_code}"


def test_a_blocked_user_cannot_log_in(server):
    victim, name, password = a_second_admin(server)
    server["admin"].put(f"/api/console/users/{name}", json={"disabled": True})

    fresh = TestClient(server["app"])
    assert fresh.post("/api/console/login", json={
        "username": name, "password": password}).status_code == 401


def test_unblocking_restores_access(server):
    """חסימה הפיכה — זה ההבדל מול מחיקה."""
    _, name, password = a_second_admin(server)
    admin = server["admin"]
    admin.put(f"/api/console/users/{name}", json={"disabled": True})
    assert admin.put(f"/api/console/users/{name}",
                     json={"disabled": False}).status_code == 200

    fresh = TestClient(server["app"])
    assert fresh.post("/api/console/login", json={
        "username": name, "password": password}).status_code == 200


def test_the_last_admin_cannot_be_blocked(server):
    """אותו שומר שמגן מפני מחיקת המנהל האחרון — חסימה נועלת בדיוק כמוה.

    **נבדק ברמת הפונקציה ולא דרך ה-API, ובכוונה.** דרך ה-API השומר הזה
    אינו ניתן להגעה: רק מנהל יכול לערוך משתמשים, ולכן מנהל שחוסם מנהל
    אחר תמיד נשאר בעצמו — ואם ינסה לחסום את עצמו, שומר "המשתמש
    המחובר" תופס קודם.

    זה לא הופך אותו למת. ‏`set_disabled` היא הפונקציה, וקוראים לה
    יכולים להתווסף — תסריט תחזוקה, מיגרציה, נתיב עתידי. שומר שקיים רק
    בשכבת ה-HTTP הוא שומר שנעלם ברגע שמישהו עוקף אותה.
    """
    from server.console import users                                # noqa: PLC0415

    conn = server["ctx"].conn
    # שני מנהלים בהתקנה: `admin` מההרצה הראשונה, ו-`noc` של הפיקסטורה.
    # ‏#1262: `admin` הוא ה-builtin ואינו ניתן להשבתה; הדגל מוסר כדי לבדוק
    # את שומר המנהל האחרון לבדו (שומר ה-builtin: test_builtin_admin_protected).
    conn.execute("UPDATE users SET is_builtin = 0 WHERE username = 'admin'")
    conn.commit()
    users.set_disabled(conn, "admin", True, by="test")
    assert users.active_admin_count(conn) == 1

    with pytest.raises(ValueError) as err:
        users.set_disabled(conn, "noc", True, by="test")
    assert "מנהל" in str(err.value)
    assert users.active_admin_count(conn) == 1              # לא נחסם בפועל


def test_you_cannot_block_yourself(server):
    """גם כשיש מנהל נוסף: חסימה עצמית היא נעילה מיידית מחוץ למסך."""
    a_second_admin(server)
    r = server["admin"].put("/api/console/users/noc", json={"disabled": True})
    assert r.status_code == 400


def test_the_list_says_who_is_blocked(server):
    """המסך חייב להראות את זה — חסום שנראה פעיל הוא מלכודת למפעיל."""
    _, name, _ = a_second_admin(server)
    server["admin"].put(f"/api/console/users/{name}", json={"disabled": True})
    users = {u["username"]: u for u in server["admin"].get("/api/console/users").json()}
    assert users[name]["disabled"] is True
    assert users["noc"]["disabled"] is False
