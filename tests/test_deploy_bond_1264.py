"""‏#1264 — רשת ההפצה על שני כרטיסים: bond במצב balance-alb, כתובת אחת.

הכרעת נדב (10–11/10): שני הכרטיסים **פעילים במקביל** על ה-‎.1 של רשת
ההפצה, ואם אחד נופל השני ממשיך. בלי הגדרה בסוויץ'.

מה נבדק כאן:
- ‏`netcfg`: קובץ ה-bond (‏`bond-slaves`/‏`bond-mode balance-alb`/‏`bond-miimon`),
  ובעיות הגדרה של bond. כרטיס יחיד — הקובץ **בלי שינוי**.
- ‏`netcfg_host`: קריאת `/proc/net/bonding/<bond>` — ‏`checked=False` הוא "לא
  נבדק", ולעולם לא "פעיל" (עיקרון 5).
- ‏`PUT /api/console/net/config/{שם}/second`: הוספה והסרה על מכונה מדומה —
  הכתובת, ה-DHCP, ‏`deploy:interface`, חומת האש ו-dnsmasq עוברים לשם החדש;
  כל הסירובים קורים **לפני** שנוגעים במשהו; קריאה חוזרת שנכשלה מחזירה את
  הקבצים הקודמים. בבדיקות הכול hooks — שום דבר לא נוגע ברשת של המכונה.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server.net import deploy_net, dhcp, netcfg, netcfg_host

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

REPO = Path(__file__).resolve().parent.parent

BONDING_TEXT = """Ethernet Channel Bonding Driver: v6.12.0

Bonding Mode: adaptive load balancing
Primary Slave: None
Currently Active Slave: eth1
MII Status: up
MII Polling Interval (ms): 100

Slave Interface: eth1
MII Status: up
Speed: 1000 Mbps

