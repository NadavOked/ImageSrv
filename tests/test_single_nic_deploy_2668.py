"""#2668 ס' 3: שרת עם כרטיס רשת יחיד — אותו כרטיס הוא כרטיס הניהול וגם רשת ההפצה.

ברזל 09/11 (v0.91.5, כרטיס יחיד `enp0s31f6`, 10.0.0.2/24 סטטי): "רשת הפצה" על
כרטיס הניהול סורב תמיד (#1263, #2428), ולא הייתה שום דרך להגדיר הפצה.

DHCP על כרטיס הניהול הוא ההגדרה המסוכנת ביותר במערכת (#53): אם הרשת שלו היא
רשת המכללה, dnsmasq מחלק כתובות לכל המכללה. לכן זה **מעבר מפורש** ולא הסרת
חסימה, ורק בחלון רשת ההפצה (`PUT /net/config/{שם}/deploy`):

- המפעיל אישר במפורש (`confirm_management`, כפתור אדום — בלי הקלדת מילה, #1270);
- השומר של DHCP קיים (#53, #2417) רץ והחזיר **ראיה חיובית** שאין שרת אחר.
  "לא הצלחנו לבדוק" = סירוב (עיקרון 5), ואין `ignore_existing` על כרטיס הניהול;
- הכרטיס בכתובת ידנית, ו**הכתובת שלו נשארת** כתובת השרת ברשת ההפצה — הקונסולה
  ממשיכה באותה כתובת, אין `ifdown`/`ifup`, ואין מה להחזיר אחרי 60 שניות.

לעולם לא נוגעים במכונה: ה-hooks של DHCP, הכתובות וההשלמה מוזרקים.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from server.db import get_setting, set_setting  # noqa: E402
from server.net import deploy_net, dhcp, netcfg, netcfg_host, nic_services  # noqa: E402

from test_nftables_rules import generate, needs_bash  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONF = "/api/console/net/config"
ADDR = "10.0.0.2"
MGMT = netcfg.NetConfig(name="eth0", mode=netcfg.MODE_STATIC, address=ADDR,
                        netmask="255.255.255.0", gateway="10.0.0.254", dns=["10.0.0.254"])
BODY = {"network": "10.0.0.0", "netmask": "255.255.255.0",
        "range_start": "10.0.0.100", "range_end": "10.0.0.200", "lease": "12h",
        "confirm": "eth0", "confirm_management": True}


@pytest.fixture()
def single(tmp_path: Path, images_root: Path, clock):
    """שרת עם כרטיס אחד, eth0: כרטיס הניהול (נרשם בהתקנה), 10.0.0.2/24 סטטי,
    והקונסולה נקשרת אליו (`servers_interface`). רשת ההפצה טרם הוגדרה."""
    from server.app import create_app
    from server.console import users

    fake = {
        "interfaces": [{"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00",
                        "addresses": [f"{ADDR}/24"]}],
        "probe": dhcp.ProbeResult(True, ()), "probed": [],
        "confs": {}, "applied": [], "dnsmasq": [], "deploy_calls": [],
        "local": ADDR, "management": "eth0",
    }

    def probe(name):
        fake["probed"].append(name)
        return fake["probe"]

    def live_state():
        return netcfg_host.LiveState(
            True, {n["name"]: list(n["addresses"]) for n in fake["interfaces"]},
            ["default via 10.0.0.254"], ["10.0.0.254"])

    netcfg_hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "netcfg_read_conf": lambda name, root=None: fake["confs"].get(name),
        "netcfg_write_conf": lambda name, text, root=None: fake["confs"].__setitem__(name, text),
        "netcfg_write_resolv": lambda text, path=None: None,
        "netcfg_apply": lambda name: fake["applied"].append(name),
        "netcfg_down": lambda name: fake["applied"].append(("down", name)),
        "netcfg_state": live_state,
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: (True, "active"),
        "netcfg_now": clock,
        "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: fake["local"],
        "netcfg_peer_address": lambda request: "10.0.0.50",
        "netcfg_management": lambda: fake["management"],
        "netcfg_foreign_manager": lambda: None,
        "netcfg_main_defines": lambda name: False,
        "netcfg_bond_supported": lambda: True,
        "netcfg_read_bond_module": lambda: None,
        "netcfg_write_bond_module": lambda text: None,
    }
    dhcp_hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "probe": probe,
        "apply": lambda text: fake["dnsmasq"].append(text) or None,
        "apply_proxy": lambda text, active: None,
        "read_active_conf": lambda: "", "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
        "netcfg_management": lambda: fake["management"],
    }
    deploy_hooks = {
        "write_grub_cfg": lambda url, root: fake["deploy_calls"].append(("grub", url)) or None,
        "enable_dnsmasq": lambda: fake["deploy_calls"].append(("dnsmasq",)) or None,
        "firewall": lambda repo, **kw: fake["deploy_calls"].append(("fw", kw)) or None,
        "restart_server": lambda: fake["deploy_calls"].append(("restart",)) or None,
    }
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=REPO, servers_interface="eth0",
        hooks=deploy_hooks)
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080",
                     now_fn=clock, netcfg_hooks=netcfg_hooks, dhcp_hooks=dhcp_hooks,
                     deploy=deploy, netcfg_state_dir=tmp_path / "netstate")
    conn = app.state.ctx.conn
    set_setting(conn, netcfg.SETTING_PREFIX + "eth0", MGMT.to_json())
    users.create(conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
    return {"client": client, "fake": fake, "conn": conn, "deploy": deploy}


def _put(single, name="eth0", **over):
    return single["client"].put(f"{CONF}/{name}/deploy", json={**BODY, **over})


def _dhcp(single, name="eth0") -> dhcp.InterfaceConfig:
    return dhcp.InterfaceConfig.from_json(
        name, get_setting(single["conn"], dhcp.SETTING_PREFIX + name))


def _nothing_happened(single) -> None:
    fake = single["fake"]
    assert fake["dnsmasq"] == [] and fake["deploy_calls"] == []
    assert fake["confs"] == {} and fake["applied"] == []
    assert _dhcp(single).enabled is False
    assert deploy_net.stored(single["conn"]) == (None, None)


def test_a_clean_probe_and_an_explicit_ok_make_the_one_card_the_deploy_network(single):
    """הלב: הבדיקה ענתה "נבדק, נקי", המפעיל אישר — ההפצה על eth0, על הכתובת שלו."""
    response = _put(single)
    assert response.status_code == 200, response.text
    out = response.json()
    fake = single["fake"]
    assert out["ok"] is True, out
    assert out["server_ip"] == ADDR, "כתובת הניהול נשארת כתובת השרת — לא \u200e.1 של הרשת"
    assert fake["probed"] == ["eth0"], "השומר של DHCP קיים חייב לרוץ על הכרטיס"
    # הכתובת לא זזה: אין קובץ חדש, אין ifdown/ifup, ואין סמן החזרה של #56.
    assert out["address"] is None
    assert fake["confs"] == {} and fake["applied"] == []
    saved = _dhcp(single)
    assert saved.enabled and saved.server_ip == ADDR
    assert (saved.range_start, saved.range_end) == ("10.0.0.100", "10.0.0.200")
    assert "interface=eth0" in fake["dnsmasq"][-1]
    assert f"option:tftp-server,{ADDR}" in fake["dnsmasq"][-1]
    # ההשלמה רצה על אותו כרטיס בשני התפקידים.
    assert [c[0] for c in fake["deploy_calls"]] == ["grub", "dnsmasq", "fw", "restart"]
    fw = fake["deploy_calls"][2][1]
    assert fw["deploy_if"] == "eth0" and fw["servers_if"] == "eth0"
    assert deploy_net.stored(single["conn"]) == ("eth0", f"http://{ADDR}:8080")
    events = [r["event"] for r in single["conn"].execute("SELECT event FROM journal")]
    assert "net_deploy_on_management" in events


def test_a_foreign_dhcp_server_on_the_card_refuses_and_nothing_moves(single):
    single["fake"]["probe"] = dhcp.ProbeResult(True, ("10.0.0.254",))
    response = _put(single)
    assert response.status_code == 409, response.text
    assert "10.0.0.254" in response.json()["detail"]
    _nothing_happened(single)


@pytest.mark.parametrize("reason", ["permission", "port_busy", "link_down", "os_error",
                                    "ה-DHCPDISCOVER לא נראה יוצא מ-eth0"])
def test_a_probe_that_could_not_run_is_a_refusal_not_clean(single, reason):
    """עיקרון 5: "לא הצלחנו לבדוק" אינו "בדקנו, אין". על כרטיס הניהול זה מה
    שעומד בין השרת לבין רשת המכללה."""
    single["fake"]["probe"] = dhcp.ProbeResult(False, reason=reason)
    response = _put(single)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "לא ניתן לבדוק" in detail
    assert "ignore_existing" not in detail, "אין עקיפה של הבדיקה על כרטיס הניהול"
    _nothing_happened(single)


def test_skipping_the_probe_is_refused_on_the_management_card(single):
    """`ignore_existing` קיים לכרטיס שהמפעיל יודע שאינו ברשת המכללה. כרטיס הניהול
    הוא בדיוק הכרטיס שכן — אין לו עקיפה."""
    response = _put(single, ignore_existing=True)
    assert response.status_code == 409, response.text
    assert "כרטיס הניהול" in response.json()["detail"]
    assert single["fake"]["probed"] == []
    _nothing_happened(single)


def test_without_the_explicit_ok_the_refusal_says_what_is_at_stake(single):
    response = _put(single, confirm_management=False)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "כרטיס הניהול" in detail and "רשת המכללה" in detail
    assert single["fake"]["probed"] == []
    _nothing_happened(single)


def test_the_ok_is_a_boolean_not_a_truthy_string(single):
    assert _put(single, confirm_management="yes").status_code == 409
    _nothing_happened(single)


def test_a_management_card_on_dhcp_must_get_a_fixed_address_first(single):
    """כרטיס ניהול שמקבל כתובת מ-DHCP — יש ברשת שלו שרת DHCP, והכתובת שלו אינה
    קבועה; שרת DHCP שמכריז על כתובת שאולה הוא סתירה."""
    set_setting(single["conn"], netcfg.SETTING_PREFIX + "eth0",
                netcfg.NetConfig(name="eth0", mode=netcfg.MODE_DHCP).to_json())
    response = _put(single)
    assert response.status_code == 409, response.text
    assert "כתובת ידנית" in response.json()["detail"]
    _nothing_happened(single)


def test_another_network_on_the_management_card_is_refused_not_renumbered(single):
    """הכתובת של הקונסולה אינה זזה מכאן: רשת אחרת = 400, עם הדרך הנכונה."""
    response = _put(single, network="10.44.20.0", range_start="10.44.20.50",
                    range_end="10.44.20.200")
    assert response.status_code == 400, response.text
    assert "10.0.0.0/24" in response.json()["detail"]
    _nothing_happened(single)


def test_the_address_inside_the_range_is_refused(single):
    response = _put(single, range_start="10.0.0.1", range_end="10.0.0.50")
    assert response.status_code == 400, response.text
    _nothing_happened(single)


def test_the_dhcp_tab_still_refuses_the_management_card_even_with_the_ok(single):
    """המעבר קיים **רק** בחלון רשת ההפצה. `confirm_management` בגוף של
    `PUT /net/interfaces/{שם}` (דף הפורטים, API ישיר) אינו פותח דבר — אחרת
    ההגנה של #2428 הייתה במרחק שדה אחד בבקשה."""
    response = single["client"].put("/api/console/net/interfaces/eth0", json={
        "enabled": True, "range_start": "10.0.0.100", "range_end": "10.0.0.200",
        "netmask": "255.255.255.0", "server_ip": ADDR, "confirm": "eth0",
        "confirm_management": True, "management_ok": True})
    assert response.status_code == 409, response.text
    assert "כרטיס הניהול" in response.json()["detail"]
    _nothing_happened(single)


