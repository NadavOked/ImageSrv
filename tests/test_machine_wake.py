"""‏#984 — WoL למחשב בנייה ולמשכפל **בודד** מהקונסולה.

עד כאן WoL מהקונסולה היה מסלול אחד: ‏`POST /room/wake`, שמעיר את כל
‏`grp_CLONERS`. כאן נבדקים שני המסלולים החדשים —
‏`POST /machines/{mac}/wake` ו-`POST /groups/{gid}/wake` — ומה שנדב
הכריע (17/09) שהם **לא** עושים: תחנות כיתה הן v2, ו-`sent` אינו
‏`woken` (#528).

**לעולם לא יוצאת מכאן חבילה אמיתית** — השולח מוזרק ל-`create_app`
כמו ב-`test_wol.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: השרת מסרב לכיתות ב-v1. הבדיקות כאן יוצרות קבוצת
#: כיתה ובודקות את זרימת v1.2 — ולכן מדליקות את היכולת (`classrooms_on`).
pytestmark = pytest.mark.usefixtures("classrooms_on")

from fastapi.testclient import TestClient

BUILD = "b4:2e:99:07:1a:c4"
BUILD2 = "b4:2e:99:07:1a:c5"
CLONER = "aa:bb:cc:00:00:21"
STUDENT = "aa:bb:cc:00:00:31"
STRANGER = "de:ad:be:ef:00:99"

LAB = "grp_LAB1"


def macs_of(packets: list[bytes]) -> list[str]:
    """מהחבילות חזרה ל-MACים — כדי לבדוק *למי* נשלח, לא רק כמה."""
    return [":".join(f"{b:02x}" for b in packet[6:12]) for packet in packets]


@pytest.fixture()
def server(tmp_path: Path, images_root):
    """שרת עם WoL נתפס; שני מחשבי בנייה, משכפל אחד ותחנת כיתה אחת."""
    from server.console import users
    from server.app import create_app

    woken: list[bytes] = []
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=woken.append)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)

    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    assert admin.post("/api/console/groups", json={
        "id": LAB, "label": "כיתה LAB1", "role": "classroom"}).status_code == 200
    for mac, name, group in ((BUILD, "INS", "grp_BUILD"),
                             (BUILD2, "02", "grp_BUILD"),
                             (CLONER, "01", "grp_CLONERS"),
                             (STUDENT, "05", LAB)):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"app": app, "admin": admin, "deploy": deploy, "anon": TestClient(app),
           "woken": woken, "ctx": ctx}
    ctx.sender.stop()


def journal_rows(server, event: str) -> list:
    return server["ctx"].conn.execute(
        "SELECT user, detail FROM journal WHERE event = ?", (event,)).fetchall()


# --- מכונה בודדת -------------------------------------------------------------


def test_a_build_machine_is_woken_alone(server):
    result = server["admin"].post(f"/api/console/machines/{BUILD}/wake")
    assert result.status_code == 200
    assert result.json() == {"sent": 1, "failed": 0, "reasons": []}
    assert macs_of(server["woken"]) == [BUILD]          # רק היא — לא החדר


def test_a_single_cloner_is_woken_alone_not_the_whole_room(server):
    result = server["admin"].post(f"/api/console/machines/{CLONER}/wake")
    assert result.status_code == 200 and result.json()["sent"] == 1
    assert macs_of(server["woken"]) == [CLONER]


def test_the_mac_may_be_written_any_way(server):
    """סעיף 10 — ‏`B4-2E-99-07-1A-C4` היא אותה מכונה."""
    spelled = BUILD.upper().replace(":", "-")
    result = server["admin"].post(f"/api/console/machines/{spelled}/wake")
    assert result.status_code == 200 and result.json()["sent"] == 1
    assert macs_of(server["woken"]) == [BUILD]


def test_a_classroom_station_is_refused_v2(server):
    """הכרעת נדב 17/09: תחנות כיתה = v2. לא נפתח "כי זה אותו קוד"."""
    result = server["admin"].post(f"/api/console/machines/{STUDENT}/wake")
    assert result.status_code == 403
    assert "v2" in result.json()["detail"]
    assert server["woken"] == []


def test_an_unregistered_mac_is_404_and_nothing_is_sent(server):
    result = server["admin"].post(f"/api/console/machines/{STRANGER}/wake")
    # ההודעה שלנו, לא ה-404 של FastAPI על נתיב שאינו קיים.
    assert result.status_code == 404 and "רשומה" in result.json()["detail"]
    assert server["woken"] == []


def test_a_malformed_mac_is_400(server):
    assert server["admin"].post(
        "/api/console/machines/not-a-mac/wake").status_code == 400
    assert server["woken"] == []


def test_the_wake_is_written_to_the_journal_with_mac_and_user(server):
    server["admin"].post(f"/api/console/machines/{BUILD}/wake")
    rows = journal_rows(server, "wol_sent")
    assert len(rows) == 1
    assert rows[0]["user"] == "noc" and rows[0]["detail"] == f"{BUILD} count=1"


def test_the_endpoint_reports_sent_not_woken(server):
    """‏#528 — UDP בלי ACK: הקרנל קיבל 102 בייט, ותו לא."""
    body = server["admin"].post(f"/api/console/machines/{BUILD}/wake").json()
    assert "sent" in body and "woken" not in body


