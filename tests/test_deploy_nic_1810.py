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

    yield {"fake": fake, "rows": rows, "ports": port_rows}
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