Slave Interface: eth2
MII Status: down
Speed: Unknown
"""


# --- netcfg: הקובץ ---------------------------------------------------------------


def test_bond_renders_slaves_mode_and_link_polling_on_the_bond_only():
    cfg = netcfg.NetConfig(name="bond0", mode="static", address="10.44.9.1",
                           netmask="255.255.255.0", slaves=["eth1", "eth2"])
    text = netcfg.render(cfg)
    assert "auto bond0\niface bond0 inet static\n" in text
    assert "    bond-slaves eth1 eth2\n" in text
    assert "    bond-mode balance-alb\n" in text
    assert "    bond-miimon 100\n" in text
    single = netcfg.render(netcfg.NetConfig(name="eth1", mode="static",
                                            address="10.44.9.1", netmask="255.255.255.0"))
    assert "bond" not in single, "one card — the file is exactly what it was before #1264"


def test_slaves_round_trip_and_old_settings_have_none():
    cfg = netcfg.NetConfig(name="bond0", mode="static", address="10.44.9.1",
                           slaves=["eth1", "eth2"])
    assert netcfg.NetConfig.from_json("bond0", cfg.to_json()).slaves == ["eth1", "eth2"]
    old = json.dumps({"mode": "static", "address": "10.44.9.1"})
    assert netcfg.NetConfig.from_json("eth1", old).slaves == []


@pytest.mark.parametrize("mode,slaves,needle", [
    ("dhcp", ["eth1", "eth2"], "כתובת סטטית"),
    ("static", ["eth1"], "בדיוק שני כרטיסים"),
    ("static", ["eth1", "eth1"], "בדיוק שני כרטיסים"),
    ("static", ["eth1", "eth 2"], "eth 2"),
])
def test_bond_problems(mode, slaves, needle):
    cfg = netcfg.NetConfig(name="bond0", mode=mode, address="10.44.9.1" if mode == "static" else "",
                           slaves=slaves)
    assert any(needle in p for p in netcfg.problems(cfg, [cfg])), netcfg.problems(cfg, [cfg])


# --- netcfg_host: הראיה ----------------------------------------------------------


def test_parse_bonding_reads_the_mode_and_each_member_link():
    bond = netcfg_host.parse_bonding(BONDING_TEXT)
    assert bond.checked and bond.mode == "adaptive load balancing" and bond.mii == "up"
    assert bond.slaves == {"eth1": "up", "eth2": "down"}
    assert not netcfg_host.parse_bonding("").checked, "empty is not an empty bond"


def test_read_bond_that_cannot_be_read_is_unchecked(tmp_path):
    bond = netcfg_host.read_bond("bond0", tmp_path)
    assert bond.checked is False and "bond0" in bond.reason


def test_bond_mismatches_unchecked_is_never_a_match():
    cfg = netcfg.NetConfig(name="bond0", mode="static", address="10.44.9.1",
                           slaves=["eth1", "eth2"])
    state = netcfg_host.LiveState(True, {"bond0": ["10.44.9.1/24"]})
    assert netcfg_host.bond_mismatches(cfg, netcfg_host.parse_bonding(BONDING_TEXT), state) == [], \
        "a pulled cable is a state (down), not a configuration that did not take"
    assert netcfg_host.bond_mismatches(cfg, netcfg_host.BondState(False, reason="x"), state)
    wrong = netcfg_host.BondState(True, "load balancing (round-robin)", "up", {"eth1": "up"})
    gaps = " · ".join(netcfg_host.bond_mismatches(cfg, wrong, state))
    assert "balance-alb" in gaps and "eth2 אינו חבר" in gaps
    leaked = netcfg_host.LiveState(True, {"bond0": ["10.44.9.1/24"], "eth2": ["10.44.9.1/24"]})
    assert any("eth2 נושא כתובת" in g for g in netcfg_host.bond_mismatches(
        cfg, netcfg_host.parse_bonding(BONDING_TEXT), leaked))


def test_main_defines_three_states(tmp_path):
    main = tmp_path / "interfaces"
    main.write_text("auto lo\niface lo inet loopback\nallow-hotplug eth2\niface eth2 inet dhcp\n")
    assert netcfg_host.main_defines("eth2", main) is True
    assert netcfg_host.main_defines("eth3", main) is False
    assert netcfg_host.main_defines("eth2", tmp_path / "absent") is False
    assert netcfg_host.main_defines("eth2", tmp_path) is None, "unreadable is not 'not defined'"


# --- ה-API, על מכונה מדומה --------------------------------------------------------


class Machine:
    """מכונה מדומה: מה ש-ifupdown + ifenslave היו עושים לקבצים שנכתבו."""

    def __init__(self):
        self.nics = {"eth0": ["10.1.2.3/24"], "eth1": [], "eth2": [], "eth3": []}
        self.confs: dict[str, str] = {}
        self.bond: dict | None = None            # {"slaves": [...], "mode": ...}
        self.bond_readable = True
        self.bond_link = {"eth1": "up", "eth2": "up"}
        self.calls: list[tuple] = []

    def interfaces(self):
        return [{"name": n, "state": "up", "mac": f"aa:00:00:00:00:{i:02x}",
                 "addresses": list(a)} for i, (n, a) in enumerate(self.nics.items())]

    def down(self, name):
        self.calls.append(("down", name))
        if name == "bond0" and self.bond:
            self.bond = None
            self.nics.pop("bond0", None)
        elif name in self.nics:
            self.nics[name] = []
        return None

    def apply(self, name):
        self.calls.append(("apply", name))
        text = self.confs.get(name)
        if not text:
            return f"{name}: no stanza"
        address = re.search(r"^    address (\S+)", text, re.M).group(1)
        slaves = re.search(r"^    bond-slaves (.+)$", text, re.M)
        if slaves:
            members = slaves.group(1).split()
            self.bond = {"slaves": members,
                         "mode": re.search(r"^    bond-mode (\S+)", text, re.M).group(1)}
            for member in members:
                self.nics[member] = []
        self.nics[name] = [f"{address}/24"]
        return None

    def bond_state(self, name):
        if not self.bond_readable or self.bond is None:
            return netcfg_host.BondState(False, reason=f"/proc/net/bonding/{name}: No such file")
        mode = "adaptive load balancing" if self.bond["mode"] == "balance-alb" else self.bond["mode"]
        return netcfg_host.BondState(True, mode, "up",
                                     {s: self.bond_link.get(s, "up") for s in self.bond["slaves"]})

    def state(self):
        return netcfg_host.LiveState(True, {n: list(a) for n, a in self.nics.items()})


@pytest.fixture()
def lab(tmp_path: Path, images_root: Path, clock):
    """שרת שרשת ההפצה שלו כבר הוגדרה מהקונסולה על eth1 (‏10.44.9.1/24).
    ‏eth0 = ניהול, ‏eth2/eth3 = כרטיסים פנויים."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    m = Machine()
    module_conf = tmp_path / "modules-load.d" / "imagesrv-bond.conf"
    fake = {"applied": [], "deploy_calls": [], "local": "10.1.2.3",
            "probe": dhcp.ProbeResult(True, ()), "supported": True, "main_defines": False,
            "apply": m.apply, "bond_state": m.bond_state}
    dhcp_hooks = {
        "interfaces": m.interfaces,
        "probe": lambda name: fake["probe"],
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
        "netcfg_apply": lambda name: fake["apply"](name),
        "netcfg_down": m.down,
        "netcfg_state": m.state,
        "netcfg_bond_state": lambda name: fake["bond_state"](name),
        "netcfg_bond_supported": lambda: fake["supported"],
        "netcfg_read_bond_module": lambda: netcfg_host.read_bond_module(module_conf),
        "netcfg_write_bond_module": lambda text: netcfg_host.write_bond_module(text, module_conf),
        "netcfg_main_defines": lambda name: fake["main_defines"],
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: (True, "active"),
        "netcfg_now": clock, "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: fake["local"],
        "netcfg_foreign_manager": lambda: None,   # #1558: ifupdown, לא המכונה המריצה
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
    r = client.put("/api/console/net/config/eth1/deploy", json={
        "network": "10.44.9.0", "netmask": "255.255.255.0", "range_start": "10.44.9.50",
        "range_end": "10.44.9.200", "lease": "12h", "confirm": "eth1"})
    assert r.status_code == 200 and r.json()["deploy"]["ok"], r.text
    assert deploy_net.stored(app.state.ctx.conn) == ("eth1", "http://10.44.9.1:8080")
    fake["applied"].clear(); fake["deploy_calls"].clear(); m.calls.clear()
    return SimpleNamespace(client=client, conn=app.state.ctx.conn, m=m, fake=fake,
                           deploy=ctx, module_conf=module_conf)


def _settings(conn, prefix):
    return {r["key"][len(prefix):]: r["value"] for r in conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?", (prefix + "%",))}


def _second(lab, name, second, **extra):
    return lab.client.put(f"/api/console/net/config/{name}/second",
                          json={"second": second, **extra})


def test_adding_a_second_card_moves_everything_bound_to_the_deploy_name_to_the_bond(lab):
    r = _second(lab, "eth1", "eth2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["interface"] == "bond0" and body["server_ip"] == "10.44.9.1"
    # המכונה: קובץ אחד ל-bond, אין קובץ לחברים, והכתובת על ה-bond בלבד
    assert set(lab.m.confs) == {"bond0"}
    assert "bond-slaves eth1 eth2" in lab.m.confs["bond0"]
    assert lab.m.calls[:2] == [("down", "eth1"), ("apply", "bond0")], \
        "ifdown the old card while its file still exists, then ifup the bond"
    assert lab.m.nics["bond0"] == ["10.44.9.1/24"] and lab.m.nics["eth1"] == []
    # ה-DB: הכתובת וה-DHCP עברו לשם החדש, והישנים נמחקו
    cfgs, offers = _settings(lab.conn, netcfg.SETTING_PREFIX), _settings(lab.conn, dhcp.SETTING_PREFIX)
    assert "eth1" not in cfgs and "eth2" not in cfgs and "eth1" not in offers
    assert json.loads(cfgs["bond0"])["slaves"] == ["eth1", "eth2"]
    moved = json.loads(offers["bond0"])
    assert moved["enabled"] and moved["range_start"] == "10.44.9.50" and moved["server_ip"] == "10.44.9.1"
    # ההשלמה של #1088 לשם החדש: חומת אש, אתחול (udp-sender --interface), רישום
    assert ("fw", "bond0") in lab.fake["deploy_calls"] and ("restart",) in lab.fake["deploy_calls"]
    assert deploy_net.stored(lab.conn) == ("bond0", "http://10.44.9.1:8080")
    assert lab.deploy.state.interface == "bond0"
    assert "interface=bond0" in lab.fake["applied"][-1]
    assert "interface=eth1" not in lab.fake["applied"][-1]
    assert body["bond"]["state"] == "ok"
    events = [r["event"] for r in lab.conn.execute("SELECT event FROM journal")]
    assert "net_deploy_bond" in events


def test_get_shows_the_bond_each_member_and_who_is_a_member(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    lab.m.bond_link["eth2"] = "down"                  # הכבל של הכרטיס השני נשלף
    view = lab.client.get("/api/console/net/config").json()
    bond = view["deploy_bond"]
    assert bond["name"] == "bond0" and bond["mode"] == "balance-alb" and bond["checked"] is True
    assert bond["state"] == "degraded"
    assert bond["members"] == [{"name": "eth1", "role": 1, "state": "active"},
                               {"name": "eth2", "role": 2, "state": "down"}]
    rows = {r["name"]: r for r in view["interfaces"]}
    assert rows["eth1"]["member_of"] == "bond0" and rows["eth2"]["member_of"] == "bond0"
    assert rows["eth0"]["member_of"] is None
    assert rows["bond0"]["slaves"] == ["eth1", "eth2"] and rows["bond0"]["mismatches"] == []


def test_a_bond_state_that_could_not_be_read_is_unchecked_not_active(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    lab.m.bond_readable = False
    view = lab.client.get("/api/console/net/config").json()
    assert view["deploy_bond"]["state"] == "unchecked"
    assert {m["state"] for m in view["deploy_bond"]["members"]} == {"unchecked"}
    bond_row = next(r for r in view["interfaces"] if r["name"] == "bond0")
    assert bond_row["mismatches"], "unchecked is never a match (principle 5)"


def test_without_a_second_card_there_is_no_bond_in_the_view(lab):
    view = lab.client.get("/api/console/net/config").json()
    assert view["deploy_bond"] is None
    assert all(r["member_of"] is None for r in view["interfaces"])


def test_removing_the_second_card_moves_everything_back_to_the_first(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    lab.fake["applied"].clear(); lab.fake["deploy_calls"].clear(); lab.m.calls.clear()
    r = _second(lab, "bond0", "")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["interface"] == "eth1"
    assert set(lab.m.confs) == {"eth1"} and "bond" not in lab.m.confs["eth1"]
    assert lab.m.calls[:2] == [("down", "bond0"), ("apply", "eth1")]
    assert lab.m.nics["eth1"] == ["10.44.9.1/24"] and "bond0" not in lab.m.nics
    cfgs, offers = _settings(lab.conn, netcfg.SETTING_PREFIX), _settings(lab.conn, dhcp.SETTING_PREFIX)
    assert "bond0" not in cfgs and "bond0" not in offers
    assert json.loads(offers["eth1"])["enabled"] is True
    assert deploy_net.stored(lab.conn) == ("eth1", "http://10.44.9.1:8080")
    assert ("fw", "eth1") in lab.fake["deploy_calls"]
    assert "interface=eth1" in lab.fake["applied"][-1] and "bond0" not in lab.fake["applied"][-1]


def test_a_bond_that_did_not_verify_restores_the_old_card_and_records_nothing(lab):
    lab.m.bond_readable = False                       # ifup עבר, אבל הקרנל לא מראה bond
    before_confs = dict(lab.m.confs)
    r = _second(lab, "eth1", "eth2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and "bond0 לא אומת" in body["apply_error"]
    assert "הוחזרה ואומתה" in body["apply_error"]
    assert lab.m.confs == before_confs, "the previous files are back"
    assert lab.m.nics["eth1"] == ["10.44.9.1/24"], "and the deploy card carries its address again"
    assert "eth1" in _settings(lab.conn, netcfg.SETTING_PREFIX)
    assert "bond0" not in _settings(lab.conn, dhcp.SETTING_PREFIX)
    assert deploy_net.stored(lab.conn) == ("eth1", "http://10.44.9.1:8080")
    assert lab.fake["deploy_calls"] == [] and lab.fake["applied"] == []


def _refused(lab, r, status, needle):
    assert r.status_code == status, r.text
    assert needle in r.json()["detail"], r.json()["detail"]
    assert lab.m.calls == [] and lab.fake["deploy_calls"] == [] and lab.fake["applied"] == []
    assert set(lab.m.confs) == {"eth1"}


def test_the_management_card_cannot_be_the_second_card(lab):
    lab.m.nics["eth0"] = ["10.1.2.3/24"]
    _refused(lab, _second(lab, "eth1", "eth0"), 409, "כרטיס הניהול")


def test_a_card_in_another_mode_is_refused_with_its_mode(lab):
    lab.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                     (netcfg.SETTING_PREFIX + "eth2",
                      netcfg.NetConfig(name="eth2", mode="dhcp").to_json()))
    lab.conn.commit()
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "לקוח DHCP")


def test_a_card_that_carries_an_address_is_refused(lab):
    lab.m.nics["eth2"] = ["192.168.5.7/24"]
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "נושא כתובת")


def test_a_card_defined_in_the_main_interfaces_file_or_unreadable_is_refused(lab):
    lab.fake["main_defines"] = True
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "מוגדר ב-/etc/network/interfaces")
    lab.fake["main_defines"] = None
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "אי אפשר לוודא")


@pytest.mark.parametrize("probe,needle", [
    (dhcp.ProbeResult(True, ("192.168.5.1",)), "נמצא שרת DHCP פעיל על eth2"),
    (dhcp.ProbeResult(False, ()), "הבדיקה עצמה לא רצה"),
])
def test_dhcp_already_answering_on_the_second_card_or_not_checked_is_refused(lab, probe, needle):
    lab.fake["probe"] = probe
    _refused(lab, _second(lab, "eth1", "eth2"), 409, needle)


def test_without_ifenslave_there_is_no_bond(lab):
    lab.fake["supported"] = False
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "ifenslave")


@pytest.mark.parametrize("local", ["10.44.9.1", ""])
def test_a_request_through_the_deploy_network_or_unknown_card_is_refused(lab, local):
    lab.fake["local"] = local
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "כרטיס הניהול")


