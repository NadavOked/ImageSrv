"""#2518 (VM of #2490, v0.84.1, 06/11, ``ss -ltnp``): while the first-boot wizard
runs -- before the permanent firewall -- dnsmasq (DNS, 53) and rpcbind (111)
listen on 0.0.0.0, the deploy card included. The #2490 guard covered 8081 only.

Nobody on the network needs either before the install: the wizard is reached by
address (no DNS), and nothing is served over NFS yet. The guard now drops 53 and
111 (udp and tcp) on every card but loopback -- the installer card too -- and
firstboot reads those drops back like the 8081 one; without them the wizard does
not start.
"""

from __future__ import annotations

import pytest

from test_nftables_rules import BASH, needs_bash
from test_wizard_guard_2490 import MAC, OTHER_MAC, _fact, _run, _sys_net

LOCAL_ONLY = [f'iifname != "lo" {proto} dport {port} counter drop'
              for port in (53, 111) for proto in ("udp", "tcp")]


def _lines(out: str) -> list[str]:
    return [line.strip() for line in out.replace("\r\n", "\n").splitlines()]


@needs_bash
@pytest.mark.parametrize("known", [True, False])
def test_dns_and_rpcbind_are_dropped_off_loopback_with_or_without_an_installer_card(tmp_path, known):
    sys_net = _sys_net(tmp_path, {"ens18": OTHER_MAC, "ens19": MAC})
    fact = _fact(tmp_path, f"state=configured\nmac={MAC if known else '52:54:00:00:00:01'}\n")
    proc = _run(BASH, "--nic-fact", str(fact), "--sys-net", str(sys_net))
    assert proc.returncode == 0, proc.stderr
    lines = _lines(proc.stdout)
    for rule in LOCAL_ONLY:
        assert rule in lines, f"{rule!r} missing from the guard:\n{proc.stdout}"
    # nothing opens 53/111 on any card -- not even the installer card
    assert not [line for line in lines if ("dport 53" in line or "dport 111" in line)
                and "accept" in line]


#: nft and systemctl as bash functions (as in test_wizard_guard_2490); ``list``
#: shows what was loaded, minus the lines of ``$STRIP`` when it is set.
