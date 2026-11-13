"""#2491: two leads from the boot/network security audit (05/11).

1. ``netcfg.render`` wrote the kernel's hostname unquoted into the ``up`` line
   of an ifupdown stanza, and ifupdown runs that line with sh as root.  The
   name comes from ``socket.gethostname`` at write time, not from a checked
   setting, so a name outside ``hostname_rule`` is now left out (no ``-h``).
2. ``deploy_net.refresh_grub_cfg`` skipped a TFTP bootstrap that was already
   locked with a password, so after ``/etc/imagesrv/grub-password`` was
   replaced by hand the bootstrap kept checking the old one.  It is now
   rewritten on every start with this start's hash.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from boot import grub_lock
from boot.grub_menu import GrubConfig, render_bootstrap
from server.net import deploy_net, netcfg

DHCP = netcfg.NetConfig("eth0", mode=netcfg.MODE_DHCP)
BARE_UP = "    up /usr/sbin/dhcpcd -4 -b eth0\n"

BAD_NAMES = [
    "x;touch /tmp/pwn",
    "$(touch /tmp/pwn)",
    "a`id`",
    "a b",
    "a\n    up touch /tmp/pwn",
    "a|id",
    "a&&id",
    "-h",
    "imagesrv.college",     # dots are outside hostname_rule
    "imagesrv-primary1",    # 17 > HOSTNAME_MAX
]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_a_hostname_outside_the_rule_is_not_written_into_the_up_line(name):
    text = netcfg.render(DHCP, hostname=name)
    assert BARE_UP in text, text
    assert " -h " not in text
    up = [line for line in text.splitlines() if line.strip().startswith("up ")]
    assert up == [BARE_UP.rstrip("\n")]
    assert "pwn" not in text and "`" not in text and "$(" not in text


@pytest.mark.parametrize("name", ["imagesrv", "imagesrv-haifa", "srv2", "IMAGESRV-01"])
def test_a_valid_hostname_is_still_sent(name):
    text = netcfg.render(DHCP, hostname=name)
    assert f"    up /usr/sbin/dhcpcd -4 -b -h {name} eth0\n" in text


def test_the_console_preview_drops_a_bad_kernel_hostname(tmp_path, images_root, clock):
    """The path the triage named: the name the console hands render() is
    ``socket.gethostname`` (here the ``hostname`` hook), not a checked setting."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.app import create_app
    from server.console import users

    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     netcfg_hooks={"hostname": lambda: "x;touch /tmp/pwn",
                                   "interfaces": lambda: [{"name": "eth0", "state": "up",
                                                           "addresses": []}]})
    users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
    r = client.post("/api/console/net/config/eth0/preview", json={"mode": "dhcp"})
    assert r.status_code == 200, r.text
    after = r.json()["after"]
    assert "up /usr/sbin/dhcpcd -4 -b eth0" in after
    assert "pwn" not in after


# ---- the TFTP bootstrap ------------------------------------------------------------------

BASE = "http://10.44.12.10:8080"
OLD_PASSWORD = "abcde-fghjk-mnpqr-stuvw"
NEW_PASSWORD = "zyxwv-tsrqp-nmkjh-gfedc"
LOCK = 'set superusers="imagesrv"'


def _hash(password: str) -> str:
    return grub_lock.pbkdf2_hash(password, iterations=1000)   # fast; same format


def _checks(text: str, entered: str) -> bool:
    """GRUB's check_password() on the file's password_pbkdf2 line."""
    lines = [line for line in text.splitlines() if line.startswith("password_pbkdf2 ")]
    if not lines:
        return False
    value = lines[0].split()[-1]
    found = re.fullmatch(r"grub\.pbkdf2\.sha512\.(\d+)\.([0-9A-F]+)\.([0-9A-F]+)", value)
    assert found, value
    iterations, salt, digest = (int(found.group(1)), bytes.fromhex(found.group(2)),
                                bytes.fromhex(found.group(3)))
    return hashlib.pbkdf2_hmac("sha512", entered.encode(), salt, iterations,
                               len(digest)) == digest


def test_a_password_replaced_by_hand_reaches_the_locked_bootstrap(tmp_path):
    assert deploy_net.write_grub_cfg(BASE, tmp_path, password_hash=_hash(OLD_PASSWORD)) is None
    path = tmp_path / "grub" / "grub.cfg"
    assert _checks(path.read_text(encoding="ascii"), OLD_PASSWORD)

    new_hash = _hash(NEW_PASSWORD)          # the next start, after the file was edited
    assert deploy_net.refresh_grub_cfg(tmp_path, new_hash) is None
    text = path.read_text(encoding="ascii")
    assert _checks(text, NEW_PASSWORD)
    assert not _checks(text, OLD_PASSWORD)
    assert text == render_bootstrap(GrubConfig(server_base=BASE, password_hash=new_hash))
    assert text.isascii()


def test_a_password_that_could_not_be_read_drops_the_old_one_and_stays_locked(tmp_path):
    """ensure() returns "" on an error, and the served menus are then locked
    with no password; the bootstrap must not keep accepting the old one."""
    deploy_net.write_grub_cfg(BASE, tmp_path, password_hash=_hash(OLD_PASSWORD))
    assert deploy_net.refresh_grub_cfg(tmp_path, "") is None
    text = (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii")
    assert LOCK in text.splitlines()
    assert not any(line.startswith("password_pbkdf2 ") for line in text.splitlines())
    assert f"set imagesrv_fallback={BASE.removeprefix('http://')}" in text.splitlines()