def test_an_open_round_blocks_the_change(lab):
    group = lab.conn.execute("SELECT id FROM groups LIMIT 1").fetchone()["id"]
    lab.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at) VALUES"
        " ('s1', ?, 'img', 'p', 1, 60, 'running', 'noc', '2026-10-11T00:00:00', 0)", (group,))
    lab.conn.commit()
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "יש סבב פעיל")


def test_a_deploy_network_from_the_unit_flag_is_refused(lab):
    lab.deploy.state = deploy_net.DeployState("cli", "eth1", "http://10.44.9.1:8080")
    _refused(lab, _second(lab, "eth1", "eth2"), 409, "--server-url")


def test_a_bond_member_is_not_edited_as_a_lone_card(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    r = lab.client.put("/api/console/net/config/eth2", json={
        "mode": "static", "address": "192.168.5.7", "netmask": "255.255.255.0", "confirm": "eth2"})
    assert r.status_code == 409 and "חבר ב-bond0" in r.json()["detail"]
    r = lab.client.put("/api/console/net/config/eth1/deploy", json={
        "network": "10.44.9.0", "netmask": "255.255.255.0", "range_start": "10.44.9.50",
        "range_end": "10.44.9.200", "confirm": "eth1"})
    assert r.status_code == 409 and "חבר ב-bond0" in r.json()["detail"]


def test_editing_the_deploy_network_on_the_bond_keeps_both_cards(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    r = lab.client.put("/api/console/net/config/bond0/deploy", json={
        "network": "10.44.9.0", "netmask": "255.255.255.0", "range_start": "10.44.9.60",
        "range_end": "10.44.9.180", "lease": "8h", "confirm": "bond0"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "bond-slaves eth1 eth2" in lab.m.confs["bond0"]
    assert json.loads(_settings(lab.conn, netcfg.SETTING_PREFIX)["bond0"])["slaves"] == ["eth1", "eth2"]
    assert json.loads(_settings(lab.conn, dhcp.SETTING_PREFIX)["bond0"])["range_start"] == "10.44.9.60"


def test_a_failed_completion_is_named_dnsmasq_still_follows_and_a_retry_on_the_new_name_finishes(lab):
    """חומת האש נפלה אחרי שהמכונה כבר עברה ל-bond: ‏ok=false עם השלב בשם, ה-DHCP
    הולך אחרי הכתובת, ושליחה חוזרת על bond0 משלימה בלי לגעת שוב במכונה."""
    lab.deploy.hooks["firewall"] = lambda repo, **kw: "nft -c נפל"
    r = _second(lab, "eth1", "eth2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and "nft -c נפל" in body["apply_error"]
    assert body["deploy"]["ok"] is False and body["deploy"]["recorded"] is False
    assert deploy_net.stored(lab.conn) == ("eth1", "http://10.44.9.1:8080"), "not recorded"
    assert "interface=bond0" in lab.fake["applied"][-1], "the DHCP follows the address"
    lab.m.calls.clear()
    lab.deploy.hooks["firewall"] = lambda repo, **kw: lab.fake["deploy_calls"].append(("fw", kw["deploy_if"])) or None
    r = _second(lab, "bond0", "eth2")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert lab.m.calls == [], "the machine is already bonded — nothing is touched again"
    assert deploy_net.stored(lab.conn) == ("bond0", "http://10.44.9.1:8080")
    assert ("fw", "bond0") in lab.fake["deploy_calls"]


# #1804: exercise the real host apply function, faking only command execution
# and the kernel's proc directory. The older Machine.apply skipped both.
@pytest.fixture()
def host_lab(lab, monkeypatch, tmp_path):
    proc = tmp_path / "proc" / "net" / "bonding"
    proc.mkdir(parents=True)
    lab.commands = []
    lab.module_result = (0, "")
    lab.module_loaded = False
    lab.proc_text = BONDING_TEXT
    lab.fake["apply"] = netcfg_host.apply_interface
    lab.fake["bond_state"] = lambda name: netcfg_host.read_bond(name, proc)

    def run(cmd, timeout=40):
        lab.commands.append(tuple(cmd))
        if cmd == ["modprobe", "bonding"]:
            lab.module_loaded = lab.module_result[0] == 0
            return lab.module_result
        if cmd[:2] == ["ip", "link"]:
            return (0, "") if cmd[4] in lab.m.nics else (1, 'Cannot find device "bond0"')
        if cmd[0] == "ifdown":
            lab.m.down(cmd[-1])
            if cmd[-1] == "bond0":
                (proc / "bond0").unlink(missing_ok=True)
            return 0, ""
        if cmd[0] == "ifup":
            if cmd[-1] == "bond0" and not lab.module_loaded:
                return 1, "bonding module is not loaded"
            error = lab.m.apply(cmd[-1])
            if cmd[-1] == "bond0" and not error and lab.proc_text is not None:
                (proc / "bond0").write_text(lab.proc_text, encoding="utf-8")
            return (1, error) if error else (0, "")
        pytest.fail(f"unexpected host command: {cmd}")

    monkeypatch.setattr(netcfg_host, "_run", run)
    return lab


def test_1804_loads_module_before_ifup_and_persists_atomically(host_lab, monkeypatch):
    lab = host_lab
    writes = []
    atomic = netcfg_host.write_atomic

    def write(path, text):
        writes.append((path, text))
        atomic(path, text)

    monkeypatch.setattr(netcfg_host, "write_atomic", write)
    result = _second(lab, "eth1", "eth2").json()
    assert result["ok"], result
    assert lab.commands.index(("modprobe", "bonding")) < lab.commands.index(("ifup", "bond0"))
    assert ("ip", "link", "set", "dev", "bond0", "up") not in lab.commands
    assert lab.module_conf.read_text(encoding="utf-8") == "bonding\n"
    assert (lab.module_conf, "bonding\n") in writes
    assert result["bond"]["checked"] is True
    assert {m["name"] for m in result["bond"]["members"]} == {"eth1", "eth2"}


def _assert_1804_rolled_back(lab, before, result):
    assert result["ok"] is False, result
    assert "הוחזרה ואומתה" in result["apply_error"]
    assert lab.m.confs == before
    assert lab.m.nics["eth1"] == ["10.44.9.1/24"]
    assert "bond0" not in lab.m.nics
    assert not lab.module_conf.exists()
    assert "bond0" not in _settings(lab.conn, netcfg.SETTING_PREFIX)
    assert "bond0" not in _settings(lab.conn, dhcp.SETTING_PREFIX)
    assert deploy_net.stored(lab.conn) == ("eth1", "http://10.44.9.1:8080")
    assert lab.fake["deploy_calls"] == [] and lab.fake["applied"] == []


@pytest.mark.parametrize("code,detail", [(1, "module rejected"), (None, "modprobe unavailable")])
def test_1804_modprobe_failure_names_the_command_and_rolls_back(host_lab, code, detail):
    lab = host_lab
    lab.module_result = (code, detail)
    before = dict(lab.m.confs)
    result = _second(lab, "eth1", "eth2").json()
    assert "modprobe bonding" in result["apply_error"]
    assert detail in result["apply_error"]
    if code is not None:
        assert f"rc={code}" in result["apply_error"]
    assert ("ifup", "bond0") not in lab.commands
    _assert_1804_rolled_back(lab, before, result)


@pytest.mark.parametrize("proc_text,needle", [
    (None, "bond0"),
    (BONDING_TEXT.split("Slave Interface: eth2")[0], "eth2 אינו חבר"),
])
def test_1804_ifup_success_without_proc_evidence_rolls_back(host_lab, proc_text, needle):
    lab = host_lab
    lab.proc_text = proc_text
    before = dict(lab.m.confs)
    result = _second(lab, "eth1", "eth2").json()
    assert ("ifup", "bond0") in lab.commands, "the readback must follow a successful ifup"
    assert needle in result["apply_error"]
    if proc_text is None:
        assert "bonding" in result["apply_error"] and "לא נקרא" in result["apply_error"]
    _assert_1804_rolled_back(lab, before, result)


def test_1804_removing_second_card_removes_module_file(lab):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    # Seed explicitly so old code reaches removal and fails on the stale file.
    lab.module_conf.parent.mkdir(parents=True, exist_ok=True)
    lab.module_conf.write_text("bonding\n", encoding="utf-8")
    result = _second(lab, "bond0", "").json()
    assert result["ok"], result
    assert not lab.module_conf.exists()
    assert lab.m.nics["eth1"] == ["10.44.9.1/24"]


def test_1804_failed_module_write_rolls_back(lab, monkeypatch):
    before = dict(lab.m.confs)

    def fail_write(path, text):
        raise OSError("module file write failed")

    monkeypatch.setattr(netcfg_host, "write_atomic", fail_write)
    result = _second(lab, "eth1", "eth2").json()
    assert result["ok"] is False, result
    assert "imagesrv-bond.conf" in result["apply_error"]
    assert "module file write failed" in result["apply_error"]
    assert ("apply", "bond0") not in lab.m.calls
    _assert_1804_rolled_back(lab, before, result)


def test_1804_unreadable_module_file_stops_before_network_changes(lab):
    lab.module_conf.mkdir(parents=True)  # Reading a directory must not mean absent.
    result = _second(lab, "eth1", "eth2").json()
    assert result["ok"] is False, result
    assert "imagesrv-bond.conf" in result["apply_error"]
    assert lab.m.calls == []
    assert lab.m.nics["eth1"] == ["10.44.9.1/24"]


def test_1804_failed_removal_restores_module_file_and_bond(lab, monkeypatch):
    assert _second(lab, "eth1", "eth2").json()["ok"]
    lab.module_conf.parent.mkdir(parents=True, exist_ok=True)
    original = "# existing boot configuration\nbonding\n"
    lab.module_conf.write_text(original, encoding="utf-8")
    before = dict(lab.m.confs)
    unlink = Path.unlink

    def fail_removal(path, *args, **kwargs):
        if path == lab.module_conf:
            raise OSError("module file removal failed")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_removal)
    result = _second(lab, "bond0", "").json()
    assert result["ok"] is False, result
    assert "module file removal failed" in result["apply_error"]
    assert "הוחזרה ואומתה" in result["apply_error"]
    assert lab.module_conf.read_text(encoding="utf-8") == original
    assert lab.m.confs == before and lab.m.nics["bond0"] == ["10.44.9.1/24"]
    assert deploy_net.stored(lab.conn) == ("bond0", "http://10.44.9.1:8080")