def test_complete_still_refuses_the_console_card_unless_the_caller_confirmed(tmp_path):
    """`complete` נשאר השומר האחרון (#2428): בלי האישור — סירוב לפני כל צעד."""
    from server.db import connect
    from test_servers_dhcp_1088 import _complete_ctx, _complete_hooks
    conn = connect(tmp_path / "x.db")
    calls: list = []
    ctx = _complete_ctx(tmp_path, _complete_hooks(calls))
    refused = deploy_net.complete(ctx, conn, interface="eth0", server_ip=ADDR)
    assert refused["ok"] is False and calls == []
    done = deploy_net.complete(ctx, conn, interface="eth0", server_ip=ADDR,
                               management_confirmed=True)
    assert done["ok"] is True, done
    assert ("fw", "eth0", "eth0", None) in calls
    conn.close()


def test_no_second_card_bond_over_the_management_card(single):
    """bond מעביר את הכתובת מ-eth0 ל-bond0 — הקונסולה (`--console-host eth0`)
    הייתה מאבדת את הכתובת שלה. גם כשהבקשה מגיעה מ-loopback (ssh -L)."""
    conn, fake = single["conn"], single["fake"]
    set_setting(conn, dhcp.SETTING_PREFIX + "eth0", dhcp.InterfaceConfig(
        "eth0", enabled=True, range_start="10.0.0.100", range_end="10.0.0.200",
        server_ip=ADDR).to_json())
    deploy_net.record(conn, "eth0", f"http://{ADDR}:8080")
    single["deploy"].state = deploy_net.DeployState("console", "eth0", f"http://{ADDR}:8080")
    fake["interfaces"] += [{"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01",
                            "addresses": []},
                           {"name": "lo", "state": "up", "mac": "", "addresses": ["127.0.0.1/8"]}]
    fake["local"] = "127.0.0.1"
    response = single["client"].put(f"{CONF}/eth0/second", json={"second": "eth1"})
    assert response.status_code == 409, response.text
    assert "כרטיס הניהול" in response.json()["detail"]
    assert fake["confs"] == {} and fake["applied"] == []


