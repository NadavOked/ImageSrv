"""#1816: execute the installer's dnsmasq section with sandboxed writes/tools.

These check the installed retry policy and binding, not a running systemd.
The late-NIC recovery/TFTP packet check still needs the Debian VM.
"""
from configparser import ConfigParser
from pathlib import Path
import subprocess

import pytest

from native import requires_native
from test_installer_1131 import BASH, HELPERS


REPO = Path(__file__).resolve().parents[1]
pytestmark = requires_native(("bash", BASH), why="execute dnsmasq installer section")


def install_dnsmasq(tmp_path, *, iface="deploy0", console_iface="", reload_rc=0,
                    dry_run=0):
    text = (REPO / "deploy/setup-boot-server.sh").read_text(encoding="utf-8")
    start = text.index('say "מגדיר dnsmasq')
    end = text.index("\n# חומת אש nftables", start)
    # Capture actual expanded heredocs, with every absolute path inside tmp_path.
    script = "set -euo pipefail\n" + HELPERS + r'''
OUT="$PWD/out"
write_file() {
    printf 'write %s\n' "$1" >> calls.log
    if (( DRY_RUN )); then cat >/dev/null; return; fi
    mkdir -p "$(dirname "$OUT$1")"
    cat > "$OUT$1"
}
systemctl() {
    printf 'systemctl %s\n' "$*" >> calls.log
    if [[ "$1" == daemon-reload ]]; then return "$RELOAD_RC"; fi
}
'''
    script += (f"IFACE={iface!r}\nCONSOLE_DEPLOY_IF={console_iface!r}\n"
               f"DRY_RUN={dry_run}\nRELOAD_RC={reload_rc}\n")
    script += text[start:end]
    result = subprocess.run([BASH, "-c", script], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=15, stdin=subprocess.DEVNULL)
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()
    return result, calls, tmp_path / "out"


@pytest.mark.parametrize("iface,console_iface", [("deploy0", ""), ("", ""), ("", "deploy0")])
def test_installer_installs_unlimited_delayed_retries(tmp_path, iface, console_iface):
    result, calls, out = install_dnsmasq(tmp_path, iface=iface, console_iface=console_iface)
    assert result.returncode == 0, result.stderr
    policy_path = out / "etc/systemd/system/dnsmasq.service.d/imagesrv-restart.conf"
    assert policy_path.is_file(), "installer did not write a dnsmasq retry policy"
    policy = ConfigParser()
    policy.read(policy_path)
    assert policy["Unit"]["StartLimitIntervalSec"] == "0"
    assert policy["Service"]["Restart"] == "on-failure"
    assert policy["Service"]["RestartSec"] == "5s"
    written = calls.index("write /etc/systemd/system/dnsmasq.service.d/imagesrv-restart.conf")
    reloaded = calls.index("systemctl daemon-reload")
    assert written < reloaded
    if iface:
        assert reloaded < calls.index("systemctl restart dnsmasq")
        config = (out / "etc/dnsmasq.d/imagesrv.conf").read_text(encoding="utf-8")
        assert "interface=deploy0" in config.splitlines()
        assert "bind-interfaces" in config.splitlines()
        assert "bind-dynamic" not in config.splitlines()
    elif not console_iface:
        assert "systemctl disable --now dnsmasq" in calls
        assert "systemctl enable dnsmasq" not in calls
        assert "systemctl restart dnsmasq" not in calls
    else:
        assert not any("dnsmasq" in call for call in calls if call.startswith("systemctl"))


def test_daemon_reload_failure_stops_installation_before_restart(tmp_path):
    result, calls, _ = install_dnsmasq(tmp_path, reload_rc=1)
    assert result.returncode != 0
    assert "systemctl daemon-reload" in calls
    assert "systemctl enable dnsmasq" not in calls
    assert "systemctl restart dnsmasq" not in calls


def test_retry_policy_dry_run_does_not_write_or_reload(tmp_path):
    result, calls, out = install_dnsmasq(tmp_path, dry_run=1)
    assert result.returncode == 0, result.stderr
    assert not out.exists()
    assert not any(call.startswith("systemctl") for call in calls)


def test_server_render_still_pins_tftp_to_deploy_nic():
    from server.net import dhcp

    config = dhcp.render([], tftp_root="/srv/tftp", deploy_interface="deploy0",
                         pin_tftp=True).splitlines()
    assert "enable-tftp" in config
    assert "bind-interfaces" in config
    assert [line for line in config if line.startswith("interface=")] == ["interface=deploy0"]
    assert "bind-dynamic" not in config
