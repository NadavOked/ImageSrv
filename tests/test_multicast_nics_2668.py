"""Per-round multicast egress follows the operator's NIC services."""

from types import SimpleNamespace

import pytest

from server.agent.identity import LeaseFile
from server.app import _multicast_addresses, create_runtime
from server.db import set_setting
from server.net.nic_services import SERVICES, Baseline
from server.rounds.sender import SenderEngine
from test_nic_services import NICS, rig  # noqa: F401 -- shared fixture
from test_sender import Recorder, free_ports, library  # noqa: F401 -- shared fixtures


def enable(svc, *names):
    for nic in NICS:
        svc._write(nic["name"], ["multicast"] if nic["name"] in names else [])


def transmit(svc, library, addresses=(), override=None):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, interface="ens33")
    engine.gate = svc.multicast_refused
    engine.select_interface = lambda session: svc.multicast_interface(
        lambda: list(addresses), override, default="ens33")
    assert engine.start({"id": "ses_2668", "image_id": "img_7f3a91", "joined": 1}) is None
    engine._thread.join(5)
    assert not engine._thread.is_alive()
    return engine, recorder.commands


@pytest.mark.parametrize("enabled,addresses,override,expected", [
    (("ens34",), (), None, "ens34"),
    (("ens33", "ens34"), ("10.44.0.100", "10.44.0.101"), None, "ens34"),
    (("ens33", "ens34"), ("10.10.10.100",), None, "ens33"),
    (("ens33", "ens34"), ("10.44.0.100",), "ens33", "ens33"),
])
def test_spawn_uses_selected_nic(rig, library, free_ports, caplog,
                               enabled, addresses, override, expected):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], library, addresses, override)
    assert engine.status()["state"] == "done"
    assert commands
    assert all(cmd[cmd.index("--interface") + 1] == expected for cmd in commands)
    assert engine.link()["interface"] == expected
    if override:
        assert "explicit --interface ens33 overrides" in caplog.text


@pytest.mark.parametrize("enabled,addresses,reason", [
    ((), (), "no NIC has multicast enabled"),
    (("ens33", "ens34"), (), "no receiver addresses"),
    (("ens33", "ens34"), (None,), "missing or invalid"),
    (("ens33", "ens34"), ("192.0.2.10",), "no unique NIC"),
    (("ens33", "ens34"), ("10.44.0.100", "10.10.10.100"), "span multiple NICs"),
])
def test_unverifiable_round_fails_before_spawn(rig, library, free_ports,
                                            enabled, addresses, reason):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], library, addresses)
    assert not commands
    state = engine.status()
    assert state["state"] == "failed"
    assert reason in state["error"]
    if len(enabled) > 1:
        assert all(nic in state["error"] for nic in enabled)


@pytest.mark.parametrize("enabled", [(), ("ens34",)])
def test_override_does_not_bypass_the_off_switch(rig, library, free_ports, enabled):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], library, ("10.44.0.100",), "ens33")
    assert not commands
    assert engine.status()["state"] == "failed"
    assert "ens33" in engine.status()["error"]


def test_unedited_server_keeps_its_startup_interface(rig, library, free_ports):
    svc = rig["svc"]
    assert not svc.edited()
    engine, commands = transmit(svc, library)
    assert engine.status()["state"] == "done"
    assert commands and all(cmd[cmd.index("--interface") + 1] == "ens33" for cmd in commands)


def test_single_nic_all_services_share_one_plan_and_sender(rig, library, free_ports):
    svc = rig["svc"]
    svc.hooks["interfaces"] = lambda: [NICS[1]]
    svc.base = Baseline(console="ens34", deploy="ens34", interserver="ens34")
    svc._write("ens34", list(SERVICES))
    plan = svc.current_plan()
    assert plan["hosts"] == {"http_console": ["10.44.0.1"],
                             "http_boot": ["10.44.0.1"],
                             "interserver": ["10.44.0.1"]}
    assert plan["nics"] == {service: ["ens34"] for service in SERVICES}
    assert svc.netboot() == (["ens34"], ["ens34"])
    engine, commands = transmit(svc, library)
    assert engine.status()["state"] == "done"
    assert commands and all(cmd[cmd.index("--interface") + 1] == "ens34" for cmd in commands)


