"""Per-round multicast egress follows the operator's NIC services.

#3173: the chunk engine (`chunksend.ChunkEngine`) is the only sender, and its card is `--iface`. The engine's own
NIC tests are in test_chunk_nic_gate_2533.py (which takes `enable` from here); what this file holds beyond them --
every reason a card cannot be settled, the override against the off switch, the startup card of a server nobody
edited, one NIC that carries every service, and the runtime's override -- is here, on the chunk engine.
"""

from types import SimpleNamespace

import pytest

import chunkcast_rig as C
from fakesend import Recorder, arg, wait_for
from server.agent.identity import LeaseFile
from server.app import _multicast_addresses, create_runtime
from server.db import set_setting
from server.net.nic_services import SERVICES, Baseline
from server.rounds import chunksend
from test_chunksend_2533 import Lib
from test_nic_services import NICS, rig  # noqa: F401 -- shared fixture

SID = "ses_2668"


def enable(svc, *names):
    for nic in NICS:
        svc._write(nic["name"], ["multicast"] if nic["name"] in names else [])


def engine_for(svc, root, addresses=(), override=None):
    """The chunk engine of one machine ("a") and one partition (`test_chunksend_2533.Lib`: partition 3, ten chunks),
    the gate and the selection wired as in `server.app`. (engine, the recorder of its `chunkcast send`)"""
    recorder = Recorder()
    engine = chunksend.ChunkEngine(
        Lib(root), live_members=lambda sid: ["a"], lost_wait=lambda: 1, write_floor=lambda: 50,
        runner=recorder, interface="ens33", max_wait=1, start_timeout=1,
        wave_bitrate=lambda session: "700m", ledger=C.MemLedger())
    engine.gate = svc.multicast_refused
    engine.select_interface = lambda session: svc.multicast_interface(
        lambda: list(addresses), override, default="ens33")
    engine.note_agent("a", ["chunk"])
    return engine, recorder


def _told(engine, sid, state, ranges):
    return engine.report(sid, "a", {"part": 3, "pass": 1, "state": state,
                                    "targets": [{"serial": "A1", "durable": [{"part": 3, "ranges": ranges}]}]})


def send_whole(engine, recorder, sid=SID):
    """The wave goes out: the machine listens, the engine sends the partition once, and the machine ends
    holding all of it -- until the engine says `done`."""
    before = len(recorder.commands)
    assert _told(engine, sid, "listening", [])["action"] == "receive"
    assert wait_for(lambda: len(recorder.commands) > before, 5), engine.status()
    assert wait_for(lambda: _told(engine, sid, "ended", [[0, 9]])["action"] != "wait", 5), engine.status()
    assert wait_for(lambda: engine.status()["state"] == "done", 5), engine.status()


def transmit(svc, root, addresses=(), override=None):
    """One wave: (the engine once its thread ended, the commands it ran). A wave whose card is settled goes out
    whole (`send_whole`); one whose card is not fails before a byte."""
    engine, recorder = engine_for(svc, root, addresses, override)
    assert engine.start({"id": SID, "image_id": "img_7f3a91"}) is None
    assert wait_for(lambda: engine.status()["state"] in ("sending", "failed"), 5), engine.status()
    if engine.status()["state"] == "sending":
        send_whole(engine, recorder)
    engine._thread.join(5)
    assert not engine._thread.is_alive()
    return engine, recorder.commands


@pytest.mark.parametrize("enabled,addresses,override,expected", [
    (("ens34",), (), None, "ens34"),
    (("ens33", "ens34"), ("10.44.0.100", "10.44.0.101"), None, "ens34"),
    (("ens33", "ens34"), ("10.10.10.100",), None, "ens33"),
    (("ens33", "ens34"), ("10.44.0.100",), "ens33", "ens33"),
])
def test_spawn_uses_selected_nic(rig, tmp_path, caplog,
                               enabled, addresses, override, expected):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], tmp_path, addresses, override)
    assert engine.status()["state"] == "done"
    assert commands
    assert all(arg(cmd, "--iface") == expected for cmd in commands)
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
def test_unverifiable_round_fails_before_spawn(rig, tmp_path, enabled, addresses, reason):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], tmp_path, addresses)
    assert not commands
    state = engine.status()
    assert state["state"] == "failed"
    assert reason in state["error"]
    if len(enabled) > 1:
        assert all(nic in state["error"] for nic in enabled)


