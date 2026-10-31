"""קליטה ממחשב כיתה, קשורה ל-MAC — ‏#381, הצד הכותב של #69.

שני חצאים שאסור להפריד ביניהם, וזו כל הסיבה שהם Issue אחד:

* **השער נפתח** — ‏`capture.py` מקבל גם מכונת כיתה. ‏`cloner` נשאר חסום:
  אין לו מערכת מקומית שיש מה לקלוט ממנה (#17), ולכן ההרחבה היא **רשימת
  היתר** ולא הסרת הבדיקה.
* **הקשירה נכתבת** — ה-MAC של המכונה הקולטת יושב **במניפסט** (עיקרון 3:
  הדיסק הוא מקור האמת), והשחזור מסרב אותו לכל מכונה אחרת **בגלוי**
  (עיקרון 5): הודעה שנוקבת בבעלים ובמה שביקשו, לא כישלון שקט.

פתיחת השער לבדה הייתה רגרסיה בטיחותית — אימג' של תחנה אחת שנראה בספרייה
כמו אימג' זהב וניתן לפרוס אותו על כיתה שלמה. לכן שני החצאים נבדקים כאן
זה מול זה, ולא בשני קבצים.

ארבעה מסלולי שחזור נבדקים, כי הכלל הזה הוא **בטיחות** ולא נוחות, ואכיפה
בשלושה מתוך ארבעה היא היעדר אכיפה: משיכת יוניקאסט, סבב כיתה מהקונסולה,
סבב כיתה ממסך התחנה, וסבב חדר השיכפולים.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentlib import server_files

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from test_capture import do_capture, make_task, setup_build_machine

REPO = Path(__file__).resolve().parent.parent
CLONER = "aa:bb:cc:00:00:20"
ADMIN = {"username": "noc", "password": "admin-pass-123"}


def setup_cloner(server, mac: str = CLONER) -> str:
    server["admin"].post("/api/console/machines",
                         json={"mac": mac, "name": "CLONER1",
                               "group_id": "grp_CLONERS"})
    return mac


def captured_manifest(server, images_root: Path, mac: str) -> dict:
    """קליטה שלמה מהמכונה הזו, ומה שהונח בספרייה בסופה."""
    created = make_task(server, mac).json()
    response = do_capture(server, created["id"])
    assert response.status_code == 202, response.text
    path = images_root / created["image_id"] / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- השער: מי רשאי לקלוט -----------------------------------------------------


def test_a_classroom_machine_may_now_be_captured_from(server):
    """מסלול 8: "צריך אותו דבר למחשב כיתה". זה מה שהיה חסום."""
    ids = setup_classroom(server)
    response = make_task(server, ids["mac1"])
    assert response.status_code == 200, response.text
    assert response.json()["image_id"].startswith("img_")


def test_a_cloner_is_still_refused_and_the_message_says_why(server):
    """מכונה בלי מערכת מקומית אין מה לקלוט ממנה (#17). ההרחבה היא
    רשימת היתר — לא "כל מי שאינו build"."""
    response = make_task(server, setup_cloner(server))
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "cloner" in detail, detail
    assert "מערכת מקומית" in detail, detail


def test_an_unregistered_machine_is_still_refused_before_the_role_is_read(server):
    """עיקרון 1 לא זז: ‏MAC שאיננו מכירים אינו מקבל עבודה, גם לא קליטה.
    בלי הבדיקה הזו הרחבת רשימת ההיתר הייתה נמדדת על `machine["role"]`
    של `None`."""
    assert make_task(server, "ff:ff:ff:ff:ff:ff").status_code == 400


# --- הקשירה: מה נכתב לספרייה -------------------------------------------------


def test_the_manifest_of_a_classroom_capture_carries_its_machine(server, images_root):
    """עיקרון 3: הקשירה על הדיסק, בתיקייה שאפשר להעתיק לשרת אחר —
    ולא בטבלה שנשארת מאחור."""
    ids = setup_classroom(server)
    manifest = captured_manifest(server, images_root, ids["mac1"])
    assert manifest.get("machine_mac") == ids["mac1"], sorted(manifest)


def test_a_build_capture_stays_free(server, images_root):
    """אימג' זהב ממחשב בנייה נפרס על כיתה שלמה, כמו תמיד. אילו הקשירה
    הייתה חלה על כולם, ‏#381 היה שובר את כל הפריסה הקיימת."""
    manifest = captured_manifest(server, images_root, setup_build_machine(server))
    assert "machine_mac" not in manifest


def test_the_machine_cannot_declare_its_own_binding(server, images_root):
    """המניפסט מגיע ממכונה ברשת הלימודית. ‏`machine_mac` הוא שער בטיחות,
    ולכן השרת כותב אותו — בדיוק כמו `id` ו-`name` — ומה שהמכונה שלחה
    נמחק. בלי זה, מחשב כיתה היה משחרר את עצמו בשורה אחת ב-JSON."""
    from test_capture import manifest_for

    ids = setup_classroom(server)
    created = make_task(server, ids["mac1"]).json()
    sent = manifest_for()
    sent["machine_mac"] = None                 # "אני חופשי", לטענת המכונה
    assert do_capture(server, created["id"], manifest=sent).status_code == 202

    written = json.loads((images_root / created["image_id"] / "manifest.json")
                         .read_text(encoding="utf-8"))
    assert written.get("machine_mac") == ids["mac1"], sorted(written)


def test_a_machine_that_left_the_registry_produces_a_bound_image(server, images_root):
    """הכיוון הבטוח: אימג' **חופשי** דורש ראיה חיובית שהמכונה היא מחשב
    בנייה. "לא ידענו מה התפקיד" אינו "מותר לכולם" (עיקרון 5)."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    conn = server["ctx"].conn
    conn.execute("DELETE FROM machines WHERE mac = ?", (mac,))
    conn.commit()
    assert do_capture(server, created["id"]).status_code == 202

    written = json.loads((images_root / created["image_id"] / "manifest.json")
                         .read_text(encoding="utf-8"))
    assert written.get("machine_mac") == mac, sorted(written)


# --- האכיפה בשחזור: ארבעת המסלולים -------------------------------------------


def bind(server, images_root: Path, owner: str, image_id: str = "img_7f3a91") -> str:
    """קושר אימג' שכבר בספרייה ל-MAC — כמו שקליטה ממחשב כיתה קושרת אותו.

    הקשירה נכתבת לקובץ ולא ל-DB בכוונה: זה מה שהמסלולים קוראים, וטסט
    שיזריק שורה לטבלה היה עובר גם על קוד שלא מסתכל במניפסט כלל.
    """
    path = images_root / image_id / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["machine_mac"] = owner
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return image_id


def test_a_bound_image_is_refused_to_another_station(server, images_root):
    """המסלול של אשף השחזור (זרימה 13.2). הסירוב **גלוי**, נוקב בבעלים
    ובמי שביקש, ואינו נפילה שקטה לדיסק מקומי."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["anon"].post("/api/v1/agent/pulls", json={
        "mac": ids["mac2"], "image_id": "img_7f3a91", **ADMIN})

    assert response.status_code == 403, response.text
    body = response.json()
    assert body["code"] == "image_bound_to_another_machine"
    assert ids["mac1"] in body["error"], body
    assert ids["mac2"] in body["error"], body


