"""409 על קבוצה קיימת אינו משאיר את נעילת הכתיבה תפוסה (#184).

`console/api.py` תופס את כשל ה-INSERT ומרים 409 — **בלי `rollback`**.
ה-INSERT שנכשל כבר פתח טרנזאקציית כתיבה, והחריגה יוצאת בלי לסגור
אותה: הנעילה נשארת, והכתיבה הבאה מקבלת ``database is locked`` אחרי
`busy_timeout` שלם.

זה ה-gotcha שכתוב ב-CLAUDE.md מילה במילה:

> קורא שזרק חריגה בלי `rollback` משאיר נעילה יתומה... **זה נראה כמו
> עומס וזה קורא שכבר ויתר** (#54).

**המפעיל ייתקל בזה בוודאות** — יצירת קבוצה שכבר קיימת היא טעות
שגרתית: הקלדה חוזרת, רענון דף, לחיצה כפולה. הוא יקבל 409 סביר, ואז
**כל פעולה הבאה בקונסולה תיכשל** בלי שום קשר נראה לעין בין השתיים.

**וזה גם מסתיר באגים אחרים:** כל טסט שרץ אחרי 409 כזה נופל על
``database is locked`` ונראה כמו פלייק סביבתי. כך זה התגלה — שלושה
טסטים של #138 נפלו **גם על מעבדת ה-VM ולא רק בווינדוס**, וזה מה
שהוציא את זה מ"פלייק" ל-באג.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: השרת מסרב לכיתות ב-v1. הבדיקות כאן יוצרות קבוצת
#: כיתה ובודקות את זרימת v1.2 — ולכן מדליקות את היכולת (`classrooms_on`).
pytestmark = pytest.mark.usefixtures("classrooms_on")

GROUP = {"id": "grp_DUP", "label": "DUP", "role": "classroom"}


def test_a_second_write_after_a_group_conflict_still_works(server):
    """הבקרה השלילית של #184.

    לפני התיקון הכתיבה השנייה נפלה ב-`sqlite3.OperationalError:
    database is locked` — לא ב-409, לא ב-400, אלא בחריגה שמגיעה
    מהשרת כ-500.
    """
    admin = server["admin"]
    assert admin.post("/api/console/groups", json=GROUP).status_code == 200

    # אותה קבוצה שוב — 409 צפוי ותקין
    assert admin.post("/api/console/groups", json=GROUP).status_code == 409

    # וכאן הכל נפל: כתיבה כלשהי מיד אחרי ההתנגשות
    r = admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:91", "name": "05", "group_id": "grp_DUP"})
    assert r.status_code == 200, f"הכתיבה שאחרי ההתנגשות נכשלה: {r.status_code} {r.text[:160]}"


def test_the_conflict_does_not_leave_a_half_written_group(server):
    """ה-rollback אינו מוחק את הקבוצה שכן נוצרה קודם."""
    admin = server["admin"]
    assert admin.post("/api/console/groups", json=GROUP).status_code == 200
    admin.post("/api/console/groups", json=GROUP)
    ids = {g["id"] for g in admin.get("/api/console/groups").json()}
    assert "grp_DUP" in ids


def test_a_conflict_does_not_bleed_into_the_next_read(server):
    """קריאה אחרי ההתנגשות גם היא חייבת לעבוד — נעילת כתיבה יתומה
    חוסמת כותבים, וקורא שמנסה לשדרג נתקע איתם."""
    admin = server["admin"]
    admin.post("/api/console/groups", json=GROUP)
    admin.post("/api/console/groups", json=GROUP)
    assert admin.get("/api/console/groups").status_code == 200
    assert admin.get("/api/console/images").status_code == 200
