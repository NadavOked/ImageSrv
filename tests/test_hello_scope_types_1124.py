"""`scope` של בקשה הוא `starlette.types.Scope` ולא `dict` (mypy, 24/11): התשובות לא זזו.

שלוש הפונקציות שקוראות את `scope["server"]`/`scope["client"]` (`on_deploy_vlan`, `off_deploy_vlan`,
`server_source`) תפסו כל חריגה והחזירו False. הקריאה נכתבה מחדש בלי `None` באמצע (`(scope or {}).get(key, default)`),
והמבחן הזה נועל שהתשובה אותה תשובה גם על כל scope שאינו ניתן להשוואה.
"""

from __future__ import annotations

import pytest

from server.agent.hello import off_deploy_vlan, on_deploy_vlan, server_source

VLAN = "http://10.44.12.10:7712"

#: scope שאין בו זוג כתובות להשוות: כולם חוזרים ל"כמו היום" (False) בשלושת המסלולים.
UNCOMPARABLE = [
    None,
    {},
    {"server": None},
    {"server": ("testserver", 80)},
    {"server": ("10.44.12.10",)},
    {"server": "10.44.12.10:7712"},
    {"server": ("999.1.1.1", 7712)},
]


@pytest.mark.parametrize("scope", UNCOMPARABLE)
def test_a_scope_without_a_comparable_pair_is_neither_on_nor_off_the_vlan(scope):
    assert off_deploy_vlan(scope, VLAN) is False
    assert on_deploy_vlan(scope, VLAN) is False


def test_the_same_local_address_is_on_the_vlan_and_another_is_off_it():
    assert on_deploy_vlan({"server": ("10.44.12.10", 7712)}, VLAN) is True
    assert off_deploy_vlan({"server": ("10.44.12.10", 7712)}, VLAN) is False
    assert on_deploy_vlan({"server": ("10.10.10.8", 7712)}, VLAN) is False
    assert off_deploy_vlan({"server": ("10.10.10.8", 7712)}, VLAN) is True


@pytest.mark.parametrize("scope", [
    None, {}, {"client": None}, {"server": ("10.44.12.10", 7712)}, {"client": ("10.44.12.10", 50000)},
    {"client": ("not-an-ip", 1), "server": ("10.44.12.10", 7712)}, {"client": (), "server": ("10.44.12.10", 7712)},
    {"client": ("10.44.12.99", 1), "server": ("10.44.12.10", 7712)},
])
def test_server_source_is_false_when_the_pair_is_missing_or_is_another_machine(scope):
    assert server_source(scope) is False


@pytest.mark.parametrize("scope", [
    {"client": ("127.0.0.1", 50000), "server": ("10.44.12.10", 7712)},
    {"client": ("10.44.12.10", 50000), "server": ("10.44.12.10", 7712)},
])
def test_server_source_is_true_for_loopback_or_the_servers_own_address(scope):
    assert server_source(scope) is True
