"""#1912 / #526: שומר התנהגותי לשמות תיקיות בקונסולה — כל הדרכים ליצור תיקייה.

#526: ארבע דרכים ליצור תיקייה (POST, PUT, order, rename) ולא כולן בדקו את
השם; POST דרס תיקייה קיימת. הטסטים של #526 ב-`test_server_library.py` תופסים
שם לא תקין ב-PUT, שם לא קיים ב-order, ודריסה ב-POST. כאן מה שנשאר מהטבלה:

* שמות שמסוכנים בפועל — `..`, רווח בקצה, סלאש — נדחים ב-POST וב-rename,
  ואינם נשמרים מקוצצים או "מתוקנים" בשקט.
* עברית ורווח פנימי מותרים (זה שם תצוגה, לא נתיב) ונקראים בחזרה כמו שנכתבו.
* שינוי שם לתיקייה שכבר קיימת — 409, והתיאור והסדר של הקיימת לא נפגעים.

כל טסט קורא את המצב בחזרה מ-GET /folders (ראיה חיובית), לא רק את קוד התשובה.

בקרה שלילית (ראו גוף ה-PR): `_checked_name` ב-POST הוחלף ב-strip; בדיקת ה-409
ב-rename הוסרה; `_checked_name` על שם ה-URL ב-PUT הוסר.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

BAD_NAMES = ["..", " Lab", "Lab ", "a/b", "../etc", "a\\b", ""]


def _folder_names(admin) -> list[str]:
    r = admin.get("/api/console/folders")
    assert r.status_code == 200
    return [f["name"] for f in r.json()]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_post_folder_refuses_a_dangerous_name_and_stores_nothing(server, name):
    admin = server["admin"]
    before = _folder_names(admin)
    r = admin.post("/api/console/folders", json={"name": name})
    assert r.status_code == 400, f"{name!r} -> {r.status_code}"
    assert _folder_names(admin) == before, f"{name!r} נשמר למרות הדחייה"


@pytest.mark.parametrize("name", ["כיתה יב", "מעבדת סייבר 2", "Lab-1.a"])
def test_post_folder_accepts_hebrew_and_inner_spaces_exactly_as_typed(server, name):
    admin = server["admin"]
    assert admin.post("/api/console/folders",
                      json={"name": name, "description": "d"}).status_code == 200
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert name in folders, f"{name!r} לא חזר ב-GET /folders"
    assert folders[name]["description"] == "d"


@pytest.mark.parametrize("new_name", ["..", " Lab", "Lab ", "a/b"])
def test_renaming_a_folder_to_a_dangerous_name_is_refused_and_keeps_the_old_one(server, new_name):
    admin = server["admin"]
    assert admin.post("/api/console/folders",
                      json={"name": "Cyber", "description": "מעבדות אבטחה"}).status_code == 200
    r = admin.put("/api/console/folders/Cyber", json={"name": new_name})
    assert r.status_code == 400, f"{new_name!r} -> {r.status_code}"
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert new_name not in folders
    assert folders["Cyber"]["description"] == "מעבדות אבטחה"


def test_renaming_a_folder_onto_an_existing_one_is_409_and_touches_neither(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders",
                      json={"name": "Alpha", "description": "first"}).status_code == 200
    assert admin.post("/api/console/folders",
                      json={"name": "Beta", "description": "second"}).status_code == 200
    r = admin.put("/api/console/folders/Alpha", json={"name": "Beta"})
    assert r.status_code == 409
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert folders["Alpha"]["description"] == "first", "התיקייה שהוחלפה שמה נפגעה"
    assert folders["Beta"]["description"] == "second", "היעד נדרס"


def test_put_with_a_dangerous_url_name_does_not_create_a_folder(server):
    """PUT על שם שאינו קיים יוצר אותו — ולכן שם ה-URL נבדק כמו שם ב-POST."""
    admin = server["admin"]
    before = _folder_names(admin)
    r = admin.put("/api/console/folders/%20Lab", json={"description": "x"})
    assert r.status_code == 400, r.status_code
    assert _folder_names(admin) == before
