from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from conftest import complete_console_login
from server import health, timesync, users


class ClockMachine:
    def __init__(self):
        self.written, self.removed, self.restarts = [], 0, 0
        self.tracking = "Reference ID : 1.2.3.4\nSystem time : 0.001250 seconds slow of NTP time\nLeap status : Normal\n"
        self.sources = "MS Name/IP address Stratum Poll Reach LastRx Last sample\n^* 1.2.3.4 2 6 377 10 -10us[ -20us] +/- 1ms\n"

    def hooks(self):
        return {"write": self.written.append, "write_server": lambda _: None,
                "ensure_confdir": lambda: None, "restart": self.restart,
                "tracking": lambda: self.tracking, "sources": lambda: self.sources,
                "sleep": lambda _: None,
                "now": lambda: "2026-10-19T12:34:00+00:00"}

    def remove(self): self.removed += 1
    def restart(self): self.restarts += 1


@pytest.mark.parametrize("value", ["bad_name!", "a..b", "-bad.example", "one two three four five"])
def test_invalid_server_is_rejected_with_the_exact_hebrew_prefix(value):
    with pytest.raises(ValueError, match="כתובת שרת זמן לא תקינה"):
        timesync.validate(value)


def test_ipv4_ipv6_and_rfc1123_names_are_accepted_and_normalized():
    assert timesync.validate("  1.1.1.1   2001:db8::1 ntp.example.org ") == "1.1.1.1 2001:db8::1 ntp.example.org"


def test_apply_writes_exact_dropin_restarts_and_reads_back():
    machine = ClockMachine()
    result = timesync.apply("ntp.example.org 10.0.0.1", machine.hooks())
    assert machine.written == ["ntp.example.org 10.0.0.1"] and machine.restarts == 1
    assert result == {"synchronized": True, "server": "1.2.3.4",
                      "offset_ms": -1.25, "checked_at": "2026-10-19T12:34:00+00:00"}


def test_default_write_has_exact_file_content(tmp_path, monkeypatch):
    path = tmp_path / "imagectl.conf"
    monkeypatch.setattr(timesync, "CLIENT_CONF", path)
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    timesync._write_client("a.example 1.2.3.4")
    assert path.read_text(encoding="utf-8").endswith("server a.example iburst\nserver 1.2.3.4 iburst\n")


def test_automatic_writes_debian_pools_and_server_off_writes_port_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "CLIENT_CONF", tmp_path / "imagectl.conf")
    monkeypatch.setattr(timesync, "SERVER_CONF", tmp_path / "imagectl-server.conf")
    timesync._write_client("")
    assert timesync.CLIENT_CONF.read_text(encoding="utf-8").count("debian.pool.ntp.org") == 4
    timesync._write_server([])
    assert "port 0" in timesync.SERVER_CONF.read_text(encoding="utf-8")


def test_server_on_writes_allow_subnets_and_listener_bind(tmp_path, monkeypatch):
    monkeypatch.setattr(timesync, "CONF_DIR", tmp_path)
    monkeypatch.setattr(timesync, "SERVER_CONF", tmp_path / "imagectl-server.conf")
    timesync._write_server(["10.44.0.0/24", "2001:db8::/64"])
    text = timesync.SERVER_CONF.read_text(encoding="utf-8")
    assert "port 123" in text and "bindaddress 0.0.0.0" in text
    assert "allow 10.44.0.0/24" in text and "allow 2001:db8::/64" in text


def test_apply_server_uses_only_hooks_and_restarts():
    seen = []
    hooks = {"ensure_confdir": lambda: seen.append("include"),
             "write_server": lambda networks: seen.append(networks),
             "restart": lambda: seen.append("restart")}
    timesync.apply_server(["10.44.0.1/24"], hooks)
    assert seen == ["include", ["10.44.0.0/24"], "restart"]


def test_debian_main_conf_is_made_additive_safe_and_confdir_is_idempotent(tmp_path, monkeypatch):
    main = tmp_path / "chrony.conf"
    main.write_text("pool 2.debian.pool.ntp.org iburst\nsourcedir /run/chrony-dhcp\ndriftfile /x\n", encoding="utf-8")
    monkeypatch.setattr(timesync, "MAIN_CONF", main)
    timesync._ensure_confdir(); timesync._ensure_confdir()
    text = main.read_text(encoding="utf-8")
    assert not any(line.startswith(("pool ", "server ", "peer ", "sourcedir ")) for line in text.splitlines())
    assert text.count("confdir /etc/chrony/conf.d") == 1


