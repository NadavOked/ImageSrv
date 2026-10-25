"""#2094 (ISO rc1, ESXi 24/10): no management address on the first boot.

The journal of the installed VM says what happened -- not the NIC rename
(firstboot found ens33 by its MAC, as designed), but the cgroup:

    07:51:34 dhcpcd[14282]: dhcpcd-10.1.0 starting           (ifup, from firstboot)
    07:51:35 dhcpcd[14285]: received SIGTERM, stopping
    07:51:35 dhcpcd[14285]: ens33: removing interface
    07:51:35 systemd[1]: imagectl-firstboot.service: Deactivated successfully.

``dhcpcd -b`` (netcfg.render) stays in the cgroup of whoever ran ``ifup``;
firstboot is a oneshot unit, and when it ends systemd stops its cgroup. So
firstboot hands the NIC to networking.service (RemainAfterExit -- the unit
that owns it on every later boot), waits for the address, and only then lets
the server issue the console certificate for it (#2095).

These run the real ``firstboot_from_answers`` under bash with the host
boundary (systemctl, ip, setup-boot-server, verify) shimmed and recorded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ANSWERS_SH = REPO / "tools" / "iso" / "firstboot-answers.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or os.name == "nt",
                                reason="bash and executable shims (Linux)")


def _shim(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _firstboot(tmp_path: Path, *, lease_after: int | None, ip_fails: bool = False):
    """``lease_after`` = how many address reads come back empty before the
    lease; None = it never comes."""
    calls, bindir = tmp_path / "calls", tmp_path / "bin"
    bindir.mkdir()
    rec = f'printf "%s %s\\n" "${{0##*/}}" "$*" >> {calls}\n'
    for name in ("systemctl", "ifup", "hostnamectl", "sleep"):
        _shim(bindir / name, rec + "exit 0\n")
    _shim(bindir / "hostname", "echo fb-2094\n")
    reads = tmp_path / "reads"
    reads.write_text("0", encoding="ascii")
    if ip_fails:
        ip_body = "exit 1\n"
    elif lease_after is None:
        ip_body = "exit 0\n"
    else:
        ip_body = (f'n=$(cat {reads}); echo $((n + 1)) > {reads}\n'
                   f'[ "$n" -ge {lease_after} ] && echo "2: lo    inet 10.68.0.61/24 brd 10.68.0.255 scope global lo"\n'
                   "exit 0\n")
    _shim(bindir / "ip", ip_body)
    src, app = tmp_path / "src", tmp_path / "app"
    (src / "install").mkdir(parents=True)
    (app / "install").mkdir(parents=True)
    # #2148: and whether first boot asked it to leave the fingerprint to the DCUI
    _shim(src / "install" / "setup-boot-server.sh",
          rec.replace('"${0##*/}" "$*"', '"setup-boot-server" "fp_on_dcui=${IMAGECTL_CONSOLE_FP_ON_DCUI:-} $*"')
          + "exit 0\n")
    _shim(app / "install" / "verify-boot-payload.sh", rec.replace("${0##*/}", "verify-boot-payload") + "exit 0\n")
    answers, secret = tmp_path / "answers", tmp_path / "answers.secret"
    # servers_if=lo: firstboot resolves the NIC from the real /sys/class/net
    answers.write_text("role=standalone\nservers_mode=dhcp\nservers_if=lo\nservers_mac=\n"
                       "hostname=fb-2094\nadmin_user=admin\n", encoding="utf-8")
    secret.write_text("admin_pass=Aa123456!\n", encoding="utf-8")
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""set -euo pipefail
log() {{ printf 'LOG %s\\n' "$*"; }}
fail() {{ printf 'FAIL [%s] %s\\n' "$1" "$2"; exit 1; }}
ANSWERS={answers} ANSWERS_SECRET={secret} APP_DIR={app} SRC={src}
BUILD_LOG={tmp_path}/build.log STATUS={tmp_path}/status STAMP={tmp_path}/stamp HTTP_ROOT={tmp_path}/http
FIRSTBOOT_ADDR_WAIT_S=5
. {ANSWERS_SH.as_posix()}
firstboot_from_answers
""", encoding="utf-8", newline="\n")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    got = subprocess.run([BASH, str(harness)], capture_output=True, text=True, env=env,
                         stdin=subprocess.DEVNULL, timeout=60)
    seen = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return got, seen


