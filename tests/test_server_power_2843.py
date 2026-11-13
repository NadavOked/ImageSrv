"""#2843 — הפעלה מחדש וכיבוי של השרת מהקונסולה (``POST /api/console/server-power``).

שום טסט כאן לא מכבה מכונה: הפעולה עוברת דרך ``host_hooks["power"]``, וכאן
מוזרק רושם. בלי hook מוזרק הנתיב עונה 503 — וזה עצמו נבדק, כי זה מה שמגן
על כל הרצת בדיקות אחרת. ``schedule_later`` נבדק מול ``deploy_net._run``
מזויף: הפקודה נבנית, ולא רצה.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login


def _sp():
    """ייבוא בתוך הטסט: בבקרה השלילית המודול אינו קיים, והטסטים של הנתיב צריכים
    להיכשל על ההתנהגות (404), לא על שגיאת איסוף."""
    from server.console import server_power
    return server_power


class Power:
    """במקום ``systemd-run … systemctl reboot|poweroff``."""

    def __init__(self, error=None):
        self.calls: list[str] = []
        self.error = error

    def __call__(self, action):
        self.calls.append(action)
        return self.error


def _build(tmp_path: Path, images_root: Path, clock, hooks):
    from server.app import create_app
    from server.console import users
    from test_sender import Recorder

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), host_hooks=hooks)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    return app, ctx, admin, deploy


@pytest.fixture()
def host(tmp_path, images_root, clock):
    power = Power()
    app, ctx, admin, deploy = _build(tmp_path, images_root, clock, {"power": power})
    yield {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy, "power": power}
    ctx.sender.stop()


def journal_rows(ctx, event):
    return [(r["detail"], r["user"]) for r in ctx.conn.execute(
        "SELECT detail, user FROM journal WHERE event = ? ORDER BY id", (event,))]


def open_round(ctx):
    """סבב רץ — ``restart_blocker`` מחזיר סיבה (אותה שורה כמו ב-test_console_hostcfg)."""
    ctx.conn.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES ('grp_X', 'X', 'cloner')")
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, "
        "wait_seconds, state, opened_by, created_at, last_join_at) "
        "VALUES ('ses_x', 'grp_X', 'img', 'LAB', 1, 30, 'running', 'noc', 'now', 1)")
    ctx.conn.commit()


@pytest.mark.parametrize("action", ["reboot", "poweroff"])
def test_admin_schedules_through_the_hook_and_it_is_journaled(host, action):
    r = host["admin"].post("/api/console/server-power", json={"action": action})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["action"] == action and body["interrupted"] is None
    assert body["in_seconds"] == _sp().DELAY_SECONDS
    assert host["power"].calls == [action]
    assert journal_rows(host["ctx"], "server_power") == [(action, "noc")]


def test_deploy_user_gets_403_and_nothing_happens(host):
    r = host["deploy"].post("/api/console/server-power", json={"action": "poweroff"})
    assert r.status_code == 403, r.text
    assert host["power"].calls == []
    assert journal_rows(host["ctx"], "server_power") == []


def test_anonymous_gets_401(host, tmp_path):
    anon = TestClient(host["app"])
    assert anon.post("/api/console/server-power", json={"action": "reboot"}).status_code == 401
    assert host["power"].calls == []


@pytest.mark.parametrize("body", [{}, {"action": "halt"}, {"action": "REBOOT"}, {"action": ["reboot"]}])
def test_unknown_action_is_refused_before_the_hook(host, body):
    r = host["admin"].post("/api/console/server-power", json=body)
    assert r.status_code == 400, r.text
    assert host["power"].calls == []


def test_cross_site_request_is_refused_on_the_console_app(host):
    """CSRF: אפליקציית הקונסולה (:8081) — השומר של #1633 חל גם על הנתיב הזה."""
    from server.app import create_console_app
    console = TestClient(create_console_app(host["app"].state.runtime))
    complete_console_login(console, host["ctx"].conn, "noc", "admin-pass-123")
    r = console.post("/api/console/server-power", json={"action": "poweroff"},
                     headers={"Sec-Fetch-Site": "cross-site"})
    assert (r.status_code, r.json().get("code")) == (403, "cross_site"), r.text
    assert host["power"].calls == []
    # the same request from the console's own page passes: the refusal above is the guard, not the route
    r = console.post("/api/console/server-power", json={"action": "poweroff"},
                     headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.text
    assert host["power"].calls == ["poweroff"]


def test_active_round_is_409_with_the_reason_until_the_admin_forces(host):
    open_round(host["ctx"])
    r = host["admin"].post("/api/console/server-power", json={"action": "poweroff"})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "power_busy" and body["busy"]
    assert host["power"].calls == []
    assert journal_rows(host["ctx"], "server_power") == []
    # "force" must be the boolean the console sends -- a truthy string is not the admin's click
    r = host["admin"].post("/api/console/server-power", json={"action": "poweroff", "force": "yes"})
    assert r.status_code == 409, r.text
    r = host["admin"].post("/api/console/server-power", json={"action": "poweroff", "force": True})
    assert r.status_code == 200, r.text
    assert r.json()["interrupted"] == body["busy"]
    assert host["power"].calls == ["poweroff"]
    [(detail, user)] = journal_rows(host["ctx"], "server_power")
    assert user == "noc" and detail.startswith("poweroff") and body["busy"] in detail


def test_hook_failure_is_500_and_journaled_not_success(host):
    host["power"].error = "systemd-run נכשל: boom"
    r = host["admin"].post("/api/console/server-power", json={"action": "reboot"})
    assert r.status_code == 500, r.text
    assert "boom" in r.json()["detail"]
    assert journal_rows(host["ctx"], "server_power") == []
    [(detail, user)] = journal_rows(host["ctx"], "server_power_failed")
    assert user == "noc" and "boom" in detail


def test_without_an_installed_hook_the_route_refuses_503(tmp_path, images_root, clock):
    """``create_app`` בלי ``host_hooks["power"]`` (כל שאר הבדיקות, הרצת פיתוח) — אין כיבוי."""
    _app, ctx, admin, _deploy = _build(tmp_path, images_root, clock, None)
    try:
        r = admin.post("/api/console/server-power", json={"action": "poweroff"})
        assert r.status_code == 503, r.text
        assert journal_rows(ctx, "server_power") == []
        assert len(journal_rows(ctx, "server_power_failed")) == 1
    finally:
        ctx.sender.stop()


def test_schedule_later_runs_systemctl_in_a_transient_unit_after_the_reply(monkeypatch):
    """התשובה יוצאת לפני שהשרת יורד: systemd-run --on-active, שם יחידה ייחודי."""
    server_power = _sp()
    seen = []
    monkeypatch.setattr(server_power.deploy_net, "_run",
                        lambda cmd, **kw: seen.append(cmd) or (True, "", ""))
    assert server_power.schedule_later("poweroff") is None
    assert server_power.schedule_later("reboot") is None
    a, b = seen
    assert a[0] == "systemd-run" and f"--on-active={server_power.DELAY_SECONDS}" in a
    assert a[-2:] == ["systemctl", "poweroff"] and b[-2:] == ["systemctl", "reboot"]
    assert "--collect" in a
    units = [next(x for x in cmd if x.startswith("--unit=")) for cmd in seen]
    assert units[0] != units[1]
    monkeypatch.setattr(server_power.deploy_net, "_run", lambda cmd, **kw: (False, "", "denied"))
    assert "denied" in server_power.schedule_later("reboot")


def test_main_installs_the_real_hook(tmp_path, monkeypatch):
    """הייצור: ``server.main`` — ורק הוא — מעביר את ``schedule_later``."""
    from test_dev_station_no_etc_2267 import _run_main
    seen = _run_main(tmp_path, monkeypatch, "linux")
    assert "power" in (seen.get("host_hooks") or {}), seen.get("host_hooks")
    assert seen["host_hooks"]["power"] is _sp().schedule_later


def test_journal_events_have_hebrew_and_severity():
    from server.console import journal_he
    assert journal_he.EVENTS_HE["server_power"] and journal_he.EVENTS_HE["server_power_failed"]
    assert journal_he.severity("server_power") == "warn"
    assert journal_he.severity("server_power_failed") == "err"