@pytest.mark.parametrize("enabled", [(), ("ens34",)])
def test_override_does_not_bypass_the_off_switch(rig, tmp_path, enabled):
    enable(rig["svc"], *enabled)
    engine, commands = transmit(rig["svc"], tmp_path, ("10.44.0.100",), "ens33")
    assert not commands
    assert engine.status()["state"] == "failed"
    assert "ens33" in engine.status()["error"]


def test_unedited_server_keeps_its_startup_interface(rig, tmp_path):
    svc = rig["svc"]
    assert not svc.edited()
    engine, commands = transmit(svc, tmp_path)
    assert engine.status()["state"] == "done"
    assert commands and all(arg(cmd, "--iface") == "ens33" for cmd in commands)


def test_single_nic_all_services_share_one_plan_and_sender(rig, tmp_path):
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
    engine, commands = transmit(svc, tmp_path)
    assert engine.status()["state"] == "done"
    assert commands and all(arg(cmd, "--iface") == "ens34" for cmd in commands)


def test_selection_is_recomputed_after_operator_changes_services(rig, tmp_path):
    """The same engine, two waves: each one's card is chosen when it goes out, not once."""
    svc = rig["svc"]
    engine, recorder = engine_for(svc, tmp_path)
    engine.select_interface = lambda session: svc.multicast_interface(lambda: [])
    for name in ("ens34", "ens33"):
        enable(svc, name)
        assert engine.start({"id": name, "image_id": "img_7f3a91"}) is None
        send_whole(engine, recorder, sid=name)
        engine._thread.join(5)
        assert engine.status()["state"] == "done"
        assert arg(recorder.commands[-1], "--iface") == name


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
def test_runtime_wires_selection_and_override(tmp_path, enabled, override, expected):
    from conftest import MANIFEST_256, write_image
    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    leases = tmp_path / "runtime.leases"
    mac = "00:11:22:33:44:55"
    leases.write_text(f"0 {mac} 10.44.0.100 receiver *\n", encoding="utf-8")
    recorder = Recorder()
    runtime = create_runtime(tmp_path / "data", images, "http://10.10.10.8:7712",
                             interface="ens33", multicast_interface_override=override,
                             sender_runner=recorder, nic_hooks={"interfaces": lambda: NICS},
                             identity_hooks={"leases": LeaseFile(leases)},
                             health_hooks={"interfaces": lambda: NICS})
    enable(runtime.ctx.nic_services, *enabled)
    conn = runtime.conn
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g2668', 'test', 'cloner')")
    conn.commit()
    sid = runtime.ctx.store.open("grp_CLONERS", "img_7f3a91", "ROOM", 1, "test")   # a wave of the cloning room: the only one (#3173)
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, '')", (sid, mac))
    conn.commit()
    chunk = runtime.ctx.chunk
    # This test needs no room/disk setup; keep the real engine and NIC wiring.
    chunk.wave_bitrate = lambda session: "700m"
    chunk.pending_prompts = None
    chunk.note_agent(mac, ["chunk"])                    # what its hello would have declared
    assert chunk.start({"id": sid, "image_id": "img_7f3a91"}) is None
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    try:
        # the receiver listens (no ledger before the first byte, as `fakesend.Cloners.listen`), and the pass goes out
        assert wait_for(lambda: chunk.report(sid, mac, {
            "part": first, "pass": 1, "state": "listening",
            "targets": [{"serial": "A1", "write_mbps": 150}]}).get("action") == "receive", 5), chunk.status()
        assert wait_for(lambda: recorder.commands, 10), chunk.status()
        assert all(arg(cmd, "--iface") == expected for cmd in recorder.commands)
        assert chunk.status()["state"] == "sending", chunk.status()   # it went out, and did not fail
    finally:
        chunk.stop(sid)
        chunk.close()