def test_the_nic_is_brought_up_by_networking_service_not_inside_firstboot(tmp_path):
    got, seen = _firstboot(tmp_path, lease_after=2)
    assert got.returncode == 0, got.stdout + got.stderr
    assert not [c for c in seen if c.startswith("ifup ")], "ifup inside the oneshot unit: dhcpcd dies with it"
    order = [c.split()[0] if not c.startswith("systemctl") else c for c in seen]
    networking = order.index("systemctl restart networking.service")
    assert order.index("setup-boot-server") < networking, "the interfaces file is written first"
    assert "LOG nic lo: 10.68.0.61/24" in got.stdout


def test_the_server_issues_its_certificate_again_once_the_address_is_there(tmp_path):
    """#2095: setup-boot-server issued it before the lease (loopback + name);
    the restart here gives the first boot its final certificate."""
    got, seen = _firstboot(tmp_path, lease_after=2)
    assert got.returncode == 0, got.stdout + got.stderr
    restart = seen.index("systemctl restart imagectl-server.service")
    assert seen.index("systemctl restart networking.service") < restart
    assert restart < next(i for i, c in enumerate(seen) if c.startswith("verify-boot-payload"))


def test_no_lease_in_time_is_said_and_the_certificate_is_left_alone(tmp_path):
    got, seen = _firstboot(tmp_path, lease_after=None)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "LOG nic lo: up, no IPv4 address after 5s" in got.stdout
    assert "systemctl restart imagectl-server.service" not in seen
    assert (tmp_path / "stamp").exists()


def test_an_address_read_that_fails_is_not_no_address(tmp_path):
    """Principle 5: `ip` failing is "not checked", named as such."""
    got, seen = _firstboot(tmp_path, lease_after=None, ip_fails=True)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "[nic-check-failed]" in got.stdout
    assert "no IPv4 address after" not in got.stdout
    assert "systemctl restart imagectl-server.service" not in seen


# --- the DCUI's "Restart Management Network" on a DHCP NIC ---------------------

def _dcui_restart_dhcp(tmp_path, empty_reads: int | None):
    """``dhcpcd -b`` returns at once; the ESXi lease came ~7 s later (08:03:10
    -> 08:03:17). The read-back was taken at once and said "not verified" on a
    restart that worked. ``empty_reads``: reads before the lease (None = never)."""
    from server import netcfg, netcfg_host
    from server.db import connect, set_setting
    from server.dcui import actions

    nic = "ens33"
    conn = connect(tmp_path / "imagectl.db")
    set_setting(conn, netcfg.SETTING_PREFIX + nic, netcfg.NetConfig(nic, mode="dhcp").to_json())
    clock = {"now": 0.0}
    reads = {"n": 0}

    def state():
        reads["n"] += 1
        leased = empty_reads is not None and reads["n"] > empty_reads
        return netcfg_host.LiveState(True, addresses={nic: ["192.168.68.64/22"]} if leased else {})

    hooks = {"netcfg_apply": lambda name: None, "netcfg_state": state,
             "sleep": lambda s: clock.__setitem__("now", clock["now"] + s),
             "monotonic": lambda: clock["now"]}
    result = actions.restart_management_network(conn, nic, tmp_path, hooks=hooks)
    return result, reads["n"], clock["now"]


def test_a_dhcp_restart_waits_for_the_lease_before_it_judges(tmp_path):
    result, reads, waited = _dcui_restart_dhcp(tmp_path, empty_reads=7)
    assert result.ok, result
    assert reads == 8 and waited == 7


def test_a_dhcp_restart_with_no_lease_still_fails_after_the_wait(tmp_path):
    from server import netcfg_host
    result, _reads, waited = _dcui_restart_dhcp(tmp_path, empty_reads=None)
    assert not result.ok
    assert waited == netcfg_host.DHCP_LEASE_WAIT_S
