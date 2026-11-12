"""#2877 — רישום מסרב לכתובת MAC שאינה של מחשב: אפסים, שידור, multicast."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_app
from server.console import users

REFUSAL = "MAC לא תקין — לא כתובת של מחשב"
BAD = ("ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", "00:00:00:00:00:00", "03:11:22:33:44:55")
GOOD = "b4:2e:99:07:1a:c4"


@pytest.fixture()
def admin(tmp_path, images_root, clock):
    hooks = {"apply": lambda text: None}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, known_macs_hooks=hooks)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    c = TestClient(app)
    c.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    yield c
    ctx.sender.stop()


def macs(c):
    return {m["mac"] for m in c.get("/api/console/machines").json()}


@pytest.mark.parametrize("mac", BAD)
def test_add_refuses_non_host_mac(admin, mac):
    r = admin.post("/api/console/machines", json={"mac": mac, "name": "BUILD-1", "group_id": "grp_BUILD"})
    assert r.status_code == 400
    assert REFUSAL in r.text
    assert mac not in macs(admin)


def test_add_still_accepts_unicast(admin):
    r = admin.post("/api/console/machines", json={"mac": GOOD, "name": "BUILD-1", "group_id": "grp_BUILD"})
    assert r.status_code == 200
    assert GOOD in macs(admin)


def test_import_rejects_only_the_bad_lines(admin):
    text = "\n".join([f"{m} BUILD-{i}" for i, m in enumerate(BAD)] + [f"{GOOD} BUILD-9"])
    r = admin.post("/api/console/machines/import", json={"group_id": "grp_BUILD", "text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1
    assert [x["error"] for x in body["rejected"]] == [REFUSAL] * len(BAD)
    assert macs(admin) == {GOOD}


def test_edit_mac_refuses_non_host_mac(admin):
    admin.post("/api/console/machines", json={"mac": GOOD, "name": "BUILD-1", "group_id": "grp_BUILD"})
    r = admin.put(f"/api/console/machines/{GOOD}", json={"mac": "ff:ff:ff:ff:ff:ff"})
    assert r.status_code == 400 and REFUSAL in r.text
    assert macs(admin) == {GOOD}
