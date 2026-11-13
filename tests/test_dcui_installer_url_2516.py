"""#2516 (VM of #2490, v0.84.1, 06/11): the pre-install screen sends the operator
to a port the wizard guard drops.

Without answers, firstboot asks DHCP on every card with a link, and the DCUI
showed "Continue the installation from a browser: https://<first address>:8081"
-- there, the deploy card (ens4). The guard of #2490 (deploy/nftables-wizard.sh)
opens 8081 on loopback and on the installer card only, found by the MAC in
/etc/imagesrv/installer-nic; the operator who did what the screen said got a
timeout.

Now the address on the screen is the installer card's, by the guard's own rule
(``state=configured`` + ``mac=``). A known installer card without an address
says so by name; an undecidable one (no file, not configured, a MAC on no card)
shows no network address at all -- the guard is loopback-only then.

The snapshot comes from ``collect_snapshot`` over files and a fake runner, and
both screens (the text one and the graphical ``_main_body``) are read, so the
same test runs on the code before the fix.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from server.dcui import view
from server.dcui.app import render_preinstall
from server.dcui.data import CommandResult, collect_snapshot
from server.dcui.view import Extras

DEPLOY_MAC, INSTALLER_MAC = "00:50:56:01:02:01", "00:50:56:01:02:02"


def _machine(tmp_path: Path, installer_address: str | None) -> dict:
    """ens33 = the deploy card, first and with a DHCP address (as on 06/11);
    ens34 = the installer card."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    status = tmp_path / "firstboot.status"
    status.write_text("state=wizard-running\n", encoding="utf-8")
    sys_net = tmp_path / "net"
    for name, mac in (("ens33", DEPLOY_MAC), ("ens34", INSTALLER_MAC)):
        nic = sys_net / name
        nic.mkdir(parents=True)
        (nic / "address").write_text(mac, encoding="ascii")
        (nic / "carrier").write_text("1", encoding="ascii")
        (nic / "operstate").write_text("up", encoding="ascii")
        (nic / "speed").write_text("1000", encoding="ascii")
    installer_addr = (f'{{"family":"inet","local":"{installer_address}","prefixlen":24}}'
                      if installer_address else "")
    addresses = ('[{"ifname":"ens33","operstate":"UP","addr_info":[{"family":"inet",'
                 '"local":"10.90.2.139","prefixlen":24}]},'
                 f'{{"ifname":"ens34","operstate":"UP","addr_info":[{installer_addr}]}}]')

    def runner(argv):
        if argv[:4] == ["ip", "-j", "address", "show"]:
            return CommandResult(0, addresses)
        return CommandResult(1, "", "git unavailable")

    return dict(data_dir=data_dir, runner=runner, sys_net=sys_net, status_file=status,
                repo_dir=tmp_path / "source", manifest_file=tmp_path / "none.json")


def _fact(tmp_path: Path, text: str) -> Path:
    fact = tmp_path / "installer-nic"
    fact.write_text(text, encoding="utf-8")
    return fact


def _screens(snapshot) -> tuple[str, str]:
    frame = view.Frame("main")
    view._main_body(frame, snapshot, Extras(), datetime(2026, 11, 12))
    return "\n".join(render_preinstall(snapshot)), frame.text()


CONFIGURED = f"state=configured\ninterface=ens3\nmac={INSTALLER_MAC.upper()}\nreason=dhcp\n"


def test_the_browser_address_is_the_installer_cards_not_the_first_card_with_one(tmp_path):
    """The name in the installer (ens3) is not the name here; the MAC is -- in any case."""
    snapshot = collect_snapshot(**_machine(tmp_path, "10.44.0.37"),
                                installer_fact=_fact(tmp_path, CONFIGURED))
    assert snapshot.console_url == "https://10.44.0.37:8081"
    assert snapshot.installer_nic == "ens34"
    text, graphical = _screens(snapshot)
    assert "https://10.44.0.37:8081" in text and "https://10.44.0.37:8081" in graphical
    assert "10.90.2.139:8081" not in text + graphical, "the deploy card's address is not offered"


def test_an_installer_card_without_an_address_says_so_by_name(tmp_path):
    snapshot = collect_snapshot(**_machine(tmp_path, None),
                                installer_fact=_fact(tmp_path, CONFIGURED))
    assert snapshot.console_url == "" and snapshot.no_address is True
    text, graphical = _screens(snapshot)
    assert "ens34 (the installer card) has no address." in text
    assert "Press F2 to set a static address on it." in text
    assert "לכרטיס ההתקנה" in graphical and "ens34" in graphical and "אין כתובת" in graphical
    assert ":8081" not in text + graphical


@pytest.mark.parametrize("fact", [
    None,                                                   # no file
    "state=undecidable\nmac=00:50:56:01:02:02\n",           # not configured
    "state=configured\nmac=\n",                             # configured without a MAC
    "state=configured\nmac=00:50:56:aa:bb:cc\n",            # a MAC on no card here
])
def test_an_undecidable_installer_card_shows_no_network_address(tmp_path, fact):
    """The guard is loopback-only then (UNDECIDABLE): no address on the screen is reachable."""
    path = _fact(tmp_path, fact) if fact is not None else tmp_path / "missing"
    snapshot = collect_snapshot(**_machine(tmp_path, "10.44.0.37"), installer_fact=path)
    assert snapshot.console_url == "" and snapshot.installer_nic == ""
    text, graphical = _screens(snapshot)
    assert ":8081" not in text + graphical
    assert "The installer card is unknown" in text
    assert "כרטיס ההתקנה אינו ידוע" in graphical