def test_a_bound_image_still_restores_to_its_own_machine(server, images_root):
    """הצד החיובי, וזו כל מטרת הפיצ'ר: "אותו מחשב יקבל בשיעור הבא את
    הדיסק שלו". שער שדוחה הכול אינו שער."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["anon"].post("/api/v1/agent/pulls", json={
        "mac": ids["mac1"], "image_id": "img_7f3a91", **ADMIN})

    assert response.status_code == 200, response.text
    assert response.json()["image_id"] == "img_7f3a91"


def test_an_unbound_image_is_untouched(server, images_root):
    """כל הספרייה שנקלטה עד #381 היא בלי השדה. אילו "אין שדה" היה
    מסתיים בסירוב, ‏#381 היה מכבה את הפריסה בכללותה."""
    ids = setup_classroom(server)
    response = server["anon"].post("/api/v1/agent/pulls", json={
        "mac": ids["mac2"], "image_id": "img_7f3a91", **ADMIN})
    assert response.status_code == 200, response.text


def test_a_bound_image_is_refused_for_a_whole_class_round(server, images_root):
    """סבב מהקונסולה בלי בחירת מחשבים: היעד הוא "כל הקבוצה", ואין ראיה
    חיובית שרק הבעלים יקבל. יעד שאינו רשימה ידועה הוא סירוב."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91"})

    assert response.status_code == 400, response.text
    assert ids["mac1"] in response.json()["detail"]


def test_a_bound_image_is_refused_for_a_round_that_includes_a_stranger(
        server, images_root):
    """בחירת מחשבים שבה הבעלים הוא אחד מכמה — עדיין סירוב. ההיתר דורש
    שאין ביעד איש מלבדו."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "macs": [ids["mac1"], ids["mac2"]]})

    assert response.status_code == 400, response.text
    assert ids["mac2"] in response.json()["detail"]


