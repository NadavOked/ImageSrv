"""#2418: רשת שחופפת לרשת **החיה** של כרטיס אחר נדחית, גם כשהכרטיס אינו סטטי;
ושינוי כתובת שעלול לנתק את הקונסולה מחמש את ההחזרה של #56.

התרחיש מה-PoC (04/11): כרטיס הניהול eth0 הוא לקוח DHCP שקיבל 10.10.0.8/16 מהמכללה.
`PUT /deploy` עם 10.10.20.0/24 על eth1 החזיר 200 ו-`pending: False` — `_clash`
ראה רק הגדרות סטטיות, ו-`touches_console` לא חימש כי השינוי היה על כרטיס אחר.
אחרי ההחלה הקרנל מנתב את 10.10.20.0/24 דרך eth1, והקונסולה ננעלה; הדרך היחידה
חזרה הייתה ממסך השרת.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.net import netcfg, netcfg_rollback
from server.net.netcfg import NetConfig

from test_server_netcfg import (           # noqa: F401 — הפיקסטורה מיובאת בשמה
    journal_events, net_server, put, put_deploy,
)


def _pending(server) -> dict | None:
    path = netcfg_rollback.pending_path(server["state_dir"])
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _management_on_college_dhcp(server) -> None:
    """eth0 = כרטיס הניהול, לקוח DHCP עם 10.10.0.8/16 — הקונסולה מגיעה דרכו."""
    fake = server["fake"]
    fake["interfaces"][0]["addresses"] = ["10.10.0.8/16"]
    fake["local"] = "10.10.0.8"
    fake["peer"] = "10.10.0.50"


def test_a_deploy_network_inside_the_management_nics_live_network_is_refused(net_server):
    """ה-PoC עצמו: 200 ו-`pending: False` היו התשובה. עכשיו 400, ושום קובץ."""
    _management_on_college_dhcp(net_server)
    response = put_deploy(net_server, network="10.10.20.0", range_start="10.10.20.50",
                          range_end="10.10.20.200")
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "eth0" in detail and "10.10.0.0/16" in detail
    fake = net_server["fake"]
    assert fake["confs"] == {} and fake["applied"] == [] and fake["dnsmasq"] == []


def test_a_static_address_inside_another_cards_live_network_is_refused(net_server):
    """אותו שומר גם בטופס הכתובת הרגיל, ולא רק ברשת ההפצה."""
    _management_on_college_dhcp(net_server)
    response = put(net_server, address="10.10.30.1")
    assert response.status_code == 400, response.text
    assert "חופפת לרשת שעל eth0" in response.json()["detail"]
    assert net_server["fake"]["confs"] == {}


def test_a_new_network_that_holds_the_operator_arms_the_rollback(net_server):
    """המפעיל מגיע דרך נתב מ-10.20.0.5 אל eth0. הרשת החדשה של eth1 אינה חופפת
    לאף כרטיס — אבל היא מכילה את המפעיל, והתשובות אליו יצאו מ-eth1."""
    net_server["fake"]["peer"] = "10.20.0.5"
    result = put(net_server, address="10.20.0.1").json()
    assert result["rollback"]["pending"] is True, result
    assert _pending(net_server)["interface"] == "eth1"
    assert any(e == "net_rollback_armed" for e, _ in journal_events(net_server))


def test_live_networks_that_cannot_be_read_arm_the_rollback(net_server):
    """החפיפה לא נשללה — "לא נבדק" אינו "אין חפיפה" (עיקרון 5)."""
    net_server["fake"]["readable"] = False
    result = put(net_server, address="10.44.9.11").json()
    assert result["rollback"]["pending"] is True, result


def test_an_unrelated_address_on_another_card_still_needs_no_confirmation(net_server):
    """הצד החיובי — עובר גם בלי התיקון: המפעיל מחוץ לרשת החדשה, אין חפיפה."""
    result = put(net_server, address="10.44.9.11").json()
    assert result["ok"] is True and result["rollback"]["pending"] is False
    assert _pending(net_server) is None


def test_the_pure_check_sees_a_live_network_once():
    """כרטיס סטטי שגם הגדרתו חופפת — שורה אחת, לא שתיים; ושם הכרטיס עצמו
    והחברים ב-bond שלו אינם "כרטיס אחר"."""
    deploy = NetConfig(name="eth1", mode="static", address="10.10.20.1", netmask="255.255.255.0")
    dhcp_mgmt = NetConfig(name="eth0", mode="dhcp")
    found = netcfg.problems(deploy, [dhcp_mgmt, deploy], live={"eth0": ["10.10.0.8/16"]})
    assert len(found) == 1 and "eth0" in found[0]
    static_mgmt = NetConfig(name="eth0", mode="static", address="10.10.0.8", netmask="255.255.0.0")
    found = netcfg.problems(deploy, [static_mgmt, deploy], live={"eth0": ["10.10.0.8/16"]})
    assert len(found) == 1
    assert netcfg.problems(deploy, [deploy], live={"eth1": ["10.10.20.9/24"]}) == []
    bond = NetConfig(name="bond0", mode="static", address="10.10.20.1",
                     netmask="255.255.255.0", slaves=["eth1", "eth2"])
    assert netcfg.problems(bond, [bond], live={"eth1": ["10.10.20.1/24"]}) == []
