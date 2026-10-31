"""שני צדי השרת של "הכול גואי" (נדב, 11/10).

1. ‏hello עם ``gui_failed`` — הסוכן מדווח שהגואי לא עלה, לפני שהוא מאתחל
   לדיסק המקומי. נרשם ביומן (``gui_failed``, "הגואי לא עלה") עם ה-MAC,
   שהקונסולה מתרגמת לשם המכונה. שדה פגום או מכונה לא רשומה — כלום.
2. ‏``/agent/pulls`` מקבל את סשן הקונסולה של הגואי (ה-cookie שהגשר מעביר)
   כהרשאה, בלי סיסמה שנייה, ופותח את המשיכה בשם המשתמש — כך השחזור מקושר
   לקונסולה. ‏cookie פסול / של החלפת סיסמה אינו סשן: הדרך הישנה מחליטה.

בקרה שלילית: על הקומיט שלפני (8eda4eff) — אין רשומה ביומן, והמשיכה עם
ה-cookie בלבד מחוץ לווילן מקבלת 401.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: השרת מסרב לכיתות ב-v1. העזר `second_class` (מ-test_server_streams)
#: רושם את המכונות בקבוצת כיתה — ולכן מדליקים את היכולת, כמו שם.
pytestmark = pytest.mark.usefixtures("classrooms_on")

from conftest import hello_body  # noqa: E402
from test_server_streams import OFF_VLAN, VLAN, overview, second_class  # noqa: E402


def journal_rows(server, event: str) -> list[dict]:
    return [r for r in server["admin"].get("/api/console/journal").json()
            if r["event"] == event]


def hello(server, body: dict):
    return server["anon"].post(VLAN + "/api/v1/agent/hello", json=body)


GUI_FAILED = {"screen": "build", "boot_next": "set", "detail": "Boot0001"}


def test_a_gui_failure_reaches_the_journal_with_the_machine(server):
    lab2 = second_class(server)
    body = {**hello_body(lab2["mac1"]), "joining": False, "gui_failed": GUI_FAILED}
    assert hello(server, body).status_code == 200
    rows = journal_rows(server, "gui_failed")
    assert len(rows) == 1, rows
    from server.console.journal_he import EVENTS_HE, severity
    assert EVENTS_HE["gui_failed"] == "הגואי לא עלה"
    assert severity("gui_failed") == "warn"


def test_a_boot_next_that_failed_says_why(server):
    lab2 = second_class(server)
    body = {**hello_body(lab2["mac1"]), "joining": False,
            "gui_failed": {"screen": "recovery", "boot_next": "failed",
                           "detail": "no local-disk Boot#### entry"}}
    assert hello(server, body).status_code == 200
    raw = server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'gui_failed'").fetchall()
    assert len(raw) == 1
    detail = raw[0][0]
    assert lab2["mac1"] in detail
    assert "לא הצלחנו לכוון" in detail and "no local-disk Boot#### entry" in detail


@pytest.mark.parametrize("bad", [
    "yes", {"screen": "kiosk", "boot_next": "set"}, {"screen": "build", "boot_next": "maybe"},
    {"screen": "build", "boot_next": "set", "detail": 7},
])
def test_a_malformed_report_is_ignored(server, bad):
    lab2 = second_class(server)
    body = {**hello_body(lab2["mac1"]), "joining": False, "gui_failed": bad}
    assert hello(server, body).status_code == 200
    assert journal_rows(server, "gui_failed") == []


def test_an_unregistered_machine_is_not_journaled(server):
    second_class(server)
    body = {**hello_body("b4:2e:99:07:9f:ff"), "joining": False, "gui_failed": GUI_FAILED}
    assert hello(server, body).status_code == 200
    assert journal_rows(server, "gui_failed") == []


# --- 2: סשן הגואי מאשר משיכה ---------------------------------------------------


def console_cookie(server) -> str:
    token = server["deploy"].cookies.get("imagesrv_session")
    assert token, "the deploy client holds no console session"
    return token


def gui_pull(server, mac: str, cookie: str | None, base: str = OFF_VLAN):
    headers = {"Cookie": f"imagesrv_session={cookie}"} if cookie else {}
    return server["anon"].post(base + "/api/v1/agent/pulls",
                               json={"mac": mac, "image_id": "img_7f3a91",
                                     "username": "", "password": ""},
                               headers=headers)


def test_the_gui_session_authorizes_a_pull_where_a_login_is_required(server):
    """מחוץ לווילן הכניסה נדרשת תמיד (#42). ה-cookie של הגואי מספיק, והמשיכה
    נפתחת בשם המשתמש של הגואי."""
    lab2 = second_class(server)
    response = gui_pull(server, lab2["mac1"], console_cookie(server))
    assert response.status_code == 200, response.text
    sid = response.json()["id"]
    row = server["ctx"].conn.execute(
        "SELECT opened_by FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row is not None and row[0] == "labtech"
    assert len(overview(server)["pulls"]) == 1


def test_no_session_and_no_password_is_still_refused(server):
    lab2 = second_class(server)
    response = gui_pull(server, lab2["mac1"], None)
    assert response.status_code == 401
    assert overview(server)["pulls"] == []


def test_a_forged_session_is_not_a_session(server):
    lab2 = second_class(server)
    response = gui_pull(server, lab2["mac1"], "labtech|admin|9999999999|:0|deadbeef")
    assert response.status_code == 401
    assert overview(server)["pulls"] == []


def test_a_password_change_session_does_not_authorize_work(server):
    from server.console import auth
    lab2 = second_class(server)
    token = auth.issue(server["ctx"].conn, "labtech", "deploy", purpose=auth.PURPOSE_PWCHANGE)
    response = gui_pull(server, lab2["mac1"], token)
    assert response.status_code == 401
    assert overview(server)["pulls"] == []
