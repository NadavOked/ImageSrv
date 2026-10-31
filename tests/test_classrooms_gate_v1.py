"""הכרעת נדב 11/10: כיתות מסורבות **בשרת** כש-`capabilities.classrooms()` כבוי.

עד כאן v1 רק *הסתיר* כיתות (#1081) — בקונסולה, בתפריט מחשב הבנייה ובגואי.
השרת עצמו עדיין יצר קבוצת כיתה ופתח סבב כיתה לכל מי ששלח את הבקשה
ישירות (ממצא סקר השאריות, 11/10). כאן נבדק שהשרת מסרב בשמו:

- ‏`POST /api/console/groups` עם ‏role=classroom → ‏409 עם סיבה; ‏build/cloner
  ממשיכים לעבוד.
- ‏`POST /api/console/sessions` (פתיחת סבב כיתה) → ‏409 עם סיבה.
- ‏allowlist הקיוסק על פורט הסוכן: ‏`GET /api/console/groups` (רשימת הכיתות
  של מסך התחנה שהוסר) אינו מוגש; ‏`POST /api/console/sessions` מוגש אך
  מסרב (זה אותו route של הקונסולה, והשער יושב בו).

‏start/close של סבב **אינם** נחסמים: גל חדר השיכפולים הוא סבב רגיל לכל
דבר, וסגירתו עוברת באותו endpoint (`test_room_wave_recovery.py`).

כל בדיקה של "דלוק" מדליקה את היכולת ב-monkeypatch — כך v1.2 ממשיך לעבוד.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.core import capabilities
from server.app import create_agent_app

from conftest import setup_classroom

CLASSROOM = {"id": "grp_LAB9", "label": "כיתה LAB9", "role": "classroom"}


def _ids(admin) -> set[str]:
    return {g["id"] for g in admin.get("/api/console/groups").json()}


def test_classroom_group_is_refused_while_classrooms_are_off(server):
    admin = server["admin"]
    assert capabilities.classrooms() is False, "v1 — הבדיקה מניחה כיתות כבויות"
    response = admin.post("/api/console/groups", json=CLASSROOM)
    assert response.status_code == 409, response.text
    assert "כיתות" in response.json()["detail"]
    assert "grp_LAB9" not in _ids(admin), "הקבוצה נוצרה למרות הסירוב"


def test_cloner_group_still_works_while_classrooms_are_off(server):
    admin = server["admin"]
    response = admin.post("/api/console/groups",
                          json={"id": "grp_CL2", "label": "שיכפול 2", "role": "cloner"})
    assert response.status_code == 200, response.text
    assert "grp_CL2" in _ids(admin)


def test_classroom_group_works_when_classrooms_are_on(server, monkeypatch):
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    admin = server["admin"]
    assert admin.post("/api/console/groups", json=CLASSROOM).status_code == 200
    assert "grp_LAB9" in _ids(admin)


def _open(client, ids):
    return client.post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "prefix": "LAB1", "expected_clients": 2})


def test_class_round_is_refused_while_classrooms_are_off(server, monkeypatch):
    # כיתה תקינה ואימג' קיים (נבנו כש-v1.2) — כך שהסירוב הוא של השער
    # בלבד ולא של גוף בקשה פגום.
    ids = setup_classroom(server)
    monkeypatch.setattr(capabilities, "CLASSROOMS", False)
    for client in (server["admin"], server["deploy"]):
        response = _open(client, ids)
        assert response.status_code == 409, response.text
        assert "כיתות" in response.json()["detail"]
    assert server["admin"].get("/api/console/overview").json()["session"] is None


def test_class_round_opens_when_classrooms_are_on(server):
    ids = setup_classroom(server)          # מדליק את היכולת (v1.2)
    response = _open(server["deploy"], ids)
    assert response.status_code == 200, response.text


def test_kiosk_does_not_serve_the_class_list_while_classrooms_are_off(server):
    rt = server["app"].state.runtime
    kiosk = TestClient(create_agent_app(rt))
    assert kiosk.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    assert kiosk.get("/api/console/groups").status_code == 404
    # ‏POST /sessions נשאר ב-allowlist ומסרב בשער שלו.
    response = kiosk.post("/api/console/sessions",
                          json={"group_id": "grp_LAB1", "image_id": "img_7f3a91"})
    assert response.status_code == 409, response.text


def test_kiosk_serves_the_class_list_when_classrooms_are_on(server, monkeypatch):
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    rt = server["app"].state.runtime
    kiosk = TestClient(create_agent_app(rt))
    assert kiosk.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    assert kiosk.get("/api/console/groups").status_code == 200


# --- נתיבי הסוכן (‏station.py, פורט 8080) --------------------------------------
#
# הכרעת נדב 11/10, המשך: "השרת מסרב לפקודות כיתות כל עוד הכיתות כבויות" —
# כולל נתיבי הסוכן, **בלי תלות** במתג ‏`class_deploy_enabled` (#880).

from test_station import open_body, station  # noqa: E402,F401 — fixture


def _agent_class_calls(client, ids):
    return {
        "groups": client.get("/api/v1/agent/groups"),
        "machines": client.get(f"/api/v1/agent/groups/{ids['group']}/machines"),
        "open": client.post("/api/v1/agent/sessions", json=open_body(ids["mac1"])),
    }


def test_agent_class_routes_are_refused_while_classrooms_are_off(station, monkeypatch):  # noqa: F811
    # כיתה רשומה והמתג #880 **דלוק** (‏setup_classroom) — הסירוב הוא של
    # היכולת בלבד, לא של המתג ולא של נתונים חסרים.
    ids = setup_classroom(station)
    monkeypatch.setattr(capabilities, "CLASSROOMS", False)
    for name, response in _agent_class_calls(station["anon"], ids).items():
        assert response.status_code == 409, (name, response.text)
        assert response.json()["code"] == "classrooms_off", name
    assert station["woken"] == []
    assert station["ctx"].conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0


def test_agent_round_refusal_comes_before_the_password(station, monkeypatch):  # noqa: F811
    """אותו לקח של #1282: נתיב שאין מאחוריו פתיחה לא בודק סיסמאות."""
    ids = setup_classroom(station)
    monkeypatch.setattr(capabilities, "CLASSROOMS", False)
    wrong = station["anon"].post("/api/v1/agent/sessions",
                                 json=open_body(ids["mac1"], password="wrong"))
    right = station["anon"].post("/api/v1/agent/sessions", json=open_body(ids["mac1"]))
    assert wrong.status_code == right.status_code == 409
    assert wrong.json() == right.json()
    assert station["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM login_attempts").fetchone()["n"] == 0


def test_agent_class_routes_work_when_classrooms_are_on(station):  # noqa: F811
    ids = setup_classroom(station)                         # מדליק את היכולת (v1.2)
    calls = _agent_class_calls(station["anon"], ids)
    assert calls["groups"].status_code == 200
    assert calls["machines"].status_code == 200
    assert calls["open"].status_code == 200, calls["open"].text


def test_agent_active_session_view_is_not_a_class_route(station):  # noqa: F811
    """‏/sessions/active מציג גם גל של חדר השיכפולים — הוא נשאר פתוח ב-v1."""
    assert capabilities.classrooms() is False
    assert station["anon"].get("/api/v1/agent/sessions/active").json() == {"session": None}
