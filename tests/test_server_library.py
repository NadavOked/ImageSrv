"""בדיקות סבב המשוב הראשון על הקונסולה — ספרייה, מכונות ידניות, יומן עברי.

העריכות של הספרייה חייבות להגיע עד הדיסק: manifest.json הוא מקור האמת,
אז הבדיקות קוראות את הקובץ עצמו אחרי כל עריכה, לא רק את ה-API.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from conftest import (
    MANIFEST_256, MANIFEST_LINUX, hello_body, setup_classroom, write_image,
)

from server.archive import tar_stream


# --- עריכת אימג'ים — נכתבת למניפסט עצמו --------------------------------------


def manifest_on_disk(images_root, image_id):
    return json.loads(
        (images_root / image_id / "manifest.json").read_text(encoding="utf-8")
    )


def test_image_edits_land_in_the_manifest(server, images_root):
    admin = server["admin"]
    r = admin.put("/api/console/images/img_7f3a91",
                  json={"name": "Office 2024 Updated", "folder": "Teaching", "sort": 1})
    assert r.status_code == 200

    raw = manifest_on_disk(images_root, "img_7f3a91")
    assert raw["name"] == "Office 2024 Updated"
    assert raw["folder"] == "Teaching"
    assert raw["sort"] == 1
    assert raw["partitions"]                       # שאר המניפסט לא נפגע
    assert raw["field_from_the_future"] == "ignored"

    listed = {m["id"]: m for m in admin.get("/api/console/images").json()}
    assert listed["img_7f3a91"]["folder"] == "Teaching"


def test_sort_controls_the_order_within_a_folder(server):
    admin = server["admin"]
    assert admin.put("/api/console/images/img_7f3a91", json={"sort": 2}).status_code == 200
    assert admin.put("/api/console/images/img_2c8e04", json={"sort": 1}).status_code == 200
    ids = [m["id"] for m in admin.get("/api/console/images").json()]
    assert ids == ["img_2c8e04", "img_7f3a91"]


def test_only_display_fields_are_editable(server):
    r = server["admin"].put("/api/console/images/img_7f3a91",
                            json={"min_target_bytes": 1})
    assert r.status_code == 400


def test_image_delete_needs_the_exact_name(server, images_root):
    admin = server["admin"]
    r = admin.post("/api/console/images/img_7f3a91/delete",
                   json={"confirm_name": "לא השם"})
    assert r.status_code == 400
    assert (images_root / "img_7f3a91").exists()

    r = admin.post("/api/console/images/img_7f3a91/delete",
                   json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 200
    assert not (images_root / "img_7f3a91").exists()


def test_image_in_active_session_cannot_be_deleted(server, images_root):
    admin = server["admin"]
    ctx = server["ctx"]
    # שידור פעיל על האימג' — עכשיו הוא בשימוש, המחיקה חייבת להידחות
    ctx.conn.execute("INSERT INTO groups (id, label, role, sort) VALUES (?, ?, ?, 0)",
                     ("g1", "LAB1", "classroom"))
    ctx.conn.commit()
    ctx.store.open(group_id="g1", image_id="img_7f3a91", prefix="LAB1",
                   expected_clients=2, opened_by="test")
    # מחיקה עם שם נכון חייבת להידחות ב-409, והאימג' נשאר
    r = admin.post("/api/console/images/img_7f3a91/delete",
                   json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 409
    assert (images_root / "img_7f3a91").exists()


def test_library_edits_are_admin_only(server):
    deploy = server["deploy"]
    assert deploy.put("/api/console/images/img_7f3a91", json={"sort": 1}).status_code == 403
    assert deploy.post("/api/console/images/img_7f3a91/delete",
                       json={"confirm_name": "x"}).status_code == 403
    assert deploy.post("/api/console/folders", json={"name": "x"}).status_code == 403


# --- תיקיות ------------------------------------------------------------------


def test_folders_merge_console_and_disk(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders",
                      json={"name": "Cyber", "description": "מעבדות אבטחה"}).status_code == 200
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert folders["Office"]["images"] == 2          # מהמניפסטים שבדיסק
    assert folders["Cyber"]["images"] == 0          # נוצרה בקונסולה, עדיין ריקה
    assert folders["Cyber"]["description"] == "מעבדות אבטחה"


def test_nonempty_folder_cannot_be_deleted(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders/Office/delete").status_code == 409
    admin.post("/api/console/folders", json={"name": "Temp"})
    assert admin.post("/api/console/folders/Temp/delete").status_code == 200


# --- #526: ארבע דרכים ליצור תיקייה — כולן עוברות דרך אותה בדיקה ---------------
# כל טסט כאן הוא בקרה שלילית לשורה אחת בטבלת ה-Issue: הוא נופל אם הבדיקה
# שהוסרה בתיקון מוחזרת.


def test_put_folder_validates_the_url_name(server):
    """‏PUT /folders/{name} יצר תיקייה משם ה-URL בלי `_checked_name`
    (שורה 178). שם לא תקין חייב להידחות כמו ב-POST, לא להיווצר בשקט."""
    admin = server["admin"]
    r = admin.put("/api/console/folders/a@b", json={"description": "x"})
    assert r.status_code == 400
    names = [f["name"] for f in admin.get("/api/console/folders").json()]
    assert "a@b" not in names


def test_folder_order_rejects_an_unknown_name(server):
    """‏POST /folders/order יצר תיקייה משם ברשימה (שורה 153). שם שאינו
    קיים חייב לחזור 400, כמו groups/order — ולא להיווצר."""
    admin = server["admin"]
    r = admin.post("/api/console/folders/order", json={"names": ["no_such_folder"]})
    assert r.status_code == 400
    names = [f["name"] for f in admin.get("/api/console/folders").json()]
    assert "no_such_folder" not in names


def test_creating_a_folder_that_exists_does_not_overwrite(server):
    """‏POST /folders דרס תיקייה קיימת ודיווח יצירה (שורה 191). שם קיים
    חייב לחזור 409, והתיאור שהיה חייב להישאר."""
    admin = server["admin"]
    assert admin.post("/api/console/folders",
                      json={"name": "Cyber", "description": "מעבדות אבטחה"}).status_code == 200
    r = admin.post("/api/console/folders", json={"name": "Cyber"})
    assert r.status_code == 409
    folders = {f["name"]: f for f in admin.get("/api/console/folders").json()}
    assert folders["Cyber"]["description"] == "מעבדות אבטחה"


def test_deleting_a_missing_folder_is_404_and_not_journaled(server):
    """מחיקת תיקייה שאינה קיימת החזירה 200 ורשמה ביומן (שורה 202).
    חייב 404, והיומן רק אחרי מחיקה שקרתה."""
    admin = server["admin"]
    r = admin.post("/api/console/folders/NoSuch/delete")
    assert r.status_code == 404
    events = [row["event"] for row in admin.get("/api/console/journal").json()]
    assert "folder_delete" not in events


# --- מכונות — הוספה ידנית ועריכה מפורשת --------------------------------------


def test_manual_add_and_explicit_edit(server):
    admin = server["admin"]
    setup_classroom(server)

    r = admin.post("/api/console/machines",
                   json={"mac": "B4-2E-99-07-1A-C9", "name": "7", "group_id": "grp_LAB1"})
    assert r.status_code == 200 and r.json()["mac"] == "b4:2e:99:07:1a:c9"

    # הוספה חוזרת — שגיאה מפורשת, לא דריסה.
    r = admin.post("/api/console/machines",
                   json={"mac": "b4:2e:99:07:1a:c9", "name": "08", "group_id": "grp_LAB1"})
    assert r.status_code == 400

    # עריכה מפורשת של השם — הדרך היחידה לשנות סיומת.
    r = admin.put("/api/console/machines/b4:2e:99:07:1a:c9", json={"name": "ins"})
    assert r.status_code == 200
    machines = {m["mac"]: m for m in admin.get("/api/console/machines").json()}
    assert machines["b4:2e:99:07:1a:c9"]["suffix"] == "INS"


def test_cloner_machines_get_computer_names(server):
    """‏#1997 (נדב, 22/10): גם מחשב שיכפול מקבל שם מחשב באנגלית — לא שם
    חופשי. שם הקבוצה ("חדר שיכפולים") הוא תווית תצוגה ונשאר חופשי."""
    admin = server["admin"]
    assert admin.post("/api/console/groups",
                      json={"id": "grp_CLONE", "label": "חדר שיכפולים",
                            "role": "cloner"}).status_code == 200
    r = admin.post("/api/console/machines/import",
                   json={"group_id": "grp_CLONE",
                         "text": "aa:bb:cc:00:00:01 PC-1\naa:bb:cc:00:00:02 PC-2"})
    assert r.json()["saved"] == 2
    # שם עם עברית ורווח — נדחה, וההודעה נוקבת בכלל.
    r = admin.post("/api/console/machines/import",
                   json={"group_id": "grp_CLONE", "text": "aa:bb:cc:00:00:04 עמדה 4",
                         "dry_run": True})
    assert "אותיות אנגליות" in r.json()["preview"][0]["error"]

    # ובכיתה אותו שם היה נדחה.
    setup_classroom(server)
    r = admin.post("/api/console/machines/import",
                   json={"group_id": "grp_LAB1", "text": "aa:bb:cc:00:00:03 עמדה 3",
                         "dry_run": True})
    assert "01-99" in r.json()["preview"][0]["error"]


def test_folder_order_is_saved_and_served(server):
    """סדר התיקיות נקבע בגרירה בקונסולה — כמו סדר הכיתות."""
    admin = server["admin"]
    for name in ("A", "B", "C"):
        assert admin.post("/api/console/folders",
                          json={"name": name}).status_code == 200
    assert admin.post("/api/console/folders/order",
                      json={"names": ["C", "A", "B"]}).status_code == 200
    names = [f["name"] for f in admin.get("/api/console/folders").json()]
    # התיקיות מהמניפסטים (Office) מצטרפות אחרי הסדר שנקבע.
    assert names[:3] == ["C", "A", "B"]

    # תיקייה שלא נשלחה ברשימה לא נעלמת — נשארת בסוף.
    assert admin.post("/api/console/folders/order",
                      json={"names": ["B"]}).status_code == 200
    names = [f["name"] for f in admin.get("/api/console/folders").json()]
    assert names[0] == "B" and set(names) >= {"A", "B", "C"}

    assert server["deploy"].post("/api/console/folders/order",
                                 json={"names": []}).status_code == 403


def test_group_id_is_optional_and_derived_from_the_name(server, classrooms_on):
    """שם קבוצה חופשי — עברית, אנגלית או מספרים. המזהה נגזר כשלא הוקלד."""
    admin = server["admin"]

    # שם מספרי: המזהה נגזר ממנו ישירות (וכך גם ברירת המחדל של הקידומת).
    assert admin.post("/api/console/groups",
                      json={"label": "303", "role": "classroom"}).status_code == 200
    groups = {g["label"]: g["id"] for g in admin.get("/api/console/groups").json()}
    assert groups["303"] == "grp_303"

    # שם בעברית בלבד: אין מה לגזור — מזהה רץ.
    assert admin.post("/api/console/groups",
                      json={"label": "כיתת סייבר", "role": "classroom",
                            "id": ""}).status_code == 200
    groups = {g["label"]: g["id"] for g in admin.get("/api/console/groups").json()}
    assert groups["כיתת סייבר"].startswith("grp_CLASS")

    # שם שחוזר על עצמו לא דורס מזהה קיים — מקבל סיומת.
    assert admin.post("/api/console/groups",
                      json={"label": "303 ערב", "role": "classroom"}).status_code == 200
    groups = {g["label"]: g["id"] for g in admin.get("/api/console/groups").json()}
    assert groups["303 ערב"] == "grp_303_-2" or groups["303 ערב"].startswith("grp_303")

    # מזהה מפורש לא חוקי — עדיין נדחה בקול רם, לא מנוחש.
    assert admin.post("/api/console/groups",
                      json={"id": "grp_עברית", "label": "כיתה",
                            "role": "classroom"}).status_code == 400


def test_machine_write_endpoints_are_admin_only(server):
    deploy = server["deploy"]
    assert deploy.post("/api/console/machines",
                       json={"mac": "aa:bb:cc:00:00:09", "name": "01",
                             "group_id": "grp_LAB1"}).status_code == 403
    assert deploy.put("/api/console/machines/aa:bb:cc:00:00:09",
                      json={"name": "02"}).status_code == 403


# --- היומן מדבר עברית --------------------------------------------------------


def test_journal_resolves_names_and_speaks_hebrew(server):
    admin = server["admin"]
    ids = setup_classroom(server)
    admin.post("/api/console/sessions",
               json={"group_id": ids["group"], "image_id": "img_7f3a91",
                     "prefix": "LAB1", "expected_clients": 2})

    rows = admin.get("/api/console/journal").json()
    by_event = {r["event"]: r for r in rows}

    opened = by_event["session_open"]
    assert opened["label"] == "סבב נפתח"
    assert "Office 2024 Standard" in opened["text"]     # שם, לא מזהה
    assert "כיתה LAB1" in opened["text"]                 # קבוצה, לא grp_
    assert "ses_" not in opened["text"]

    imported = by_event["mac_import"]
    assert "נשמרו 2" in imported["text"]
    assert "כיתה LAB1" in imported["text"]


# --- Linux בספרייה: אזרח שווה (אפיון סעיף 14) --------------------------------


def test_a_linux_image_with_swap_is_served_like_any_other(server, images_root):
    """swap מתועד במניפסט בלי קובץ — הספרייה מקבלת את זה, לא נופלת עליו."""
    write_image(images_root, MANIFEST_LINUX)
    listing = server["admin"].get("/api/console/images").json()
    mine = next(m for m in listing if m["id"] == MANIFEST_LINUX["id"])
    assert mine["os"] == "linux"
    assert mine["partitions"] == 3
    # אימג' Windows שנקלט לפני שהשדה נוסף עדיין מזוהה — מתפקידי המחיצות.
    office = next(m for m in listing if m["id"] == "img_7f3a91")
    assert office["os"] == "windows"


def test_a_swap_partition_is_never_a_downloadable_file(server, images_root):
    write_image(images_root, MANIFEST_LINUX)
    ok = server["admin"].get(f"/api/v1/images/{MANIFEST_LINUX['id']}/files/p3.linux.pcl.zst")
    assert ok.status_code == 200
    none = server["admin"].get(f"/api/v1/images/{MANIFEST_LINUX['id']}/files/None")
    assert none.status_code == 404


def test_swap_round_trips_through_export_and_import(server, images_root, tmp_path):
    """הורדה כקובץ יחיד והעלאה חזרה — הבדיקה שמאמתת sha256 מדלגת על swap.

    מאז #110 הייבוא מקבל רק מזהה בצורה שהשרת מקצה (`img_` ושש ספרות הקסה).
    נתוני הבדיקה החזיקו פעם `img_lnx001`, שאינו כזה; מאז #1407 גם הספרייה
    דוחה אותו, ו-`MANIFEST_LINUX` עבר למזהה תקין. הנושא כאן הוא ה-swap.
    """
    linux = {**MANIFEST_LINUX, "id": "img_1c0de5"}
    write_image(images_root, linux)
    tar = server["admin"].get("/api/console/images/img_1c0de5/download")
    assert tar.status_code == 200
    (images_root / "img_1c0de5").rename(tmp_path / "moved-away")
    up = server["admin"].post("/api/console/images/upload", content=tar.content)
    assert up.status_code == 200, up.text
    assert (images_root / "img_1c0de5" / "manifest.json").is_file()


def test_an_archive_whose_id_is_not_an_id_is_refused_through_the_console(server, tmp_path):
    """‏#110 מקצה לקצה, דרך המסלול שמשתמש אמיתי עובר בו: ארכיון שהמזהה
    במניפסט שלו אינו בצורת המזהים נדחה ב-400, עם הודעה שאומרת למה."""
    source = tmp_path / "source"
    write_image(source, {**MANIFEST_256, "id": "img_lnx001"})
    raw = b"".join(tar_stream(source / "img_lnx001", "img_lnx001"))
    response = server["admin"].post("/api/console/images/upload", content=raw)
    assert response.status_code == 400
    # ‏#1407 (77557596): בדיקת המזהה עברה ל-`ImageLibrary._validate`, ולכן
    # הסירוב מגיע משם — לפני הבדיקה הנפרדת ב-archive.py.
    assert "image id or name is malformed" in response.json()["detail"]


def test_an_image_still_being_imported_is_offered_to_nobody(server, images_root):
    """‏#71 מקצה לקצה: אזור עבודה בשורש הספרייה אינו נראה בקונסולה ואינו
    מועמד לסבב. אותו מניפסט בדיוק, אחרי שנכנס לספרייה — כן."""
    write_image(images_root / ".import-4b1e77a2", MANIFEST_LINUX)
    mac = setup_classroom(server)["mac1"]

    def offered() -> tuple[set, list]:
        listing = {m["id"] for m in server["admin"].get("/api/console/images").json()}
        answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
        return listing, answer["allowed_images"]

    listed, allowed = offered()
    image_id = MANIFEST_LINUX["id"]
    assert image_id not in listed and image_id not in allowed

    (images_root / ".import-4b1e77a2" / image_id).rename(images_root / image_id)
    listed, allowed = offered()
    assert image_id in listed and image_id in allowed