def test_selection_is_recomputed_after_operator_changes_services(rig, library, free_ports):
    svc = rig["svc"]
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, interface="ens33")
    engine.select_interface = lambda session: svc.multicast_interface(lambda: [])
    for name in ("ens34", "ens33"):
        enable(svc, name)
        engine.start({"id": name, "image_id": "img_7f3a91"})
        engine._thread.join(5)
        assert engine.status()["state"] == "done"
        assert recorder.commands[-1][recorder.commands[-1].index("--interface") + 1] == name


@pytest.mark.parametrize("fault", ["missing_nic", "corrupt_services", "overlapping_subnets"])
def test_bad_network_evidence_is_not_a_default_route(rig, fault):
    svc = rig["svc"]
    enable(svc, "ens33", "ens34")
    if fault == "missing_nic":
        svc.hooks["interfaces"] = lambda: [NICS[0]]
    elif fault == "corrupt_services":
        set_setting(rig["conn"], "nicsvc:ens34", "broken")
    else:
        svc.hooks["interfaces"] = lambda: [NICS[1], {**NICS[0], "addresses": ["10.44.0.2/24"]}]
    with pytest.raises(ValueError, match="Multicast refused"):
        svc.multicast_interface(lambda: ["10.44.0.100"])


@pytest.fixture()
def members(rig, tmp_path):
    conn = rig["conn"]
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g2668', 'test', 'cloner')")
    conn.commit()
    from server.rounds.sessions import SessionStore
    sid = SessionStore(conn).open("g2668", "img_7f3a91", "TEST", 2, "test")
    mac = "00:11:22:33:44:55"
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, '')", (sid, mac))
    conn.execute("INSERT INTO net_devices (mac, ip) VALUES (?, ?)", (mac, "10.10.10.100"))
    conn.commit()
    path = tmp_path / "dnsmasq.leases"
    path.write_text(f"0 {mac} 10.44.0.100 host *\n", encoding="utf-8")
    return SimpleNamespace(conn=conn, leases=LeaseFile(path)), sid, path


def test_round_uses_lease_instead_of_old_hello_address(rig, members):
    ctx, sid, _ = members
    enable(rig["svc"], "ens33", "ens34")
    assert rig["svc"].multicast_interface(lambda: _multicast_addresses(ctx, sid)) == "ens34"
    ctx.leases = None
    assert rig["svc"].multicast_interface(lambda: _multicast_addresses(ctx, sid)) == "ens33"


@pytest.mark.parametrize("missing", [False, True])
def test_unreadable_or_missing_lease_cannot_use_stale_hello(rig, members, missing):
    ctx, sid, path = members
    if missing:
        path.unlink()
    else:
        path.write_text("", encoding="utf-8")
    enable(rig["svc"], "ens33", "ens34")
    with pytest.raises(ValueError, match="missing or invalid receiver address"):
        rig["svc"].multicast_interface(lambda: _multicast_addresses(ctx, sid))


@pytest.mark.parametrize("enabled,override,expected", [
    (("ens34",), None, "ens34"),
    (("ens33", "ens34"), None, "ens34"),
    (("ens33", "ens34"), "ens33", "ens33"),
])
def test_runtime_wires_selection_and_override(tmp_path, free_ports, enabled, override, expected):
    from conftest import MANIFEST_256, write_image
    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    leases = tmp_path / "runtime.leases"
    mac = "00:11:22:33:44:55"
    leases.write_text(f"0 {mac} 10.44.0.100 receiver *\n", encoding="utf-8")
    recorder = Recorder()
    runtime = create_runtime(tmp_path / "data", images, "http://10.10.10.8:8080",
                             interface="ens33", multicast_interface_override=override,
                             sender_runner=recorder, nic_hooks={"interfaces": lambda: NICS},
                             identity_hooks={"leases": LeaseFile(leases)},
                             health_hooks={"interfaces": lambda: NICS})
    enable(runtime.ctx.nic_services, *enabled)
    conn = runtime.conn
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g2668', 'test', 'cloner')")
    conn.commit()
    sid = runtime.ctx.store.open("g2668", "img_7f3a91", "TEST", 1, "test")
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, '')", (sid, mac))
    conn.commit()
    sender = runtime.ctx.sender
    # This test needs no room/disk setup; keep the real sender and NIC wiring.
    sender.wave_bitrate = None
    sender.pending_prompts = None
    sender.start({"id": sid, "image_id": "img_7f3a91"})
    sender._thread.join(5)
    assert recorder.commands
    assert all(cmd[cmd.index("--interface") + 1] == expected for cmd in recorder.commands)
    assert sender.status()["state"] == "done"
