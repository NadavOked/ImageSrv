"""‏#1810 — כרטיס ההפצה נשמר כשם; שם שאינו קיים במערכת אדום בבריאות.

שדרוג דביאן, ‏vNIC שנוסף ב-ESXi או כרטיס שהוחלף משנים את השם. חוקי nft,
‏dnsmasq ו-udp-sender נטענים בלי שגיאה ואינם תופסים כלום — ועד כאן הבריאות
הייתה ירוקה ("כרטיסי רשת: מחוברים") וחומת האש "פעילה".

כל מה שנוגע במכונה מוזרק (‏interfaces, ‏nft_ruleset, ‏deploy).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.net.deploy_net import DeployState

RULES = ('table inet imagesrv {\n\tchain input {\n\t\tiifname "lo" accept\n'
         '\t\tiifname "ens192" udp dport 67 accept\n'
         '\t\tiifname "ens192" udp dport 69 accept\n\t}\n}\n')


def _nic(name):
    return {"name": name, "state": "up", "mac": "aa", "addresses": []}


@pytest.fixture()
def health(tmp_path: Path, images_root: Path, clock):
    from server.console import users
    from server.app import create_app

    fake = {"interfaces": [_nic("ens192")],
            "deploy": DeployState("console", "ens192", "http://10.44.12.10:8080")}
    hooks = {
        "ss": lambda: "", "unit_active": lambda name: "active",
        "http_get": lambda url: 200, "http_size": lambda url: (200, 9_000_000),
        "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: [],
        "nft_ruleset": lambda: RULES,
        "interfaces": lambda: fake["interfaces"],
        "deploy": lambda: fake["deploy"],
        "db_free": lambda data_dir: 50 * 1024 ** 3,
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, app.state.ctx.conn, "noc", "admin-pass-123")

    def rows():
        return {r["id"]: r for r in admin.get("/api/console/health").json()}

    def port_rows():
        return {r["id"]: r for r in admin.get("/api/console/ports").json()}

    yield {"fake": fake, "rows": rows, "ports": port_rows, "conn": app.state.ctx.conn}
    app.state.ctx.sender.stop()


def test_a_deploy_nic_that_exists_is_ok(health):
    rows = health["rows"]()
    assert rows["deploy_nic"]["state"] == "ok"
    assert rows["firewall"]["state"] == "ok"


def test_a_renamed_deploy_nic_is_red_and_named(health):
    health["fake"]["interfaces"] = [_nic("enp11s0")]          # השם השתנה
    rows = health["rows"]()
    assert rows["deploy_nic"]["state"] == "bad", rows["deploy_nic"]
    assert "ens192 לא קיים במערכת" in rows["deploy_nic"]["detail"]
    assert "enp11s0" in rows["deploy_nic"]["detail"]


def test_the_firewall_is_not_shown_open_when_its_rules_name_a_missing_nic(health):
    health["fake"]["interfaces"] = [_nic("enp11s0")]
    rows = health["rows"]()
    assert rows["firewall"]["state"] == "bad", rows["firewall"]
    assert "ens192" in rows["firewall"]["detail"]
    assert health["ports"]()["firewall"]["state"] == "bad"


def test_an_unconfigured_deploy_network_has_no_nic_row(health):
    health["fake"]["deploy"] = DeployState("none", None, None)
    assert "deploy_nic" not in health["rows"]()


def test_an_empty_interface_list_is_not_checked_rather_than_ok(health):
    health["fake"]["interfaces"] = []
    rows = health["rows"]()
    assert rows["deploy_nic"]["state"] == "unknown"
    assert "לא נבדק" in rows["firewall"]["detail"]


def test_a_stored_deploy_nic_that_vanished_is_red_not_just_unconfigured(health):
    """הפער שנמדד במעבדה ב-04/11: אחרי אתחול שבו שם הכרטיס השתנה, ‏`resolve`
    מחזיר "לא הוגדר" (אף כרטיס אינו נושא את ה-IP) — אבל `deploy:interface` עדיין
    ב-DB. "לא הוגדר" ו"הוגדר על כרטיס שאינו קיים" הם שני מצבים שונים."""
    from server.db import set_setting
    set_setting(health["conn"], "deploy:interface", "ens192")
    health["fake"]["deploy"] = DeployState("none", None, None)
    health["fake"]["interfaces"] = [_nic("enp11s0")]
    rows = health["rows"]()
    assert rows["deploy_nic"]["state"] == "bad", rows
    assert "ens192 לא קיים במערכת" in rows["deploy_nic"]["detail"]
    assert "enp11s0" in rows["deploy_nic"]["detail"]
    assert rows["deploy_net"]["state"] == "warn"        # האזהרה "לא הוגדרה" נשארת


def test_a_stored_nic_that_exists_adds_no_row_while_unconfigured(health):
    from server.db import set_setting
    set_setting(health["conn"], "deploy:interface", "ens192")
    health["fake"]["deploy"] = DeployState("none", None, None)
    rows = health["rows"]()
    assert "deploy_nic" not in rows       # אין "תקין" על רשת שלא הוגדרה
    assert rows["deploy_net"]["state"] == "warn"


def test_a_stored_deploy_nic_does_not_override_a_cli_configured_one(health):
    from server.db import set_setting
    set_setting(health["conn"], "deploy:interface", "old0")      # שארית ישנה
    rows = health["rows"]()           # הרשת מוגדרת על ens192 שקיים
    assert rows["deploy_nic"]["state"] == "ok"


def _completion_context(tmp_path, calls):
    from server.net import deploy_net

    return deploy_net.DeployContext(
        state=DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=tmp_path, servers_interface="eth0",
        hooks={
            "write_grub_cfg": lambda *a: calls.append("write_grub_cfg"),
            "enable_dnsmasq": lambda: calls.append("enable_dnsmasq"),
            "firewall": lambda *a, **kw: calls.append(("firewall", kw)),
            "restart_server": lambda: calls.append("restart_server"),
        })


@pytest.mark.parametrize("names,expected_error", [
    (["enp11s0", "eth0"], "ens192 לא קיים במערכת (יש: enp11s0, eth0)"),
    ([], "לא ניתן לבדוק שכרטיס ההפצה ens192 קיים"),
    (["eth0", "ens192"], None),
])
def test_completion_checks_presence_before_any_step(tmp_path, names, expected_error):
    from server.db import connect
    from server.net import deploy_net

    conn = connect(tmp_path / "deploy.db")
    calls = []
    deploy = _completion_context(tmp_path, calls)
    deploy.hooks["interfaces"] = lambda: [_nic(name) for name in names]
    try:
        result = deploy_net.complete(deploy, conn, interface="ens192", server_ip="10.44.12.10")
        if expected_error:
            assert result["ok"] is False
            assert expected_error in result["errors"][0]
            assert "לא נכתב דבר" in result["errors"][0]
            assert calls == []
            assert result["recorded"] is False and result["restarting"] is False
            assert deploy_net.stored(conn) == (None, None)
        else:
            assert result["ok"] is True, result
            assert result["errors"] == []
            assert calls == ["write_grub_cfg", "enable_dnsmasq",
                             ("firewall", {"deploy_if": "ens192", "servers_if": "eth0",
                                           "primary_ip": None}), "restart_server"]
            assert deploy_net.stored(conn) == ("ens192", "http://10.44.12.10:8080")
    finally:
        conn.close()


@pytest.mark.parametrize("interface,names,expected_error", [
    ("ens192", ["enp11s0"], "ens192 לא קיים במערכת (יש: enp11s0)"),
    ("ens192", [], "לא ניתן לבדוק"),
    ("ens192", ["ens192"], None),
    (None, [], None),
])
def test_cli_firewall_checks_current_interfaces(tmp_path, images_root, interface, names,
                                               expected_error):
    from server.app import create_app

    calls = []
    deploy = _completion_context(tmp_path, calls)
    deploy.state = DeployState("cli", interface, "http://10.44.12.10:8080")
    app = create_app(tmp_path / "data", images_root, deploy.state.url, deploy=deploy,
                     health_hooks={"interfaces": lambda: [_nic(name) for name in names]})
    try:
        nic = app.state.ctx.nic_services
        error = nic.hooks["firewall"](nic)
        if expected_error:
            assert expected_error in error
            assert calls == []
        else:
            assert error is None
            assert calls == [("firewall", {"deploy_if": interface, "servers_if": "eth0",
                                           "primary_ip": None})]
    finally:
        app.state.ctx.sender.stop()


@pytest.mark.parametrize("sources", [
    ("dhcp_hooks", "netcfg_hooks", "health_hooks"),
    ("netcfg_hooks", "health_hooks"),
    ("health_hooks",),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_app_shares_injected_interfaces_without_overriding_deploy_hook(
        tmp_path, images_root, sources, explicit):
    from server.app import create_app

    deploy = _completion_context(tmp_path, [])
    chosen = lambda: [_nic("ens192")]
    if explicit:
        deploy.hooks["interfaces"] = chosen
    injected = {source: {"interfaces": (chosen if source == sources[0]
                                        else lambda: [_nic("other0")])}
                for source in sources}
    if explicit:
        injected[sources[0]]["interfaces"] = lambda: [_nic("other0")]
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080",
                     deploy=deploy, **injected)
    try:
        assert deploy.hooks["interfaces"] is chosen
    finally:
        app.state.ctx.sender.stop()


def test_console_refuses_a_nic_that_disappears_during_the_dhcp_probe(tmp_path, images_root):
    from server.app import create_app
    from server.console import users
    from server.db import get_setting
    from server.net import deploy_net, dhcp

    calls, applied = [], []
    deploy = _completion_context(tmp_path, calls)
    present = [_nic("eth0"), {**_nic("ens192"), "addresses": ["10.44.12.10/24"]}]
    probed = []

    def probe(name):
        probed.append(name)
        present[:] = [_nic("eth0")]
        return dhcp.ProbeResult(True, ())

    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080",
                     deploy=deploy, dhcp_hooks={
                         "interfaces": lambda: present, "probe": probe,
                         "apply": lambda text: applied.append(text),
                         "apply_proxy": lambda *a: applied.append(a),
                         "read_active_conf": lambda: "",
                         "service_active": lambda unit: True,
                         "netcfg_management": lambda: "eth0",
                     })
    try:
        conn = app.state.ctx.conn
        users.create(conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
        client = TestClient(app)
        complete_console_login(client, conn, "noc", "admin-pass-123")
        response = client.put("/api/console/net/interfaces/ens192", json={
            "enabled": True, "confirm": "ens192", "server_ip": "10.44.12.10",
            "range_start": "10.44.12.50", "range_end": "10.44.12.200",
            "netmask": "255.255.255.0",
        })
        assert probed == ["ens192"]
        assert response.status_code == 500, response.text
        assert "ens192 לא קיים במערכת (יש: eth0)" in response.json()["detail"]
        assert calls == [] and applied == []
        assert deploy_net.stored(conn) == (None, None)
        assert not get_setting(conn, "dhcp:ens192")
        assert deploy.state == DeployState("none", None, None)
    finally:
        app.state.ctx.sender.stop()
