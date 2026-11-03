"""#2105: the DHCP client must not live in the cgroup of whoever ran ifup.

``netcfg.render`` writes ``up /usr/sbin/dhcpcd -4 -b <nic>``: dhcpcd forks into
the background and stays in the cgroup of the process that ran ``ifup``. In
firstboot (#2094) that was a oneshot unit, and the journal showed it dying a
second later ("received SIGTERM, stopping ... removing interface"). The same
``ifup`` ran inside imagesrv-server (console), imagesrv-dcui ("Restart
Management Network") and the oneshot imagesrv-netrollback -- so restarting
any of them (an update restarts the server) took the management address with
it, and inside the DCUI's sandbox dhcpcd said "failed to drop privileges".

``apply_interface`` now hands ``ifup`` to a transient unit of its own
(oneshot + RemainAfterExit, as networking.service holds it at boot). These run
the real ``apply_interface`` with only command execution faked.
"""

from __future__ import annotations

from server.net import netcfg_host


def _host(monkeypatch, results: dict | None = None):
    """Record every command; ``results`` maps argv[0] (or "systemctl stop") to a
    (code, text) answer. Everything else succeeds."""
    calls: list[list[str]] = []
    results = results or {}

    def run(cmd, timeout=40):
        calls.append(list(cmd))
        key = "systemctl stop" if cmd[:2] == ["systemctl", "stop"] else cmd[0]
        return results.get(key, (0, ""))

    monkeypatch.setattr(netcfg_host, "_run", run)
    return calls


def test_ifup_runs_in_its_own_transient_unit_not_in_the_caller(monkeypatch):
    calls = _host(monkeypatch)
    assert netcfg_host.apply_interface("ens33") is None
    assert not [c for c in calls if c[0] == "ifup"], \
        f"a bare ifup leaves dhcpcd in the caller's cgroup: {calls}"
    run = next(c for c in calls if c[0] == "systemd-run")
    assert run[-2:] == ["ifup", "ens33"]
    assert "--unit=imagesrv-ifup-ens33.service" in run
    # it must outlive its ExecStart, the way networking.service does
    for prop in ("Type=oneshot", "RemainAfterExit=yes"):
        assert run[run.index(prop) - 1] == "-p", run
    assert "--scope" not in run, "a scope keeps the caller's sandbox (failed to drop privileges)"


def test_the_previous_unit_is_stopped_after_ifdown_and_before_the_new_one(monkeypatch):
    calls = _host(monkeypatch)
    assert netcfg_host.apply_interface("ens33") is None
    heads = [c[0] if c[0] != "systemctl" else " ".join(c[:2]) for c in calls]
    assert heads == ["ip", "ifdown", "systemctl stop", "systemd-run"], calls
    assert calls[2] == ["systemctl", "stop", "imagesrv-ifup-ens33.service"]


def test_no_previous_unit_is_the_ordinary_case_not_a_failure(monkeypatch):
    """First apply on this boot: the unit does not exist, `systemctl stop` says so."""
    _host(monkeypatch, {"systemctl stop": (5, "Unit imagesrv-ifup-ens33.service not loaded.")})
    assert netcfg_host.apply_interface("ens33") is None


def test_a_failed_ifup_names_the_unit_its_journal_and_why_the_stop_failed(monkeypatch):
    _host(monkeypatch, {
        "systemctl stop": (1, "Job for imagesrv-ifup-ens33.service canceled."),
        "systemd-run": (1, "Failed to start transient service unit: Unit "
                           "imagesrv-ifup-ens33.service was already loaded"),
    })
    error = netcfg_host.apply_interface("ens33")
    assert error is not None
    assert "imagesrv-ifup-ens33.service" in error
    assert "journalctl -u imagesrv-ifup-ens33.service" in error
    assert "already loaded" in error and "canceled" in error


def test_systemd_run_that_cannot_run_is_an_error_not_applied(monkeypatch):
    _host(monkeypatch, {"systemd-run": (None, "[Errno 2] No such file or directory")})
    error = netcfg_host.apply_interface("ens33")
    assert error is not None and "systemd-run" in error


def test_every_caller_reaches_the_detached_apply(monkeypatch):
    """Console, DCUI and the rollback unit all take netcfg_apply from here."""
    from server.console import netcfg as console_netcfg
    from server.dcui import actions
    assert netcfg_host.default_hooks()["netcfg_apply"] is netcfg_host.apply_interface
    assert console_netcfg.default_hooks()["netcfg_apply"] is netcfg_host.apply_interface
    assert actions.default_network_hooks()["netcfg_apply"] is netcfg_host.apply_interface


def test_dcui_restart_management_network_hands_ifup_to_systemd(monkeypatch, tmp_path):
    """The DCUI path end to end, with the real apply: only commands are faked."""
    from server.db import connect, set_setting
    from server.dcui import actions
    from server.net import netcfg

    calls = _host(monkeypatch)
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, netcfg.SETTING_PREFIX + "ens33",
                netcfg.NetConfig("ens33", mode="dhcp").to_json())
    hooks = {"netcfg_state": lambda: netcfg_host.LiveState(
                 True, addresses={"ens33": ["192.168.68.64/22"]}),
             "sleep": lambda s: None, "monotonic": lambda: 0.0}
    result = actions.restart_management_network(conn, "ens33", tmp_path, hooks=hooks)
    assert result.ok, result
    assert any(c[0] == "systemd-run" and c[-2:] == ["ifup", "ens33"] for c in calls), calls
    assert not [c for c in calls if c[0] == "ifup"]