# --- הרשאות ------------------------------------------------------------------


def test_admin_wakes_a_machine_and_deploy_has_no_console_for_it(server):
    """‏#1073: WoL למכונה בודדת הוא פעולת קונסולה, ולמשתמש הפצה אין קונסולה —
    403 עם ההודעה של `deploy_no_console`, ושום פקטה לא יוצאת. (החדר —
    `/room/wake` — נשאר לו, דרך הקיוסק ממחשב הבנייה.)"""
    refused = server["deploy"].post(f"/api/console/machines/{BUILD}/wake")
    assert refused.status_code == 403
    assert "מחשב הבנייה" in refused.json()["detail"]
    assert server["woken"] == []
    assert server["admin"].post(
        f"/api/console/machines/{BUILD}/wake").status_code == 200
    assert len(server["woken"]) == 1


def test_anonymous_cannot_wake_a_machine(server):
    assert server["anon"].post(
        f"/api/console/machines/{BUILD}/wake").status_code in (401, 403)
    assert server["woken"] == []


def test_a_viewer_role_is_refused_like_the_room(server):
    """אותה רשימת-היתר כמו `/room/wake` (‏`ROOM_OPERATOR_ROLES`): תפקיד
    שלישי אינו מקבל את הכפתור ביום היוולדו."""
    from test_station import add_user_with_role                # noqa: PLC0415

    add_user_with_role(server, "viewer", "viewer-pass-1", "viewer")
    viewer = TestClient(server["app"])
    assert viewer.post("/api/console/login", json={
        "username": "viewer", "password": "viewer-pass-1"}).status_code == 200
    assert viewer.post(f"/api/console/machines/{BUILD}/wake").status_code == 403
    assert viewer.post("/api/console/groups/grp_BUILD/wake").status_code == 403
    assert server["woken"] == []


# --- כשל שליחה ---------------------------------------------------------------


@pytest.fixture()
def broken_server(tmp_path: Path, images_root):
    """שולח שנכשל תמיד — כבל שלוף בשרת, לא BIOS במכונה."""
    from server.console import users
    from server.app import create_app

    def dead(packet: bytes) -> None:
        raise OSError("no-carrier: אין carrier על eth1")

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=dead)
    ctx = app.state.ctx
    # ‏#1073: WoL למכונה הוא פעולת קונסולה — admin (ל-deploy אין קונסולה).
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    ctx.conn.execute(
        "INSERT INTO machines (mac, suffix, group_id, added_at)"
        " VALUES (?, 'INS', 'grp_BUILD', '2026-09-17T00:00:00Z')", (BUILD,))
    ctx.conn.commit()
    yield {"admin": admin, "ctx": ctx}
    ctx.sender.stop()


def test_a_failed_send_is_visible_with_its_reason_and_in_the_journal(broken_server):
    """‏"0 מחשבים" בלי סיבה שולח את הטכנאי ל-BIOS; הסיבה עולה למסך
    **וגם** ליומן, ואין `wol_sent` על מה שלא נשלח."""
    result = broken_server["admin"].post(f"/api/console/machines/{BUILD}/wake")
    assert result.status_code == 200
    body = result.json()
    assert body["sent"] == 0 and body["failed"] == 1
    assert body["reasons"] == ["no-carrier: אין carrier על eth1"]

    failed = journal_rows(broken_server, "wol_failed")
    assert len(failed) == 1
    assert BUILD in failed[0]["detail"] and "no-carrier" in failed[0]["detail"]
    assert journal_rows(broken_server, "wol_sent") == []


# --- קבוצה: grp_BUILD ----------------------------------------------------------


def test_waking_grp_build_wakes_every_build_machine_and_nothing_else(server):
    result = server["admin"].post("/api/console/groups/grp_BUILD/wake")
    assert result.status_code == 200
    assert result.json() == {"sent": 2, "failed": 0, "reasons": []}
    assert sorted(macs_of(server["woken"])) == sorted([BUILD, BUILD2])
    rows = journal_rows(server, "wol_sent")
    assert len(rows) == 1
    assert rows[0]["user"] == "noc" and rows[0]["detail"] == "grp_BUILD count=2"


def test_waking_a_classroom_group_is_refused_v2(server):
    result = server["admin"].post(f"/api/console/groups/{LAB}/wake")
    assert result.status_code == 403
    assert "v2" in result.json()["detail"]
    assert server["woken"] == []


def test_waking_an_unknown_group_is_404(server):
    result = server["admin"].post("/api/console/groups/grp_NOPE/wake")
    assert result.status_code == 404 and "קבוצה" in result.json()["detail"]
    assert server["woken"] == []


def test_anonymous_cannot_wake_a_group(server):
    assert server["anon"].post(
        "/api/console/groups/grp_BUILD/wake").status_code in (401, 403)
    assert server["woken"] == []
