"""‏#1127 — הקשחות קטנות אחרי R36/R39/R40, כל אחת עם הראיה שלה."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from boot.grub_menu import GrubConfig
from server.agent import hello
from test_login_mfa import _fresh


def test_logout_is_allowed_while_a_password_change_is_forced(tmp_path, images_root, clock, monkeypatch):
    """‏R36: במצב "חובה להחליף סיסמה" רק `POST /me/password` היה מותר — גם
    logout קיבל 403, ומשתמש שמתחרט נשאר כלוא."""
    c = TestClient(_fresh(tmp_path, images_root, clock, monkeypatch))
    assert c.post("/api/console/login", json={"username": "admin", "password": "admin"}).json() == {
        "must_change_password": True}
    assert c.get("/api/console/me").status_code == 403
    assert c.post("/api/console/logout").status_code == 200
    assert c.get("/api/console/me").status_code == 401


def test_a_group_label_longer_than_100_chars_is_refused(server):
    admin = server["admin"]
    r = admin.post("/api/console/groups", json={"label": "ק" * 101, "role": "build"})
    assert r.status_code == 400 and "ארוך" in r.json()["detail"]
    ok = admin.post("/api/console/groups", json={"id": "grp_long", "label": "ק" * 100, "role": "build"})
    assert ok.status_code == 200, ok.text
    r = admin.put("/api/console/groups/grp_long", json={"label": "x" * 101})
    assert r.status_code == 400


def test_self_probe_without_a_client_is_false_not_an_exception():
    scope = {"headers": [(hello.PROBE_HEADER, b"1")], "client": None, "server": ("10.44.0.1", 8080)}
    assert hello.self_probe(scope) is False
    assert hello.self_probe({"headers": [(hello.PROBE_HEADER, b"1")], "server": ("10.44.0.1", 8080)}) is False


@pytest.mark.parametrize("base", [
    'http://10.44.12.10:8080/$x', 'http://10.44.12.10:8080/"', "http://10.44.12.10 :8080",
    "http://10.44.12.10:8080;reboot", "http://10.44.12.10:8080/`id`",
])
def test_a_server_base_with_a_grub_metacharacter_is_rejected(base):
    with pytest.raises(ValueError, match="metacharacter"):
        GrubConfig(server_base=base)


def test_a_plain_server_base_still_passes():
    assert GrubConfig(server_base="http://imagesrv-tlv.college.ac.il:8080").server_base


def test_an_oversized_hello_field_is_refused_with_413(server):
    """‏R39: ‏hw_inventory/hw_probe נשמרו בלי תקרת גודל/עומק."""
    from conftest import hello_body  # noqa: PLC0415
    anon = server["anon"]
    body = hello_body("b4:2e:99:07:1a:c4")
    body["inventory"] = {"dmi": {"vendor": "x" * (64 * 1024 + 1)}}
    r = anon.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
    assert "inventory" in r.json()["error"]
    deep = {"a": 1}
    for _ in range(9):
        deep = {"n": deep}
    body = hello_body("b4:2e:99:07:1a:c4")
    body["probe"] = deep
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 413
    # a normal hello is untouched
    assert anon.post("/api/v1/agent/hello", json=hello_body("b4:2e:99:07:1a:c4")).status_code == 200