# --- הקונסולה ושירות ההפצה על אותו כרטיס ------------------------------------


def test_console_and_deploy_services_share_the_card_by_default():
    """#705 בלי עריכה: כרטיס שהוא גם הקונסולה וגם ההפצה נותן את שניהם, וכל
    המאזינים נקשרים לאותה כתובת — הקונסולה אינה נעלמת כשההפצה נוספת."""
    base = nic_services.Baseline(console="eth0", deploy="eth0", interserver="eth0")
    eff = nic_services.effective(["eth0"], {}, base)
    assert set(eff["eth0"]) == {"console", "monitor", "multicast", "netboot", "interserver"}
    hosts = nic_services.plan(eff, {"eth0": ADDR})["hosts"]
    assert hosts["http_console"] == [ADDR] and hosts["http_boot"] == [ADDR]


@needs_bash
def test_the_firewall_opens_console_and_deploy_on_the_same_card():
    # פורט שאינו פורט ההפצה (#156) — כמו ב-test_nftables_rules.
    text = generate("--deploy-if", "eth0", "--servers-if", "eth0", "--mcast-ports", "2232-2233")
    for rule in ('iifname "eth0" udp dport 67 accept', 'iifname "eth0" udp dport 69 accept',
                 'iifname "eth0" tcp dport 8080 accept',
                 'iifname "eth0" udp dport 2232-2233 accept',
                 'iifname "eth0" tcp dport 8081 accept'):
        assert rule in text, rule
    assert "policy drop" in text


