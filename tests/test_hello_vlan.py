"""hello מחוץ לווילן ההפצה דורש כניסה תמיד (issue #42).

ההקלה "סבב פתוח = בלי סיסמה" נועדה לכיתה שיושבת על וילן ההפצה, שם
הגישה הפיזית היא ממילא השמירה. מכונה רשומה שפונה מרשת אחרת — משרד,
מעבדה, וילן ניהול — קיבלה בדיוק את אותו אשף שחזור (ESC ← תחנה בודדת
← ERASE) בלי שום הזדהות.

המקור לכתובת שעליה הבקשה התקבלה הוא ‎scope["server"] — ה-sockname של
החיבור, שאותו uvicorn ממלא — בדיוק כמו ב-#39. מכוון: *לא* כותרת Host,
שהיא קלט של הלקוח.

ה-TestClient ממלא את ה-scope מכתובת ה-URL של הבקשה, ולכן בקשה לכתובת
מוחלטת אחרת היא בדיוק תרחיש "הגעתי על כתובת אחרת".
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom

#: הכתובת שאיתה נוצר השרת ב-conftest — וילן ההפצה.
VLAN = "http://10.44.12.10:8080"
#: כתובת מקומית אחרת של אותו שרת: הרגל שלו ברשת המכללה.
OFF_VLAN = "http://10.10.10.8:8080"


def hello_at(server, base: str | None, mac: str) -> dict:
    """hello שמגיע על כתובת מקומית מסוימת (או ברירת המחדל של TestClient)."""
    url = "/api/v1/agent/hello" if base is None else base + "/api/v1/agent/hello"
    response = server["anon"].post(url, json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def open_round(server) -> dict:
    """קבוצה עם שתי מכונות וסבב פתוח שלה — ההקלה במלוא תוקפה."""
    ids = setup_classroom(server, 2)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    )
    assert response.status_code == 200
    return ids


def set_require_login(server, value: str) -> None:
    assert server["admin"].post(
        "/api/console/settings", json={"recovery_require_login": value}
    ).status_code == 200


def test_open_round_from_another_network_still_demands_a_login(server):
    ids = open_round(server)

    answer = hello_at(server, OFF_VLAN, ids["mac1"])

    assert answer["ui"]["require_login"] is True
    # הסבב עצמו לא נגזל ממנה — רק הכניסה נדרשת (השאלה הפתוחה בישיו).
    assert answer["session"]["state"] == "open"


def test_open_round_on_the_deployment_vlan_keeps_the_relief(server):
    ids = open_round(server)

    answer = hello_at(server, VLAN, ids["mac1"])

    assert answer["ui"]["require_login"] is False


def test_setting_off_does_not_open_a_station_on_another_network(server):
    ids = setup_classroom(server)
    set_require_login(server, "false")

    assert hello_at(server, VLAN, ids["mac1"])["ui"]["require_login"] is False
    assert hello_at(server, OFF_VLAN, ids["mac1"])["ui"]["require_login"] is True


def test_an_unregistered_mac_from_another_network_also_demands_a_login(server):
    set_require_login(server, "false")

    assert hello_at(server, OFF_VLAN, "aa:aa:aa:aa:aa:aa")["ui"]["require_login"] is True


def test_a_scope_that_says_nothing_useful_behaves_exactly_as_before(server):
    """ברירת המחדל של TestClient היא ("testserver", 80) — לא כתובת.

    כל scope שלא ניתן להשוואה חוזר להתנהגות הישנה; אסור ש-hello ייכשל
    או יתקשח בגלל מידע חסר.
    """
    ids = open_round(server)

    assert hello_at(server, None, ids["mac1"])["ui"]["require_login"] is False


@pytest.mark.parametrize(
    "scope, base",
    [
        ({}, VLAN),                                  # אין מפתח server בכלל
        ({"server": None}, VLAN),
        ({"server": ("testserver", 80)}, VLAN),      # שם ולא כתובת
        ({"server": ("10.10.10.8",)}, VLAN),         # לא זוג
        ({"server": "10.10.10.8:8080"}, VLAN),       # לא tuple
        ({"server": ("999.1.1.1", 8080)}, VLAN),     # לא כתובת תקינה
        ({"server": ("10.10.10.8", 8080)}, None),    # אין כתובת שרת מוגדרת
        ({"server": ("10.10.10.8", 8080)}, "http://imagesrv.local:8080"),  # שם בתצורה
        ({"server": ("10.44.12.10", 9999)}, VLAN),   # אותה כתובת, פורט אחר
    ],
    ids=["absent", "none", "hostname", "short", "string", "bad-ip",
         "no-base", "named-base", "other-port"],
)
def test_only_a_comparable_pair_can_mark_a_request_as_off_vlan(scope, base):
    from server.agent.hello import off_deploy_vlan

    assert off_deploy_vlan(scope, base) is False


def test_a_different_local_address_is_off_vlan():
    from server.agent.hello import off_deploy_vlan

    assert off_deploy_vlan({"server": ("10.10.10.8", 8080)}, VLAN) is True
