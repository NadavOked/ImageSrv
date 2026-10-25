"""‏#977 §4 — "אתחול מרחוק" ממגירת המחשב (‏`POST /api/console/machines/{mac}/reboot`).

אותו ערוץ כמו כיבוי החדר (#980): השרת **רושם בקשה**, והיא נוסעת בתשובת
ה-hello הבא של המכונה (‏`power_action: "reboot"`) — בלי polling חדש ובלי
‏`reboot` מהשרת. הסוכן מאתחל רק כשהוא ממתין (‏`poll_sleep`), ולכן מכונה
שעובדת עכשיו מסורבת כבר בשרת. מה שנבדק כאן: הבקשה נמסרת פעם אחת ורק
למכונה שנבחרה, האישור (שם המחשב, נשלח מחלון האישור — #1270), הסירובים,
והמיגרציה של ה-CHECK בטבלת הבקשות.
"""

from __future__ import annotations

import sqlite3

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: השרת מסרב לכיתות ב-v1. הבדיקות כאן יוצרות קבוצת
#: כיתה ובודקות את זרימת v1.2 — ולכן מדליקות את היכולת (`classrooms_on`).
pytestmark = pytest.mark.usefixtures("classrooms_on")

from fastapi.testclient import TestClient

BUILD = "b4:2e:99:07:1a:c4"
CLONER = "aa:bb:cc:00:00:21"
STATION = "aa:bb:cc:00:00:31"


@pytest.fixture()
def server(tmp_path, images_root):
    from server import users
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    assert admin.post("/api/console/groups", json={
        "id": "grp_LAB1", "label": "כיתה 1", "role": "classroom"}).status_code == 200
    for mac, name, group in ((BUILD, "BUILD-1", "grp_BUILD"), (CLONER, "CLONER-1", "grp_CLONERS"),
                             (STATION, "01", "grp_LAB1")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app)}
    ctx.sender.stop()


def hello(server, mac: str) -> dict:
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def journal(server, event: str) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def reboot(server, mac: str, name: str | None, who: str = "admin"):
    body = {} if name is None else {"confirm_name": name}
    return server[who].post(f"/api/console/machines/{mac}/reboot", json=body)


@pytest.mark.parametrize("mac, name", [(BUILD, "BUILD-1"), (CLONER, "CLONER-1")])
def test_reboot_rides_the_next_hello_of_that_machine_once(server, mac, name):
    response = reboot(server, mac, name)
    assert response.status_code == 200, response.text
    assert response.json() == {"requested": 1, "ttl_s": 120}
    other = CLONER if mac == BUILD else BUILD
    assert "power_action" not in hello(server, other)
    assert hello(server, mac)["power_action"] == "reboot"
    assert "power_action" not in hello(server, mac)      # פעם אחת
    assert journal(server, "machine_reboot") == [mac]
    assert journal(server, "power_delivered") == [f"{mac} reboot"]
    shown = {r["event"]: r["text"] for r in server["admin"].get("/api/console/journal").json()}
    # המכונה בשמה (עם הקבוצה, כמו בכל היומן), והפעולה בעברית — לא "reboot".
    assert shown["power_delivered"].startswith(name) and shown["power_delivered"].endswith(" · אתחול")
    assert shown["machine_reboot"].startswith(name) and mac not in shown["machine_reboot"]


@pytest.mark.parametrize("name", [None, "", "BUILD", "BUILD-1 "])
def test_reboot_without_the_machine_name_is_refused(server, name):
    assert reboot(server, BUILD, name).status_code == 400
    assert "power_action" not in hello(server, BUILD)
    assert journal(server, "machine_reboot") == []


def test_a_classroom_station_is_v2(server):
    assert reboot(server, STATION, "01").status_code == 403
    assert "power_action" not in hello(server, STATION)


def test_an_unknown_or_bad_mac(server):
    assert reboot(server, "aa:bb:cc:dd:ee:ff", "x").status_code == 404
    assert reboot(server, "nope", "x").status_code == 400


def test_a_machine_with_an_open_task_is_not_rebooted(server):
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, created_by, created_at, updated_at)"
        " VALUES ('tsk_1', ?, 'capture', 'sda', 'img_1', 'x', 'noc', 't', 't')", (BUILD,))
    conn.commit()
    assert reboot(server, BUILD, "BUILD-1").status_code == 409


def test_a_cloner_is_not_rebooted_while_a_room_round_is_writing(server):
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
        " VALUES ('rr_1', 'img_1', 2, 'active', 'noc', '2026-10-11T08:00:00+00:00')")
    conn.commit()
    assert reboot(server, CLONER, "CLONER-1").status_code == 409
    # מחשב הבנייה אינו בגל של החדר.
    assert reboot(server, BUILD, "BUILD-1").status_code == 200


def test_deploy_and_anonymous_cannot_reboot(server):
    for who in ("deploy", "anon"):
        assert reboot(server, BUILD, "BUILD-1", who).status_code in (401, 403), who
    assert "power_action" not in hello(server, BUILD)


def test_an_old_database_accepts_a_reboot_request_and_keeps_pending_rows(tmp_path):
    """DB מלפני #977: ה-CHECK של `power_requests` הכיר רק `poweroff`, ו-sqlite
    אינו מאפשר ALTER CHECK — הטבלה נבנית מחדש, והבקשה שממתינה נשמרת."""
    from server import db as db_module

    legacy = db_module.SCHEMA.replace("CHECK (action IN ('poweroff', 'reboot'))",
                                      "CHECK (action IN ('poweroff'))")
    # ‏#1367: גם `confirmed` לא היה אז -- הוא נוסף בעמודה, והבנייה מחדש שומרת אותו
    legacy = legacy.replace(",\n    confirmed    INTEGER NOT NULL DEFAULT 0   -- #1367: אושר מעל אזהרת שיכפול מקומי", "")
    assert "('poweroff', 'reboot')" not in legacy and "confirmed    INTEGER" not in legacy
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(legacy)
    raw.execute("INSERT INTO power_requests VALUES ('aa:bb:cc:00:00:21', 'poweroff', 't', 'noc')")
    raw.commit()
    raw.close()
    conn = db_module.connect(path)
    conn.execute("INSERT INTO power_requests (mac, action, requested_at, requested_by, confirmed)"
                 " VALUES ('aa:bb:cc:00:00:22', 'reboot', 't', 'noc', 1)")
    conn.commit()
    assert [tuple(r) for r in conn.execute(
        "SELECT mac, action, confirmed FROM power_requests ORDER BY mac")] == [
        ("aa:bb:cc:00:00:21", "poweroff", 0), ("aa:bb:cc:00:00:22", "reboot", 1)]   # #1367: נשמר בבנייה