def test_a_round_of_exactly_its_own_machine_is_allowed(server, images_root):
    """והצד השני: סבב שהיעד שלו הוא בדיוק הבעלים עובר."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "macs": [ids["mac1"]]})

    assert response.status_code == 200, response.text


def test_the_station_screen_is_refused_too(server, images_root):
    """מסך התחנה פותח סבב באותה זכות בדיוק (זרימה 13.3). שער שקיים
    בקונסולה בלבד הוא שער שאפשר לעקוף בקריאה אחת."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])

    response = server["anon"].post("/api/v1/agent/sessions", json={
        **ADMIN, "mac": ids["mac1"], "group_id": ids["group"],
        "image_id": "img_7f3a91"})

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "image_bound_to_another_machine"
    assert ids["mac1"] in response.json()["error"]


def test_the_cloning_room_is_refused_too(server, images_root):
    """מגירות בחדר השיכפולים יותקנו במכונות שאיש עוד אינו יודע מי הן —
    אין כאן רשימת יעדים בכלל, ולכן אימג' קשור אינו נשפך עליהן."""
    setup_cloner(server)
    bind(server, images_root, "b4:2e:99:07:1a:c4")

    response = server["deploy"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2})

    assert response.status_code in (400, 409), response.text
    assert "b4:2e:99:07:1a:c4" in response.text


def test_the_refusal_is_journalled_in_hebrew(server, images_root):
    """סירוב שאיש לא רואה הוא כישלון שקט. הקונסולה מציגה יומן בעברית,
    ואירוע בלי תרגום מופיע בה כמזהה באנגלית."""
    ids = setup_classroom(server)
    bind(server, images_root, ids["mac1"])
    server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91"})

    rows = {r["event"]: r for r in
            server["admin"].get("/api/console/journal").json()}
    assert "session_image_bound" in rows, sorted(rows)
    assert rows["session_image_bound"]["label"] == \
        "פתיחת סבב נדחתה — האימג' קשור למכונה אחרת"


# --- הנעילה: מסלול שחזור חמישי אינו נולד בלי החלטה ---------------------------


#: המודולים שפותחים שחזור, ולכן חייבים לשאול את `restore_refusal`.
#: ‏`pulls.py` פותח את ה-session, אבל השער יושב במי שקורא לו (`api.py`)
#: — שם המניפסט כבר בידיים, ושם התשובה חוזרת למי שביקש.
#: ‏`direct.py` (‏#715) פותח גל מדיסק חי — הקשירה נבדקת כשהמניפסט מגיע
#: (‏`put_manifest`), מול המכונות שנבחרו כיעד.
RESTORE_OPENERS = {"api.py", "console_api.py", "direct.py", "room.py", "station.py"}


def test_every_module_that_opens_a_restore_consults_the_binding():
    """ארבעה אתרי אכיפה הם ארבעה מקומות לשכוח בהם, וזה בדיוק הדפוס
    ש-#54 שילם עליו. הנעילה נגזרת מהקוד ואינה רשימה שמישהו יתחזק:
    המודול החמישי שיפתח סבב ייתפס כאן ביום שהוא נכתב, ולא מול כיתה.
    """
    server_dir = REPO / "server"
    paths = server_files(server_dir)
    opens = {
        path.name for path in paths
        if re.search(r"store\.open\(|pulls\.open_pull\(",
                     path.read_text(encoding="utf-8"))
    } - {"sessions.py", "pulls.py"}
    assert opens == RESTORE_OPENERS, (
        f"מסלול שחזור שאינו ברשימה, או שנעלם ממנה: {opens ^ RESTORE_OPENERS}")
    missing = [str(path.relative_to(server_dir)) for path in paths if path.name in opens
               if "restore_refusal" not in
               path.read_text(encoding="utf-8")]
    assert not missing, f"פותח שחזור שאינו בודק את הקשירה: {missing}"


def test_the_interfaces_document_declares_the_field():
    """‏`docs/interfaces.md` הוא מקור האמת למה שעובר בין רכיבים — שדה
    חדש במניפסט שאינו רשום שם אינו קיים מבחינת הרכיב הבא."""
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "machine_mac" in text
    assert "#381" in text
