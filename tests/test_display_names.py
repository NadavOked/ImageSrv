"""שמות תיקיות ואימג'ים — אותיות אנגלית/עברית + ספרות, בשרת, ביצירה (#138, #623).

**ההכרעה המקורית (נדב, 30/08):** *"השמות שלהם יהיו באנגלית או מספרים"* —
כי מסך התחנה היה קונסולת טקסט של Linux בלי תמיכת RTL, ועברית התגלתה
שם כמסך של ריבועים (#111).

**ההכרעה החדשה (נדב, 09/09, #623):** מסך התחנה הוא עכשיו גואי נייטיב
(Pango/HarfBuzz/FriBidi) שמרנדר עברית RTL — מוכח על ה-VM. הסיבה
היחידה ל-ASCII נעלמה, ולכן **עברית מותרת עכשיו בשמות** (#327).

⚠️ **תזמון (#111 עדיין תקף):** שם עברי מוצג נכון רק על תחנה שכבר
מריצה את הגואי. השרת מתיר; ההפעלה בפועל של שמות עבריים אחרי פריסת
הגואי. האכיפה כאן היא **ביצירה בלבד** (שם קיים לא נחסם רטרואקטיבית).

**מה שעדיין נדחה:** פיסוק שאינו `. _ -`, רווח מוביל/עוקב, התחלה
שאינה אות/ספרה, מעל 48 תווים, שורה חדשה, ריק. הקלדה במסך התחנה
נשארת אנגלית בלבד (שם משתמש/סיסמה) — עברית בשמות היא לתצוגה, לא
להקלדה שם.

שש נקודות כניסה, ובדיקה אחת:

    [A-Za-z0-9א-ת][A-Za-z0-9א-ת ._-]{0,47}
"""

from __future__ import annotations

import json

import pytest
pytest.importorskip("fastapi")

#: עברית עדיין יכולה להיות פסולה — מסיבה אחרת (פיסוק, רווח, אורך).
#: `בדיקות!` בודק בדיוק את זה: אותיות עבריות מותרות, סימן קריאה לא.
# #2878: the Latin apostrophe stays out; a long dash or a geresh cannot start a name, a long dash cannot end one
BAD = ["it's", "—start", "end—", "׳start", "כיתה ג׳ ", "בדיקות!", " leading", "-dash-first", "x" * 49, "a\nb", ""]
GOOD = ["LAB1", "Office 2024", "אופיס 2024", "כיתה יב", "tiny11 v2", "linux_base", "a.b-c", "X",
        "כיתה ג׳", "מעבדה מ״ב", "תשפ״ו", "אופיס 2024 — סטנדרט", "Office — מעודכן"]   # #2878 (Nadav 11/11)


# --- 1+2: תיקיות ---------------------------------------------------------------


@pytest.mark.parametrize("name", BAD)
def test_a_new_folder_with_a_bad_name_is_refused(server, name):
    r = server["admin"].post("/api/console/folders", json={"name": name})
    assert r.status_code == 400, f"{name!r} התקבל"


@pytest.mark.parametrize("name", GOOD)
def test_a_new_folder_with_a_good_name_is_accepted(server, name):
    assert server["admin"].post(
        "/api/console/folders", json={"name": name}).status_code == 200, name


def test_renaming_a_folder_to_a_bad_name_is_refused(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders", json={"name": "LAB1"}).status_code == 200
    r = admin.put("/api/console/folders/LAB1", json={"name": "בדיקות!"})
    assert r.status_code == 400


def test_renaming_a_folder_to_a_hebrew_name_is_accepted(server):
    # ההכרעה החדשה: עברית תקינה עוברת (#623).
    admin = server["admin"]
    assert admin.post("/api/console/folders", json={"name": "LAB2"}).status_code == 200
    assert admin.put("/api/console/folders/LAB2", json={"name": "כיתה יב"}).status_code == 200


# --- 3: עריכת אימג' ------------------------------------------------------------


@pytest.mark.parametrize("field", ["name", "folder"])
def test_editing_an_image_to_a_bad_name_is_refused(server, images_root, field):
    before = json.loads(
        (images_root / "img_7f3a91" / "manifest.json").read_text(encoding="utf-8"))
    r = server["admin"].put("/api/console/images/img_7f3a91", json={field: "בדיקות!"})
    assert r.status_code == 400
    after = json.loads(
        (images_root / "img_7f3a91" / "manifest.json").read_text(encoding="utf-8"))
    assert after[field] == before[field], "המניפסט נכתב למרות הסירוב"


def test_editing_an_image_to_a_good_name_still_works(server, images_root):
    assert server["admin"].put(
        "/api/console/images/img_7f3a91",
        json={"name": "אופיס 2024", "folder": "Teaching"}).status_code == 200


# --- 5+6: קליטה ----------------------------------------------------------------


def capture(server, **fields):
    mac = "aa:bb:cc:00:00:41"
    admin = server["admin"]
    admin.post("/api/console/groups",
               json={"id": "grp_BUILD", "label": "BUILD", "role": "build"})
    admin.post("/api/console/machines",
               json={"mac": mac, "name": "b1", "group_id": "grp_BUILD"})
    # #1251: תיקייה תקינה כברירת מחדל — אחרת שם פגום נדחה בגלל התיקייה החסרה.
    body = {"mac": mac, "disk": "sda", "name": "Base", "folder": "Lab", **fields}
    return admin.post("/api/console/tasks/capture", json=body)


@pytest.mark.parametrize("field", ["name", "folder"])
def test_a_capture_with_a_bad_name_is_refused(server, field):
    assert capture(server, **{field: "בדיקות!"}).status_code == 400, field


def test_a_capture_with_good_names_is_accepted(server):
    assert capture(server, name="Base 2024", folder="Lab").status_code == 200


def test_a_capture_with_hebrew_names_is_accepted(server):
    # ההכרעה החדשה: שם אימג' ותיקייה בעברית מתקבלים (#623).
    assert capture(server, name="בסיס 2024", folder="כיתה").status_code == 200