def test_server_unit_allows_the_exact_chrony_dropin_directory():
    unit = (__import__("pathlib").Path(__file__).parents[1] / "install/imagectl-server.service").read_text(encoding="utf-8")
    rw = next(line for line in unit.splitlines() if line.startswith("ReadWritePaths="))
    assert "/etc/chrony/conf.d" in rw.split("=", 1)[1].split()


def test_restart_failure_surfaces():
    machine = ClockMachine()
    def failed(): raise RuntimeError("restart failed")
    hooks = {**machine.hooks(), "restart": failed}
    with pytest.raises(RuntimeError, match="restart failed"):
        timesync.apply("", hooks)


def test_status_never_turns_an_unreadable_measurement_into_true():
    machine = ClockMachine()
    hooks = machine.hooks()
    hooks["tracking"] = lambda: (_ for _ in ()).throw(RuntimeError("no chronyc"))
    assert timesync.status(hooks)["synchronized"] is None
    machine.tracking = machine.tracking.replace("Normal", "Not synchronised")
    machine.sources = machine.sources.replace("^*", "^?")
    assert timesync.status(machine.hooks())["synchronized"] is False


def test_health_has_ok_warn_and_not_measured_states():
    assert health.time_sync_check({"synchronized": True, "server": "x", "offset_ms": .5})["state"] == "ok"
    assert health.time_sync_check({"synchronized": False, "server": "x", "offset_ms": 2})["state"] == "warn"
    unknown = health.time_sync_check({"synchronized": None})
    assert unknown["state"] == "off" and unknown["label"] == "שעון"


def test_unsynchronized_requires_both_normal_leap_and_selected_source():
    machine = ClockMachine()
    machine.sources = machine.sources.replace("^*", "^+")
    assert timesync.status(machine.hooks())["synchronized"] is False


@pytest.fixture()
def api(tmp_path, images_root, clock):
    from server.app import create_app
    from test_sender import Recorder
    machine = ClockMachine()
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), host_hooks=machine.hooks())
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(ctx.conn, "tech", "deploy-pass-123", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")
    assert deploy.post("/api/console/login", json={"username":"tech", "password":"deploy-pass-123"}).status_code == 200
    yield admin, deploy, machine, ctx
    ctx.sender.stop()


def test_admin_can_read_change_and_check_now(api):
    admin, _, machine, ctx = api
    assert admin.get("/api/console/timesync").status_code == 200
    changed = admin.post("/api/console/timesync", json={"ntp_servers":"ntp.example.org"})
    assert changed.status_code == 200 and machine.restarts == 1
    assert ctx.conn.execute("SELECT value FROM settings WHERE key='ntp_servers'").fetchone()[0] == "ntp.example.org"
    assert admin.post("/api/console/timesync/check", json={}).json()["synchronized"] is True


@pytest.mark.parametrize("path,method", [("/api/console/timesync", "get"), ("/api/console/timesync", "post"), ("/api/console/timesync/check", "post")])
def test_deploy_role_gets_403(api, path, method):
    _, deploy, machine, _ = api
    response = getattr(deploy, method)(path, **({"json": {"ntp_servers":"x.example"}} if method == "post" else {}))
    assert response.status_code == 403 and machine.restarts == 0


def test_a_server_without_chrony_still_comes_up_and_says_why(tmp_path):
    """The NTP-server step at start-up (port 0 by default) reads
    /etc/chrony/chrony.conf. On a server where it is missing -- the restore
    drill's second server, or an updated server whose chrony did not install
    (#1803 + #1773) -- the server still comes up, and the journal names why."""
    from types import SimpleNamespace

    from server.app import _nic_services
    from server.db import connect

    def no_chrony(_nic):
        raise FileNotFoundError(2, "No such file or directory", "/etc/chrony/chrony.conf")

    ctx = SimpleNamespace(conn=connect(tmp_path / "imagectl.db"))
    service = _nic_services(ctx, None, None, None, False,
                            {"interfaces": lambda: [], "timesync": no_chrony}, apply_timesync=True)
    assert service is not None
    rows = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'timesync_apply_failed'").fetchall()
    assert len(rows) == 1 and "chrony.conf" in rows[0][0], rows
