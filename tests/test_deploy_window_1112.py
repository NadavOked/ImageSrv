"""Cluster D, 12/11 -- the deploy network window, server side.

* #2883 (fd1111, v0.91.10): moving an existing deploy network to another address
  while a wave was writing -- no warning, no server restart; the wave hung at 98%,
  GRUB, ``deploy:url`` and the running server stayed on the old address, PXE failed.
  Now: refused by name while a round or a capture runs, and a move finishes like the
  first setup (#1088): grub.cfg, firewall, ``deploy:url``, server restart.
* #2824 (ESXi, 10/11): a static management card without DNS has no /etc/resolv.conf;
  ``read_state`` called that "not read", and every deploy save armed the rollback.
  And ``/second`` on a disabled card failed on ``link_down`` like #2805 did.
* #2891: the "probe did not run" refusal on ``/second`` pointed at an API field
  (``ignore_existing``) that the window had no way to send.
* #2889: a range that ends on the broadcast address and a lease of 99999999 hours
  were accepted.
* #2371: the nested ``interface.mismatches`` without the seconds waited; ``/second``
  not refused while another card waits for a lease; the DCUI's fixed "60 seconds".

Nothing here touches the machine: every hook is injected (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from server.net import deploy_net, dhcp, netcfg, netcfg_host  # noqa: E402
from test_deploy_bond_1264 import Machine as BondMachine  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONF = "/api/console/net/config"
FIRST = {"network": "10.44.9.0", "netmask": "255.255.255.0", "range_start": "10.44.9.50",
         "range_end": "10.44.9.200", "lease": "12h", "confirm": "eth1"}
MOVED = {**FIRST, "network": "10.44.30.0", "range_start": "10.44.30.50", "range_end": "10.44.30.200"}


class Machine(BondMachine):
    """The bond test's machine, plus the admin state of each card (#2805/#2824):
    after an install from the ISO the free cards are down, and a probe on a down
    card fails with ENETDOWN (``link_down``)."""

    def __init__(self):
        super().__init__()
        self.admin_down: set[str] = set()
        self.links: list[tuple[str, bool]] = []

    def interfaces(self):
        rows = super().interfaces()
        for row in rows:
            down = row["name"] in self.admin_down
            row.update(admin_up=not down, link_up=not down)
        return rows

    def link_set(self, name, up):
        self.links.append((name, up))
        (self.admin_down.discard if up else self.admin_down.add)(name)
        return None


@pytest.fixture()
def box(tmp_path: Path, images_root: Path, clock):
    """The deploy network set up from the console on eth1 (10.44.9.1/24), the
    console on eth0 (management), eth2/eth3 free -- as in test_deploy_bond_1264."""
    from server.app import create_app
    from server.console import users

    m = Machine()
    fake = {"deploy_calls": [], "applied": [], "local": "10.1.2.3",
            "probe": dhcp.ProbeResult(True, ()), "raised_probe": dhcp.ProbeResult(True, ()),
            "probed": [], "slept": []}

    def probe(name):
        fake["probed"].append(name)
        if name in m.admin_down:
            return dhcp.ProbeResult(False, reason="link_down")
        return fake["raised_probe"] if (name, True) in m.links else fake["probe"]

    module_conf = tmp_path / "modules-load.d" / "imagesrv-bond.conf"
    dhcp_hooks = {
        "interfaces": m.interfaces, "probe": probe, "link_set": m.link_set,
        "sleep": fake["slept"].append,
        "apply": lambda text: fake["applied"].append(text) or None,
        "apply_proxy": lambda text, active: None,
        "read_active_conf": lambda: "", "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
        "netcfg_management": lambda: "eth0",
    }
    netcfg_hooks = {
        "interfaces": m.interfaces,
        "netcfg_read_conf": lambda name, root=None: m.confs.get(name),
        "netcfg_write_conf": lambda name, text, root=None: (
            m.confs.pop(name, None) if text is None else m.confs.__setitem__(name, text)) and None,
        "netcfg_write_resolv": lambda text, path=None: None,
        "netcfg_apply": m.apply, "netcfg_down": m.down, "netcfg_state": m.state,
        "netcfg_bond_state": m.bond_state, "netcfg_bond_supported": lambda: True,
        "netcfg_read_bond_module": lambda: netcfg_host.read_bond_module(module_conf),
        "netcfg_write_bond_module": lambda text: netcfg_host.write_bond_module(text, module_conf),
        "netcfg_main_defines": lambda name: False,
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: (True, "active"),
        "netcfg_now": clock, "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: fake["local"],
        "netcfg_peer_address": lambda request: "10.1.2.50",
        "netcfg_foreign_manager": lambda: None,
        "netcfg_management": lambda: "eth0",
    }
    deploy_hooks = {
        "write_grub_cfg": lambda url, root: fake["deploy_calls"].append(("grub", url)) or None,
        "enable_dnsmasq": lambda: fake["deploy_calls"].append(("dnsmasq",)) or None,
        "firewall": lambda repo, **kw: fake["deploy_calls"].append(("fw", kw["deploy_if"])) or None,
        "restart_server": lambda: fake["deploy_calls"].append(("restart",)) or None,
    }
    ctx = deploy_net.DeployContext(state=deploy_net.DeployState("none", None, None), agent_port=8080,
                                   tftp_root=tmp_path / "tftp", repo_dir=REPO,
                                   servers_interface="eth0", hooks=deploy_hooks)
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=dhcp_hooks, netcfg_hooks=netcfg_hooks, deploy=ctx,
                     netcfg_state_dir=tmp_path / "netstate")
    users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
    first = client.put(f"{CONF}/eth1/deploy", json=FIRST)
    assert first.status_code == 200 and first.json()["deploy"]["ok"], first.text
    assert deploy_net.stored(app.state.ctx.conn) == ("eth1", "http://10.44.9.1:8080")
    fake["applied"].clear(); fake["deploy_calls"].clear(); fake["probed"].clear(); m.calls.clear()
    return SimpleNamespace(client=client, conn=app.state.ctx.conn, m=m, fake=fake, deploy=ctx,
                           clock=clock)


def _offer(box, name="eth1") -> dhcp.InterfaceConfig:
    row = box.conn.execute("SELECT value FROM settings WHERE key = ?",
                           (dhcp.SETTING_PREFIX + name,)).fetchone()
    return dhcp.InterfaceConfig.from_json(name, row["value"] if row else None)


def _events(box) -> list[str]:
    return [r["event"] for r in box.conn.execute("SELECT event FROM journal ORDER BY rowid")]


# --- #2883: moving the deploy network --------------------------------------------


def test_moving_the_deploy_network_finishes_on_the_new_address(box):
    """The fd1111 save, without a round: the card, the DHCP **and** grub.cfg,
    the firewall, ``deploy:url`` and a server restart -- all on 10.44.30.1."""
    r = box.client.put(f"{CONF}/eth1/deploy", json=MOVED)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["server_ip"] == "10.44.30.1", body
    assert body["deploy"]["ok"] is True and body["deploy"]["url"] == "http://10.44.30.1:8080"
    assert "    address 10.44.30.1" in box.m.confs["eth1"]
    assert _offer(box).server_ip == "10.44.30.1"
    assert ("grub", "http://10.44.30.1:8080") in box.fake["deploy_calls"]
    assert ("restart",) in box.fake["deploy_calls"], "the server restarts on the new address"
    assert deploy_net.stored(box.conn) == ("eth1", "http://10.44.30.1:8080")
    assert box.deploy.state.url == "http://10.44.30.1:8080"
    assert _events(box)[-1] == "deploy_net_set"


def test_a_save_that_keeps_the_address_does_not_restart_anything(box):
    """Only the range changed: dnsmasq, and nothing of the completion."""
    r = box.client.put(f"{CONF}/eth1/deploy", json={**FIRST, "range_end": "10.44.9.150"})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert "deploy" not in r.json()
    assert box.fake["deploy_calls"] == [] and len(box.fake["applied"]) == 1


def test_a_failed_completion_of_the_move_is_named_and_not_recorded(box):
    box.deploy.hooks["firewall"] = lambda repo, **kw: "nft -c נפל"
    r = box.client.put(f"{CONF}/eth1/deploy", json=MOVED)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["deploy"]["ok"] is False
    assert "השלמת הכתובת החדשה נכשלה" in body["apply_error"] and "nft -c נפל" in body["apply_error"]
    assert deploy_net.stored(box.conn) == ("eth1", "http://10.44.9.1:8080"), "not recorded"
    assert "deploy_net_failed" in _events(box)


def _running_session(box):
    group = box.conn.execute("SELECT id FROM groups LIMIT 1").fetchone()["id"]
    box.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at) VALUES"
        " ('s1', ?, 'img', 'p', 1, 60, 'running', 'noc', '2026-11-11T18:45:00', 0)", (group,))
    box.conn.commit()


def _active_room_round(box):
    box.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
                     " VALUES ('r1', 'img', 1, 'active', 'noc', '2026-11-11T18:45:00+00:00')")
    box.conn.commit()


@pytest.mark.parametrize("busy, needle", [(_running_session, "יש סבב הפצה פתוח או רץ"),
                                          (_active_room_round, "יש סבב חדר פתוח או רץ")])
def test_moving_the_deploy_network_during_a_round_is_refused_and_nothing_moves(box, busy, needle):
    busy(box)
    confs = dict(box.m.confs)
    r = box.client.put(f"{CONF}/eth1/deploy", json=MOVED)
    assert r.status_code == 409, r.text
    assert needle in r.json()["detail"] and "לא שונה דבר" in r.json()["detail"]
    assert box.m.confs == confs and box.m.calls == []
    assert box.fake["applied"] == [] and box.fake["deploy_calls"] == []
    assert _offer(box).server_ip == "10.44.9.1"
    assert deploy_net.stored(box.conn) == ("eth1", "http://10.44.9.1:8080")
    # the range alone may change during a round -- the address does not move
    keep = box.client.put(f"{CONF}/eth1/deploy", json={**FIRST, "range_end": "10.44.9.150"})
    assert keep.status_code == 200, keep.text


def test_a_move_that_armed_the_rollback_finishes_on_the_save_after_the_confirmation(box):
    """#2970 + #2883: the console's card unknown -> the move arms #56 and stops before
    the DHCP. After the confirmation the address is already stored (`changed` is
    empty) -- the save that goes on to the DHCP is the one that finishes the move."""
    box.fake["local"] = ""
    first = box.client.put(f"{CONF}/eth1/deploy", json=MOVED)
    assert first.status_code == 200, first.text
    assert first.json()["address"]["rollback"]["pending"] is True and first.json()["dhcp"] is None
    assert box.fake["deploy_calls"] == []
    assert box.client.post(f"{CONF}/confirm", json={"interface": "eth1"}).status_code == 200
    again = box.client.put(f"{CONF}/eth1/deploy", json=MOVED)
    assert again.status_code == 200, again.text
    assert again.json()["ok"] is True and again.json()["deploy"]["url"] == "http://10.44.30.1:8080"
    assert deploy_net.stored(box.conn) == ("eth1", "http://10.44.30.1:8080")


# --- #2824: no resolv.conf is "no DNS", and /second raises a disabled card ------


def test_a_missing_resolv_conf_is_no_dns_not_an_unread_state(tmp_path, monkeypatch):
    """A static management card without DNS: setup-boot-server writes no
    /etc/resolv.conf (#2138). That is an answer -- no name servers -- and not a
    read that failed; an unreadable file still is."""
    answers = {"addr": (0, "2: eth0    inet 10.1.2.3/24 brd 10.1.2.255 scope global eth0"),
               "route": (0, "default via 10.1.2.254 dev eth0")}
    monkeypatch.setattr(netcfg_host, "_run",
                        lambda cmd, timeout=10: answers["addr" if "addr" in cmd else "route"])
    state = netcfg_host.read_state(tmp_path / "no-resolv.conf")
    assert state.checked is True, state.reason
    assert state.nameservers == [] and state.addresses == {"eth0": ["10.1.2.3/24"]}
    unreadable = tmp_path / "dir-not-file"
    unreadable.mkdir()
    assert netcfg_host.read_state(unreadable).checked is False


def test_a_disabled_second_card_is_raised_and_probed_on_the_live_link(box):
    box.m.admin_down.add("eth2")
    r = box.client.put(f"{CONF}/eth1/second", json={"second": "eth2"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["interface"] == "bond0"
    assert box.m.links == [("eth2", True)]
    assert box.fake["probed"] == ["eth2", "eth2"], "once down (link_down), once on the raised link"


@pytest.mark.parametrize("raised, needle", [
    (dhcp.ProbeResult(True, ("192.168.5.1",)), "נמצא שרת DHCP פעיל על eth2"),
    (dhcp.ProbeResult(False, reason="os_error"), "הבדיקה עצמה לא רצה"),
])
def test_a_raised_second_card_that_is_not_clean_is_refused_and_lowered(box, raised, needle):
    box.m.admin_down.add("eth2")
    box.fake["raised_probe"] = raised
    r = box.client.put(f"{CONF}/eth1/second", json={"second": "eth2"})
    assert r.status_code == 409, r.text
    assert needle in r.json()["detail"]
    assert box.m.links == [("eth2", True), ("eth2", False)], "back to disabled"
    assert set(box.m.confs) == {"eth1"} and box.fake["deploy_calls"] == []


def test_a_raised_second_card_refused_later_is_lowered_too(box):
    """The request came through the deploy network -- refused after the probe raised eth2."""
    box.m.admin_down.add("eth2")
    box.fake["local"] = "10.44.9.1"
    r = box.client.put(f"{CONF}/eth1/second", json={"second": "eth2"})
    assert r.status_code == 409 and "כרטיס הניהול" in r.json()["detail"], r.text
    assert box.m.links == [("eth2", True), ("eth2", False)]


# --- #2891: the refusal names the window's checkbox ------------------------------


def test_a_probe_that_did_not_run_points_at_the_checkbox_in_the_window(box):
    from server.console import netcfg as console_netcfg

    label = "הכרטיס השני מחובר רק לרשת ההפצה — לדלג על הבדיקה"
    box.fake["probe"] = dhcp.ProbeResult(False, reason="port_busy")
    r = box.client.put(f"{CONF}/eth1/second", json={"second": "eth2"})
    assert r.status_code == 409, r.text
    # the refusal first (behaviour): before #2891 it said "שלחו ignore_existing", a field
    # the window had no way to send
    assert f'סמנו בחלון "{label}" ושמרו שוב' in r.json()["detail"], r.json()["detail"]
    assert getattr(console_netcfg, "SECOND_ONLY_DEPLOY_LABEL", None) == label
    js = (REPO / "server" / "static" / "net.js").read_text(encoding="utf-8")
    assert f'const SECOND_ONLY_DEPLOY_LABEL = "{label}";' in js, \
        "the window shows the label the refusal quotes"
    # and the box the label names is the way on
    ok = box.client.put(f"{CONF}/eth1/second", json={"second": "eth2", "ignore_existing": True})
    assert ok.status_code == 200 and ok.json()["ok"] is True, ok.text


# --- #2889: the range and the lease ----------------------------------------------


def _offer_cfg(**over) -> dhcp.InterfaceConfig:
    base = dict(name="eth1", enabled=True, range_start="10.111.1.100", range_end="10.111.1.200",
                netmask="255.255.255.0", server_ip="10.111.1.1", lease="12h")
    return dhcp.InterfaceConfig(**{**base, **over})


@pytest.mark.parametrize("over, needle", [
    ({"range_end": "10.111.1.255"}, "כתובת השידור (10.111.1.255)"),
    ({"range_start": "10.111.1.0", "server_ip": "10.111.1.254"}, "כתובת הרשת (10.111.1.0)"),
    ({"lease": "99999999h"}, "עד 365 ימים"),
    ({"lease": "366d"}, "עד 365 ימים"),
])
def test_a_range_on_the_broadcast_or_network_address_and_an_endless_lease_are_refused(over, needle):
    with pytest.raises(ValueError, match=needle.replace("(", r"\(").replace(")", r"\)")):
        dhcp.validate(_offer_cfg(**over))


@pytest.mark.parametrize("over", [{"range_end": "10.111.1.254"}, {"lease": "365d"},
                                  {"lease": "8760h"}, {"range_start": "10.111.1.120",
                                                       "range_end": "10.111.1.120"}])
def test_the_edges_that_are_fine_stay_fine(over):
    dhcp.validate(_offer_cfg(**over))


def test_the_deploy_save_refuses_the_broadcast_address_before_anything_moves(box):
    r = box.client.put(f"{CONF}/eth1/deploy", json={**FIRST, "range_end": "10.44.9.255"})
    assert r.status_code == 400 and "כתובת השידור" in r.json()["detail"], r.text
    assert box.fake["applied"] == [] and box.m.calls == []


# --- #2371 -----------------------------------------------------------------------


def test_the_nested_mismatches_carry_the_seconds_waited(tmp_path, images_root, clock):
    from test_dhcp_readback_wait_2349 import DHCP_BODY, Machine as LeaseMachine, _app

    machine = LeaseMachine(lease_after=None)
    _, admin = _app(tmp_path, images_root, clock, machine, fake_time=True)
    result = admin.put(f"{CONF}/eth1", json=DHCP_BODY).json()
    assert result["ok"] is False
    assert result["interface"]["mismatches"] == result["mismatches"], result
    assert f"אחרי {netcfg_host.DHCP_LEASE_WAIT_S} שניות" in result["interface"]["mismatches"][0]


def test_second_is_refused_while_another_card_waits_for_a_lease(tmp_path, images_root, clock, monkeypatch):
    from test_dhcp_readback_wait_2349 import Machine as LeaseMachine, _app, _during_the_wait

    monkeypatch.setattr(netcfg_host, "DHCP_LEASE_WAIT_S", 3)
    machine = LeaseMachine(lease_after=None)
    app, admin = _app(tmp_path, images_root, clock, machine, fake_time=False)
    _order, second, first = asyncio.run(_during_the_wait(
        app, machine, dict(admin.cookies),
        lambda c: c.put(f"{CONF}/eth1/second", json={"second": "eth2"})))
    assert second.status_code == 409, second.text
    assert "ממתינה לכתובת מ-DHCP" in second.json()["detail"], second.json()["detail"]
    assert first.json()["ok"] is True


def test_the_dcui_says_how_many_seconds_are_left_after_the_lease_wait(tmp_path):
    from server.db import connect
    from server.dcui import actions
    from server.dcui.view import he
    from test_dhcp_readback_wait_2349 import Machine as LeaseMachine

    machine = LeaseMachine(lease_after=10)          # the DCUI polls once a second: 10 s
    clock = {"now": 0.0}
    hooks = {
        "netcfg_timer_active": lambda: (True, "active"),
        "now": lambda: 1000.0 + clock["now"],        # wall clock moves with the wait
        "boot_id": lambda: "boot-A", "netcfg_read_conf": lambda name, root: "",
        "read_resolv": lambda: "", "netcfg_write_conf": lambda name, text, root: None,
        "netcfg_write_resolv": lambda text, path: None, "netcfg_apply": lambda name: None,
        "netcfg_state": machine.state, "hostname": lambda: "imagesrv-test",
        "sleep": lambda s: clock.__setitem__("now", clock["now"] + s),
        "monotonic": lambda: clock["now"],
    }
    form = {"interface": "eth1", "mode": "dhcp", "address": "",
            "netmask": "255.255.255.0", "gateway": "", "dns": ""}
    result = actions.apply_network(connect(tmp_path / "imagesrv.db"), form, tmp_path, {"eth1"}, hooks)
    assert result.ok, result
    waited = int(clock["now"])
    assert waited == 10
    assert result.message == f"Network change applied; confirm within {60 - waited} seconds."
    assert he(result.message) == f"השינוי הוחל. מאשרים תוך {60 - waited} שניות."
