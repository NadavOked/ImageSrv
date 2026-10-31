"""‏#980 — כיבוי כל המשכפלים והיסטוריית סבבי החדר (‏`server/rounds/room_console.py`).

הכיבוי **אינו** נעשה מהשרת: הוא בקשה שנוסעת בתשובת ה-hello הבא
(‏`power_action`), והסוכן מכבה אחרי `arm_wol` (‏#587). מה שנבדק כאן:
ההקלדה (עיקרון 7), הסירוב באמצע סבב, שהבקשה נמסרת פעם אחת ורק למשכפלים,
ושבקשה שפגה אינה מכבה מכונה שעולה אחר כך.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

CLONER1 = "aa:bb:cc:00:00:21"
CLONER2 = "aa:bb:cc:00:00:22"
BUILD = "b4:2e:99:07:1a:c4"
LABEL = "מחשבי שיכפול"


@pytest.fixture()
def server(tmp_path: Path, images_root):
    from server.console import users
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name, group in ((CLONER1, "01", "grp_CLONERS"), (CLONER2, "02", "grp_CLONERS"),
                             (BUILD, "INS", "grp_BUILD")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app)}
    ctx.sender.stop()


def hello(server, mac: str) -> dict:
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert response.status_code == 200
    return response.json()


def journal(server, event: str) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def add_round(ctx, rid: str, state: str, created: str, *, closed: str | None = None,
              image_id: str = "img_gone", written: int = 0, target: int = 4,
              reason: str | None = None) -> None:
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, written_drives, state,"
        " opened_by, created_at, closed_at, failed_reason) VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, image_id, target, written, state, "noc", created, closed, reason))
    ctx.conn.commit()


# --- כיבוי החדר ---------------------------------------------------------------


def test_poweroff_rides_the_next_hello_of_every_cloner_once(server):
    response = server["admin"].post("/api/console/room/poweroff", json={"confirm_name": LABEL})
    assert response.status_code == 200
    assert response.json() == {"requested": 2, "ttl_s": 120}
    assert hello(server, CLONER1)["power_action"] == "poweroff"
    assert hello(server, CLONER2)["power_action"] == "poweroff"
    # נמסרה פעם אחת: סוכן שלא הצליח לדרוך WoL נשאר דולק ואינו מקבל אותה שוב.
    assert "power_action" not in hello(server, CLONER1)
    # רק החדר — לא מחשב הבנייה.
    assert "power_action" not in hello(server, BUILD)
    assert journal(server, "room_poweroff") == ["requested=2"]
    assert journal(server, "power_delivered") == [f"{CLONER1} poweroff", f"{CLONER2} poweroff"]


@pytest.mark.parametrize("body", [{}, {"confirm_name": ""}, {"confirm_name": "מחשבי"},
                                  {"confirm_name": LABEL + " "}])
def test_poweroff_without_the_typed_name_is_refused_and_nothing_is_asked(server, body):
    response = server["admin"].post("/api/console/room/poweroff", json=body)
    assert response.status_code == 400
    assert "power_action" not in hello(server, CLONER1)
    assert journal(server, "room_poweroff") == []


def test_poweroff_with_a_non_json_body_is_a_400_not_a_500(server):
    response = server["admin"].post("/api/console/room/poweroff", content=b"nope",
                                    headers={"content-type": "application/json"})
    assert response.status_code == 400


def test_poweroff_is_refused_while_a_round_is_writing(server):
    add_round(server["ctx"], "rr_live", "active", "2026-09-24T08:00:00+00:00")
    response = server["admin"].post("/api/console/room/poweroff", json={"confirm_name": LABEL})
    assert response.status_code == 409
    assert "power_action" not in hello(server, CLONER1)


def test_poweroff_is_allowed_next_to_a_failed_round(server):
    # סבב כושל אינו כותב — המפעיל רשאי לכבות את החדר לפני שיסגור אותו.
    add_round(server["ctx"], "rr_bad", "failed", "2026-09-24T08:00:00+00:00",
              closed="2026-09-24T08:10:00+00:00", reason="send_failed")
    response = server["admin"].post("/api/console/room/poweroff", json={"confirm_name": LABEL})
    assert response.status_code == 200


def test_deploy_and_anonymous_cannot_power_off_the_room(server):
    for who in ("deploy", "anon"):
        response = server[who].post("/api/console/room/poweroff", json={"confirm_name": LABEL})
        assert response.status_code in (401, 403), who
    assert "power_action" not in hello(server, CLONER1)


def test_an_expired_request_does_not_power_off_a_machine_that_boots_later(server):
    from server.rounds import room_console

    assert server["admin"].post("/api/console/room/poweroff",
                                json={"confirm_name": LABEL}).status_code == 200
    later = datetime.now(timezone.utc) + timedelta(seconds=room_console.POWEROFF_TTL_SECONDS + 5)
    assert room_console.take_power_action(server["ctx"].conn, CLONER1, now=later) is None
    assert journal(server, "power_expired") == [f"{CLONER1} poweroff"]
    # והבקשה נמחקה — גם ה-hello הבא אינו מכבה.
    assert "power_action" not in hello(server, CLONER1)


# --- היסטוריית הסבבים --------------------------------------------------------


def test_history_lists_finished_rounds_newest_first_without_the_one_on_screen(server):
    ctx = server["ctx"]
    add_round(ctx, "rr_1", "closed", "2026-09-12T08:00:00+00:00",
              closed="2026-09-12T09:00:00+00:00", written=4)
    add_round(ctx, "rr_2", "failed", "2026-09-15T08:00:00+00:00",
              closed="2026-09-15T08:20:00+00:00", written=1, reason="send_failed")
    add_round(ctx, "rr_3", "active", "2026-09-24T08:55:00+00:00")
    rows = server["admin"].get("/api/console/room/history").json()
    assert [r["id"] for r in rows] == ["rr_2", "rr_1"]
    assert rows[0] == {
        "id": "rr_2", "state": "failed", "image_id": "img_gone",
        # האימג' כבר אינו בספרייה — השם נופל ל-image_id, כמו ב-`/room`.
        "image_name": "img_gone", "source_kind": "library",
        "target_drives": 4, "written_drives": 1, "opened_by": "noc",
        "created_at": "2026-09-15T08:00:00+00:00",
        "closed_at": "2026-09-15T08:20:00+00:00", "failed_reason": "send_failed",
        # ‏#1889: אין גלים בחלון — לא נמדד (null), לא 0.
        "waits": {"waves": 0, "open_wait_s": None, "smart_hold_s": None, "smart_holds": 0,
                  "smart_unreleased": 0}}


def test_history_hides_a_failed_round_that_is_still_on_screen(server):
    ctx = server["ctx"]
    add_round(ctx, "rr_1", "closed", "2026-09-12T08:00:00+00:00", closed="2026-09-12T09:00:00+00:00")
    add_round(ctx, "rr_2", "failed", "2026-09-15T08:00:00+00:00", closed="2026-09-15T08:20:00+00:00")
    assert server["admin"].get("/api/console/room").json()["round"]["id"] == "rr_2"
    assert [r["id"] for r in server["admin"].get("/api/console/room/history").json()] == ["rr_1"]


def test_history_limit(server):
    ctx = server["ctx"]
    for day in range(10, 20):
        add_round(ctx, f"rr_{day}", "closed", f"2026-09-{day}T08:00:00+00:00",
                  closed=f"2026-09-{day}T09:00:00+00:00")
    assert len(server["admin"].get("/api/console/room/history").json()) == 10
    assert [r["id"] for r in server["admin"].get("/api/console/room/history?limit=2").json()] == ["rr_19", "rr_18"]
    assert server["admin"].get("/api/console/room/history?limit=0").status_code == 400


def test_history_empty_is_an_empty_list(server):
    assert server["admin"].get("/api/console/room/history").json() == []


def test_history_needs_a_login(server):
    assert server["anon"].get("/api/console/room/history").status_code == 401
