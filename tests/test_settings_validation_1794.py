"""Regression coverage for #1794: generic settings are validated before storage."""

import pytest

from server.console_api import WRITE_SETTINGS


CASES = [
    ("recovery_require_login", "yes", "true", "שחזור מהשרת בלי סבב דורש כניסה"),
    ("mfa_required_for_admins", 1, "false", "חיוב אימות דו-שלבי למנהלים"),
    ("class_deploy_enabled", "yes", "true", "הפצה לכיתה ממסך התחנה"),
    ("update_enabled", "no", "true", "עדכון גרסה מהקונסולה"),
    ("identity_check", "TRUE", "false", "אימות מחשבים לפי כתובת ה-DHCP"),
    ("learned_rate_enabled", "0", "true", "קצב שידור אוטומטי לפי הכוננים בחדר"),
    ("session_wait_seconds", "29", "30", "המתנה מהמצטרף האחרון"),
    ("console_idle_seconds", "61", "60", "ניתוק בחוסר פעילות"),
    ("crc_warn_threshold", "1000001", "1000000", "סף CRC"),
    ("min_free_gb", "501", "500", "מינימום מקום פנוי"),
    ("cloner_beep", "yes", "true", "צליל בסיום סבב שיכפול"),
    ("readback_full", "yes", "true", "אימות מלא אחרי הכתיבה"),
    ("igmp_querier", "yes", "true", "IGMP querier"),
    ("lost_wait_seconds", "179", "180", "המתנה למחשב שהתנתק באמצע שיכפול"),   # #2056
    ("gui_idle_poweroff_seconds", "59", "60", "כיבוי כשאין פעילות"),   # #2059
    ("gui_end_poweroff_seconds", "9", "10", "ספירה לכיבוי בסוף משימה"),   # #2059
    ("intersite_rate_cap_mbps", "100001", "100000", "הגבלת קצב בין אתרים"),
    ("auto_reboot", "monthly", "daily", "הפעלה מחדש אוטומטית"),
    ("auto_reboot_time", "24:00", "23:59", "שעת הפעלה מחדש"),
    ("auto_reboot_weekday", "7", "6", "יום הפעלה מחדש"),
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
