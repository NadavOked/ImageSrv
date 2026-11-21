"""#2393: a missing distribution card must not take the management address with it.

Lab 04/11 (ImageSrv-LabRoot-1104, v0.81.2): the distribution card was renamed,
``imagesrv-ens34`` still said ``auto ens34``, ``ifup -a`` of networking.service
exited 1 ("Cannot find device"), and systemd sent SIGTERM to the whole cgroup of
the unit -- including the ``dhcpcd -b`` of the management card, which ``up``
had started inside that cgroup. The console was unreachable for ten minutes.

The fix is in the file the product writes: the DHCP client of a managed card
starts in a transient scope of its own, so the failure of the unit that ran
``ifup`` (any other card) cannot reach it. The failure itself is not hidden --
networking.service still fails, and health/DCUI still say so by name (#1810).

Systemd cannot run here, so these tests read the written file: the line that
starts the client, and the rule that no ``up`` line starts it bare.
"""

from __future__ import annotations

from server.net import netcfg
from server.net.netcfg import MODE_DHCP, MODE_STATIC, NetConfig

SCOPE = "/usr/bin/systemd-run --scope --quiet"


def _up_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().startswith("up ")]


def test_the_dhcp_client_starts_in_a_scope_of_its_own():
    text = netcfg.render(NetConfig("ens33", mode=MODE_DHCP))
    assert _up_lines(text) == [f"up {SCOPE} /usr/sbin/dhcpcd -4 -b ens33"]


def test_the_hostname_and_the_card_stay_after_the_client_command():
    text = netcfg.render(NetConfig("ens33", mode=MODE_DHCP), hostname="imagesrv")
    assert _up_lines(text) == [f"up {SCOPE} /usr/sbin/dhcpcd -4 -b -h imagesrv ens33"]


def test_no_up_line_starts_dhcpcd_bare():
    # the old line is the bug: dhcpcd in the cgroup of networking.service
    for host in ("", "imagesrv"):
        text = netcfg.render(NetConfig("ens33", mode=MODE_DHCP), hostname=host)
        bare = [u for u in _up_lines(text) if u.startswith("up /usr/sbin/dhcpcd")]
        assert bare == [], bare


def test_the_release_on_down_is_unchanged():
    # `dhcpcd -k` signals the running daemon wherever it lives; no scope needed
    text = netcfg.render(NetConfig("ens33", mode=MODE_DHCP))
    assert "    down /usr/sbin/dhcpcd -4 -k ens33" in text.splitlines()


def test_a_static_card_has_no_client_and_stays_automatic():
    # the distribution card of the incident: still `auto`, so a missing one still
    # fails networking.service by name instead of being silently skipped
    text = netcfg.render(NetConfig("ens34", mode=MODE_STATIC, address="10.44.9.1",
                                   netmask="255.255.255.0"))
    assert "auto ens34" in text.splitlines()
    assert "dhcpcd" not in text and "systemd-run" not in text
