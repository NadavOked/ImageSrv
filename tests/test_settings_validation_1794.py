"""Regression coverage for #1794: generic settings are validated before storage."""

import pytest

from server.console.api import WRITE_SETTINGS


CASES = [
    ("recovery_require_login", "yes", "true", "שחזור מהשרת בלי סבב דורש כניסה"),
    ("mfa_required_for_admins", 1, "false", "חיוב אימות דו-שלבי למנהלים"),
    ("class_deploy_enabled", "yes", "true", "הפצה לכיתה ממסך התחנה"),
    ("update_enabled", "no", "true", "עדכון גרסה מהקונסולה"),
    ("identity_check", "TRUE", "false", "אימות מחשבים לפי כתובת ה-DHCP"),
    ("learned_rate_enabled", "0", "true", "קצב שידור אוטומטי לפי הכוננים בחדר"),
    ("learning_enabled", "yes", "true", "למידה אוטומטית של הרשת והמחשבים"),   # #2028
    ("session_wait_seconds", "29", "30", "המתנה מהמצטרף האחרון"),
    ("console_idle_seconds", "61", "60", "ניתוק בחוסר פעילות"),
    ("crc_warn_threshold", "1000001", "1000000", "סף CRC"),
    ("min_free_gb", "501", "500", "מינימום מקום פנוי"),
    ("unregistered_autoboot_seconds", "601", "600", "הערך חייב להיות בין 0 ל-600 שניות"),   # #1802
    ("cloner_beep", "yes", "true", "צלילי המשכפל ומחשב הבנייה"),
    ("hdd_support", "yes", "true", "תמיכה ב-HDD"),   # #3268
    ("readback_full", "yes", "true", "אימות מלא אחרי הכתיבה"),
    ("stream_engine", "fast", "chunk", "מנוע שידור"),   # #2533
    ("igmp_querier", "yes", "true", "IGMP querier"),
    ("lost_wait_seconds", "179", "180", "המתנה למחשב שהתנתק באמצע שיכפול"),   # #2056
    ("gui_idle_poweroff_seconds", "59", "60", "כיבוי כשאין פעילות"),   # #2059
    ("gui_end_poweroff_seconds", "9", "10", "ספירה לכיבוי בסוף משימה"),   # #2059
    ("drawer_disk_wait_seconds", "29", "120", "המתנה של מגירה לדיסק"),   # #3658 (נדב 18/11)
    ("drawer_net_wait_seconds", "901", "120", "המתנה של מגירה לרשת"),   # #3658 (נדב 18/11)
    ("interrupt_ask_minutes", "4", "5", "המתנה להכרעה על משכפל שכבה באמצע סבב"),   # #2528
    ("intersite_rate_cap_mbps", "100001", "100000", "הגבלת קצב בין אתרים"),
    ("auto_reboot", "monthly", "daily", "הפעלה מחדש אוטומטית"),
    ("auto_reboot_time", "24:00", "23:59", "שעת הפעלה מחדש"),
    ("auto_reboot_weekday", "7", "6", "יום הפעלה מחדש"),
    ("clone_name_pad", "5", "3", "ריפוד שמות המחשב"),   # #2786
    ("clone_name_instructor", "IN-S", "TEACH", "סיומת המרצה"),   # #2786
    ("brain_veto_mode", "on", "enforce", "המוח: וטו על פעולות הרסניות"),   # #3064
    ("brain_net_ceiling_mbps", "9", "900", "המוח: תקרת רשת ההפצה"),   # #3064 (שלב 3)
    ("brain_drawer_hold", "enforce", "on", "המוח: השלמה מעכבת רק את המגירה שנפגעה"),   # #2023
    ("brain_verify_repair", "yes", "on", "אימות והורדה של חתיכות שלא תאמו"),   # #3064 (B30)
    ("tail_shrink_enabled", "yes", "false", "הקטנת המחיצה האחרונה ביעד קטן"),   # #3262
    ("brain_server_cap", "yes", "false", "המוח: הגבלת קצב הגל לתקרת הקריאה של דיסק הספרייה"),   # #3464
    ("brain_cpu_gate", "on", "false", "המוח: שער מעבד לפני פתיחת גל"),   # #3064 (K12)
    ("wiring_net_margin", "0.5", "1.5", "מפת החיבורים: פי כמה מהיר מהיציאה"),   # #3600
    ("brain_gate_min_samples", "0", "50", "המוח: שער מצל לאכיפה, מינימום דגימות"),   # #3563
    ("brain_gate_min_agreement", "101", "95", "המוח: שער מצל לאכיפה, מינימום הסכמה"),   # #3563
]


def test_every_write_setting_has_an_invalid_and_valid_case():
    assert {case[0] for case in CASES} == WRITE_SETTINGS


@pytest.mark.parametrize("key,bad,good,label", CASES)
def test_each_setting_rejects_an_invalid_value_without_changing_storage(
        server, key, bad, good, label):
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={key: good}).status_code == 200
    before = admin.get("/api/console/settings").json()[key]

    response = admin.post("/api/console/settings", json={key: bad})

    assert response.status_code == 400
    assert label in response.json()["detail"]
    assert admin.get("/api/console/settings").json()[key] == before


@pytest.mark.parametrize("bad", ["10", "abc", "-5", ""])
def test_bad_session_wait_is_rejected_and_the_stored_value_is_unchanged(server, bad):
    admin = server["admin"]
    assert admin.post("/api/console/settings",
                      json={"session_wait_seconds": "300"}).status_code == 200

    response = admin.post("/api/console/settings", json={"session_wait_seconds": bad})

    assert response.status_code == 400
    assert "המתנה מהמצטרף האחרון" in response.json()["detail"]
    assert admin.get("/api/console/settings").json()["session_wait_seconds"] == "300"


def test_unknown_setting_is_still_rejected_without_changing_storage(server):
    admin = server["admin"]
    before = admin.get("/api/console/settings").json()

    response = admin.post("/api/console/settings", json={"not_a_setting": "true"})

    assert response.status_code == 400
    assert response.json()["detail"] == "הגדרה לא מוכרת: not_a_setting"
    assert admin.get("/api/console/settings").json() == before


def test_a_json_boolean_from_an_api_client_is_normalized(server):
    """לקוח API ששולח true/false בוליאני (לא מחרוזת) ממשיך לעבוד -- נשמר "true"/"false"."""
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={"update_enabled": True}).status_code == 200
    assert admin.get("/api/console/settings").json()["update_enabled"] == "true"
    assert admin.post("/api/console/settings", json={"update_enabled": False}).status_code == 200
    assert admin.get("/api/console/settings").json()["update_enabled"] == "false"