def test_after_the_restart_agent_and_console_both_bind_the_one_address(tmp_path, monkeypatch):
    """העלייה שאחרי ההשלמה: `--console-host eth0` ו-`deploy:interface=eth0` —
    הסוכן (8080) והקונסולה על 10.0.0.2, וכרטיס השרתים של ההשלמה הוא eth0."""
    import uvicorn

    import server.app
    import server.main
    from server.db import connect
    from server.net import ifaddr

    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    deploy_net.record(conn, "eth0", f"http://{ADDR}:8080")
    conn.close()
    configs: dict = {}
    captured: dict = {}
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, run=None: ADDR)
    monkeypatch.setattr(ifaddr.AddressWatcher, "start", lambda self: self)
    monkeypatch.setattr(server.app, "create_runtime",
                        lambda *a, **k: (captured.update(args=a, kwargs=k), "rt")[1])
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config",
                        lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: "eth0")
    monkeypatch.setattr(server.main, "_address_for_interface", lambda name: ADDR)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "", "--console-host", "eth0",
        "--data-dir", str(data), "--images", str(tmp_path / "img")])
    server.main.main()
    assert captured["args"][2] == f"http://{ADDR}:8080"
    assert captured["kwargs"]["interface"] == "eth0"
    assert captured["kwargs"]["deploy"].servers_interface == "eth0"
    assert [c["host"] for c in configs["agent"]] == [ADDR]
    assert ADDR in [c["host"] for c in configs["console"]]
