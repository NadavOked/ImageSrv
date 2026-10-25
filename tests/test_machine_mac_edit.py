"""‏#977 §5 — עריכת MAC של מכונה רשומה (‏`PUT /api/console/machines/{mac}` עם `mac`).

כרטיס רשת שהוחלף: השורה עוברת לכתובת החדשה (שם, קבוצה, חריצים נשארים),
ו-known-macs נכתב מחדש מיד — אחרת הכתובת החדשה אינה מקבלת GRUB (#141)
והישנה עדיין כן. מה שנבדק: ההעברה, הסנכרון, וארבעת הסירובים — כתובת לא
תקינה, כתובת תפוסה, מכונה שעובדת עכשיו, ושילוב עם שדות אחרים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server import users
from server.app import create_app

OLD = "b4:2e:99:07:1a:c4"
NEW = "b4:2e:99:07:1a:d9"
OTHER = "aa:bb:cc:00:00:21"


@pytest.fixture()
def server(tmp_path, images_root, clock):
    fake = {"applied": [], "error": None}
    hooks = {"apply": lambda text: (fake["applied"].append(text), fake["error"])[1]}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, known_macs_hooks=hooks)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name, group in ((OLD, "BUILD-1", "grp_BUILD"), (OTHER, "01", "grp_CLONERS")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "fake": fake}
    ctx.sender.stop()


def rows(server) -> dict[str, dict]:
    return {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}


def test_the_row_moves_to_the_new_mac_and_keeps_its_name_and_group(server):
    server["fake"]["applied"].clear()
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": "B4-2E-99-07-1A-D9"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "mac": NEW, "network": {"applied": True, "error": None}}
    after = rows(server)
    assert OLD not in after
    assert after[NEW]["suffix"] == "BUILD-1" and after[NEW]["group_id"] == "grp_BUILD"
    # ‏known-macs נכתב מחדש: החדשה מקבלת GRUB, הישנה כבר לא (#141).
    text = server["fake"]["applied"][-1]
    assert f"{NEW},set:known" in text and OLD not in text
    detail = server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'machine_mac_edit'").fetchone()["detail"]
    assert detail == f"{OLD} -> {NEW}"
    shown = [r for r in server["admin"].get("/api/console/journal").json()
             if r["event"] == "machine_mac_edit"]
    assert shown[0]["text"] == f"{OLD} הוחלפה ב-{NEW}"


def test_a_network_failure_is_said_but_the_row_is_saved(server):
    server["fake"]["error"] = "dnsmasq לא הגיב ל-reload"
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": NEW})
    assert r.status_code == 200
    assert r.json()["network"] == {"applied": False, "error": "dnsmasq לא הגיב ל-reload"}
    assert NEW in rows(server)


@pytest.mark.parametrize("value, status", [
    ("not-a-mac", 400),
    (OTHER, 409),          # כתובת של מכונה רשומה אחרת
])
def test_an_invalid_or_taken_mac_is_refused_and_nothing_moves(server, value, status):
    before = len(server["fake"]["applied"])
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": value})
    assert r.status_code == status, r.text
    assert OLD in rows(server)
    assert len(server["fake"]["applied"]) == before


def test_the_same_mac_is_a_no_op(server):
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": OLD.upper()})
    assert r.status_code == 200
    assert r.json()["mac"] == OLD and r.json()["network"] is None


def test_an_unknown_machine_is_404(server):
    r = server["admin"].put("/api/console/machines/aa:bb:cc:dd:ee:ff", json={"mac": NEW})
    assert r.status_code == 404


def test_mac_is_edited_alone(server):
    """בלי עדכון חלקי: שם לא תקין אחרי שה-MAC כבר עבר היה משאיר חצי עריכה."""
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": NEW, "name": "x"})
    assert r.status_code == 400
    assert OLD in rows(server)


def test_a_machine_with_an_open_task_keeps_its_mac(server):
    """המשימה כתובה ל-MAC הישן: הסוכן שקולט עכשיו היה מאבד את המשימה שלו."""
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, created_by, created_at, updated_at)"
        " VALUES ('tsk_1', ?, 'capture', 'sda', 'img_1', 'x', 'noc', 't', 't')", (OLD,))
    conn.commit()
    r = server["admin"].put(f"/api/console/machines/{OLD}", json={"mac": NEW})
    assert r.status_code == 409, r.text
    assert OLD in rows(server)


def test_a_machine_in_an_open_round_keeps_its_mac(server):
    """גל של חדר המשכפלים הוא session רגיל, והחברות בו לפי MAC."""
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at) VALUES"
        " ('ses_1', 'grp_CLONERS', 'img_1', 'X', 1, 60, 'running', 'noc', 't', 0)")
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES ('ses_1', ?, 't')",
                 (OTHER,))
    conn.commit()
    r = server["admin"].put(f"/api/console/machines/{OTHER}", json={"mac": NEW})
    assert r.status_code == 409, r.text
    assert OTHER in rows(server)


def test_deploy_cannot_edit_a_mac(server):
    assert server["deploy"].put(f"/api/console/machines/{OLD}", json={"mac": NEW}).status_code == 403
