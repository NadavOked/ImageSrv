"""ערכת נושא לפי משתמש — נשמרת בשרת (#1093).

ברירת מחדל auto; PUT light/dark/auto חוזר ב-GET /me; ערך זר 422;
משתמש ב' אינו מושפע מבחירה של א'.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:                                    # pragma: no cover
    TestClient = None


def test_me_theme_defaults_to_auto(server):
    assert server["admin"].get("/api/console/me").json()["theme"] == "auto"
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: להפצה אין קונסולה


@pytest.mark.parametrize("theme", ["light", "dark", "auto"])
def test_put_theme_is_returned_by_me(server, theme):
    admin = server["admin"]
    r = admin.put("/api/console/me/theme", json={"theme": theme})
    assert r.status_code == 200, r.text
    assert admin.get("/api/console/me").json()["theme"] == theme


def test_a_foreign_theme_is_422_and_does_not_stick(server):
    admin = server["admin"]
    admin.put("/api/console/me/theme", json={"theme": "dark"})
    r = admin.put("/api/console/me/theme", json={"theme": "blue"})
    assert r.status_code == 422, r.text
    assert admin.get("/api/console/me").json()["theme"] == "dark"


def test_theme_is_per_user(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "other", "password": "Other-pass-1!", "role": "admin",
    }).status_code == 200
    other = TestClient(server["app"])
    assert other.post("/api/console/login", json={
        "username": "other", "password": "Other-pass-1!",
    }).status_code == 200

    assert admin.put("/api/console/me/theme", json={"theme": "light"}).status_code == 200
    assert other.get("/api/console/me").json()["theme"] == "auto"
    assert other.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200
    assert admin.get("/api/console/me").json()["theme"] == "light"
    assert other.get("/api/console/me").json()["theme"] == "dark"


# ---- #1499: one saved theme for the console and the build machine (Nadav 16/10) ----
# The build machine's native GUI signs in through the agent port (the kiosk
# allowlist, server/agent/kiosk.py). Its login answer carries the user's theme, and
# a switch made there is saved with the same PUT /me/theme -- also by deploy,
# who has no console at all.

def _kiosk(server):
    from server.app import create_agent_app                # noqa: PLC0415
    return TestClient(create_agent_app(server["app"].state.runtime))


def _kiosk_login(kiosk, username, password):
    r = kiosk.post("/api/console/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_kiosk_login_answer_carries_the_saved_theme(server):
    assert _kiosk_login(_kiosk(server), "noc", "admin-pass-123")["theme"] == "auto"   # never changed
    assert server["admin"].put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200
    answer = _kiosk_login(_kiosk(server), "noc", "admin-pass-123")
    assert answer["theme"] == "dark"
    assert answer["role"] == "admin"            # the rest of the answer is unchanged


def test_deploy_saves_its_own_theme_from_the_build_machine(server):
    kiosk = _kiosk(server)
    assert _kiosk_login(kiosk, "labtech", "deploy-pass-1")["theme"] == "auto"
    r = kiosk.put("/api/console/me/theme", json={"theme": "dark"})
    assert r.status_code == 200, r.text
    assert _kiosk_login(_kiosk(server), "labtech", "deploy-pass-1")["theme"] == "dark"
    # and only its own: the admin's preference is untouched
    assert server["admin"].get("/api/console/me").json()["theme"] == "auto"


def test_an_admin_switch_on_the_build_machine_reaches_the_console(server):
    kiosk = _kiosk(server)
    _kiosk_login(kiosk, "noc", "admin-pass-123")
    assert kiosk.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200
    assert server["admin"].get("/api/console/me").json()["theme"] == "dark"


def test_the_kiosk_theme_route_needs_a_session_and_a_known_value(server):
    kiosk = _kiosk(server)
    assert kiosk.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 401
    _kiosk_login(kiosk, "labtech", "deploy-pass-1")
    assert kiosk.put("/api/console/me/theme", json={"theme": "blue"}).status_code == 422
