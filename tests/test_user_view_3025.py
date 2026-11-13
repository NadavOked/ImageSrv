"""#3025: תצוגה בסיסית/מתקדמת לפי משתמש — נשמרת בשרת, כמו theme (#1093).

הכרעות נדב 12/11: המנהל שנוצר בהתקנה (``is_builtin``) מקבל מתקדם, כל מנהל אחר
בסיסי; הבחירה נשמרת לכל משתמש ב-``users.view``; ``/me`` מחזיר אותה,
``PUT /me/view`` שומר; וכניסת הקיוסק של מחשב הבנייה מחזירה אותה כמו theme.

בקרה שלילית: על הקוד שלפני #3025 אין ``view`` ב-``/me`` ובתשובת הקיוסק, ו-
``PUT /me/view`` מחזיר 404/405 — כל טסט כאן נכשל.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _login(server, username, password):
    from conftest import complete_console_login                    # noqa: PLC0415
    client = TestClient(server["app"])
    complete_console_login(client, server["ctx"].conn, username, password)
    return client


def _builtin(server):
    from server.console import users                               # noqa: PLC0415
    users.create(server["ctx"].conn, "installer", "Install-pass-1!", "admin", by="test",
                 is_builtin=True, check_policy=False)
    return _login(server, "installer", "Install-pass-1!")


def test_an_admin_created_after_install_starts_basic(server):
    assert server["admin"].get("/api/console/me").json()["view"] == "basic"


def test_the_install_admin_starts_advanced(server):
    assert _builtin(server).get("/api/console/me").json()["view"] == "advanced"


@pytest.mark.parametrize("view", ["advanced", "basic"])
def test_put_view_is_returned_by_me(server, view):
    admin = server["admin"]
    r = admin.put("/api/console/me/view", json={"view": view})
    assert r.status_code == 200, r.text
    assert admin.get("/api/console/me").json()["view"] == view


def test_the_install_admin_can_choose_basic_and_it_sticks(server):
    builtin = _builtin(server)
    assert builtin.put("/api/console/me/view", json={"view": "basic"}).status_code == 200
    assert builtin.get("/api/console/me").json()["view"] == "basic"


@pytest.mark.parametrize("body", [{"view": "expert"}, {"view": None}, {}, ["advanced"]])
def test_a_foreign_view_is_422_and_does_not_stick(server, body):
    admin = server["admin"]
    assert admin.put("/api/console/me/view", json={"view": "advanced"}).status_code == 200
    r = admin.put("/api/console/me/view", json=body)
    assert r.status_code == 422, r.text
    assert admin.get("/api/console/me").json()["view"] == "advanced"


def test_view_is_per_user_and_one_user_cannot_set_another(server):
    """``PUT /me/view`` has no username: whatever the body says, it is the caller's own row."""
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "other", "password": "Other-pass-1!", "role": "admin",
    }).status_code == 200
    other = _login(server, "other", "Other-pass-1!")

    r = admin.put("/api/console/me/view", json={"view": "advanced", "username": "other"})
    assert r.status_code == 200, r.text
    assert other.get("/api/console/me").json()["view"] == "basic", "the other user is untouched"
    assert admin.get("/api/console/me").json()["view"] == "advanced"
    assert other.put("/api/console/me/view", json={"view": "advanced"}).status_code == 200
    assert other.put("/api/console/me/view", json={"view": "basic"}).status_code == 200
    assert admin.get("/api/console/me").json()["view"] == "advanced"


def test_the_view_route_needs_a_session(server):
    assert server["anon"].put("/api/console/me/view", json={"view": "advanced"}).status_code == 401


def test_the_view_is_not_written_to_the_journal(server):
    """A preference, like theme -- not an administrative action."""
    conn = server["ctx"].conn
    before = conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
    assert server["admin"].put("/api/console/me/view", json={"view": "advanced"}).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == before


# ---- the build machine: the kiosk login answer carries the view, as it carries theme ----

def _kiosk(server):
    from server.app import create_agent_app                        # noqa: PLC0415
    return TestClient(create_agent_app(server["app"].state.runtime))


def _kiosk_login(server, username, password):
    r = _kiosk(server).post("/api/console/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_kiosk_login_answer_carries_the_saved_view(server):
    answer = _kiosk_login(server, "noc", "admin-pass-123")
    assert answer["view"] == "basic"
    assert server["admin"].put("/api/console/me/view", json={"view": "advanced"}).status_code == 200
    answer = _kiosk_login(server, "noc", "admin-pass-123")
    assert answer["view"] == "advanced"
    assert answer["role"] == "admin" and answer["theme"] == "auto"   # the rest is unchanged


def test_the_kiosk_login_answer_gives_the_install_admin_advanced(server):
    _builtin(server)
    assert _kiosk_login(server, "installer", "Install-pass-1!")["view"] == "advanced"
