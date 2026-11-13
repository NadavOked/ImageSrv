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

import os
import subprocess

import pytest

from test_nftables_rules import BASH, needs_bash
from test_wizard_guard_2490 import (MAC, OTHER_MAC, REPO, _fact, _run, _sys_net,
                                    _wizard_start_segment)

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
STUBS = r'''set -euo pipefail
fail() { printf "FAIL %s\n" "$1"; exit 1; }
log() { printf "LOG %s\n" "$*"; }
install() { :; }
systemctl() { printf "systemctl %s\n" "$*" >> "$CALLS"; }
nft() {
    printf "nft %s\n" "$*" >> "$CALLS"
    case "$1" in
        -f) cat > "$LOADED" ;;
        list) if [ -n "$STRIP" ]; then grep -v -- "$STRIP" "$LOADED" || true; else cat "$LOADED"; fi ;;
    esac
}
'''


@needs_bash
@pytest.mark.parametrize("strip", ["", "udp dport 53", "tcp dport 53", "udp dport 111",
                                   "tcp dport 111"])
def test_firstboot_starts_the_wizard_only_when_the_dns_and_rpcbind_drops_read_back(tmp_path, strip):
    sys_net = _sys_net(tmp_path, {"ens18": OTHER_MAC, "ens19": MAC})
    fact = _fact(tmp_path, f"state=configured\nmac={MAC}\n")
    calls, loaded, script = tmp_path / "calls", tmp_path / "loaded.nft", tmp_path / "segment.sh"
    units = tmp_path / "systemd"
    (units / "imagesrv-dcui.service.d").mkdir(parents=True)
    segment = _wizard_start_segment().replace("/etc/systemd/system/", f"{units.as_posix()}/")
    assert "/etc/" not in segment, "the segment would write to the real /etc"
    script.write_text(STUBS + segment, encoding="utf-8", newline="\n")
    env = {**os.environ, "SRC": REPO.as_posix(), "NIC_FILE": fact.as_posix(),
           "IMAGESRV_SYS_NET": sys_net.as_posix(), "STATUS": (tmp_path / "status").as_posix(),
           "ROLE": "standalone", "STRIP": strip, "CALLS": calls.as_posix(),
           "LOADED": loaded.as_posix()}
    proc = subprocess.run([BASH, str(script)], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, env=env, check=False)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    started = "systemctl start imagesrv-wizard" in log
    if not strip:
        assert proc.returncode == 0 and started, (proc.stdout, proc.stderr, log)
    else:
        assert not started, f"the wizard started although {strip!r} was not read back: {log}"
        assert "FAIL wizard-guard-failed" in proc.stdout, (proc.stdout, proc.stderr)
