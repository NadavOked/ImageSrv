"""Exercise the real shutdown path with fake NICs and harmless stop commands."""

import re
import shlex
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix


def finish_probe(tmp_path, *, after="poweroff", mode="ok", role=None):
    net = tmp_path / "sys/class/net"
    net.mkdir(parents=True)
    for name in ("lo", "eth0", "eth1"):
        (net / name).mkdir()
    config = tmp_path / "after-task"
    if after is not None and mode != "missing":
        config.write_text(after, encoding="utf-8")
    role_line = f"D_ROLE={shlex.quote(role)}" if role is not None else ":"
    script = f"""set -u
SYSROOT={shlex.quote(posix(tmp_path))}
AFTER_TASK_FILE={shlex.quote(posix(config))}
{role_line}
. {shlex.quote(posix(AGENT / 'lib/core/common.sh'))}
mode={shlex.quote(mode)}
log() {{ printf 'LOG %s\n' "$*"; }}
sync() {{ echo SYNC; }}
poweroff() {{ echo "POWEROFF $*"; }}
reboot() {{ echo "REBOOT $*"; }}
ethtool() {{
    if [ "$1" = -s ]; then
        echo "SET $2 $3 $4"
        [ "$mode" != set-fails ] || return 1
        : > "$SYSROOT/armed-$2"
        return 0
    fi
    [ "$mode" != query-fails ] || return 1
    if [ "$1" = eth0 ]; then
        echo 'Supports Wake-on: d'
        echo 'Wake-on: d'
        return 0
    fi
    [ "$1" = eth1 ] || return 99
    echo 'Supports Wake-on: pumbg'
    if [ -e "$SYSROOT/armed-$1" ]; then
        [ "$mode" != readback-fails ] || return 1
        if [ "$mode" != unchanged ]; then echo 'Wake-on: g'; return 0; fi
    fi
    echo 'Wake-on: d'
}}
# A shell-only missing-tool case cannot accidentally find the host ethtool.
if [ "$mode" = missing ]; then unset -f ethtool; PATH=/nonexistent; fi
finish_and_stop
echo "RETURNED:$?"
"""
    result = subprocess.run(
        [BASH, "-c", script], cwd=REPO, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@requires_native(("bash", BASH))
@pytest.mark.parametrize("after", ["poweroff", "invalid", None])
def test_poweroff_arms_before_stopping(tmp_path, after):
    out = finish_probe(tmp_path, after=after)
    assert "LOG wol: eth1 armed" in out, out
    assert out.index("LOG wol: eth1 armed") < out.index("SYNC")
    assert out.index("SYNC") < out.index("POWEROFF ")
    assert "eth0 armed" not in out
    assert "REBOOT" not in out


@requires_native(("bash", BASH))
@pytest.mark.parametrize("mode", [
    "set-fails", "unchanged", "query-fails", "readback-fails", "missing",
])
def test_arming_failure_keeps_the_machine_powered_on(tmp_path, mode):
    out = finish_probe(tmp_path, mode=mode)
    assert "POWEROFF" not in out and "SYNC" not in out, out
    assert "RETURNED:1" in out, out
    assert "not all supported wired interfaces were armed -- not powering off" in out, out
    assert "LOG wol: eth1 armed" not in out


@requires_native(("bash", BASH))
def test_reboot_does_not_arm(tmp_path):
    out = finish_probe(tmp_path, after="reboot")
    assert "REBOOT -f\nRETURNED:0" in out, out
    assert "wol:" not in out
    assert "POWEROFF" not in out


@requires_native(("bash", BASH))
def test_classroom_role_reboots_without_arming(tmp_path):
    """בלי override, תפקיד classroom נגזר ל-reboot — ואינו חמושת WoL."""
    out = finish_probe(tmp_path, after=None, role="classroom")
    assert "REBOOT -f\nRETURNED:0" in out, out
    assert "wol:" not in out
    assert "POWEROFF" not in out


@requires_native(("bash", BASH))
def test_cloner_role_arms_and_powers_off(tmp_path):
    """בלי override, תפקיד cloner נגזר ל-poweroff — וחומש WoL קודם."""
    out = finish_probe(tmp_path, after=None, role="cloner")
    assert "LOG wol: eth1 armed" in out, out
    assert out.index("LOG wol: eth1 armed") < out.index("POWEROFF ")
    assert "REBOOT" not in out


def test_ethtool_is_installed_and_packed():
    src = (REPO / "tools/build_initramfs.sh").read_text(encoding="utf-8")
    binaries = re.search(r"BINARIES=\((.*?)\)", src, re.S).group(1).split()
    assert "ethtool" in binaries
    # ⚠️ **הפקודה, לא ההערה שמזכירה אותה.** ‏`src.index("apt-get install")`
    # נופל על הערה בעברית שמסבירה מה קורה כשהחבילה חסרה, והטסט היה
    # נכשל על מילות ההערה בזמן ש-`ethtool` **כן** ברשימה. טסט שנכשל
    # על הדבר הלא נכון אינו מעיד גם כשהוא אדום.
    #
    # העוגן הוא `busybox-static` — חבילה שחייבת להיות באותה שורת
    # התקנה, ולכן היא מזהה את הפקודה האמיתית ולא טקסט חופשי.
    # מאתרים את החבילה-העוגן ומטפסים אחורה אל ה-`apt-get install`
    # שלה. ‏regex עם המשכי-שורה נבלע ב-backslash ולא מצא כלום —
    # ונתפס בבקרה השלילית, לא בקריאה.
    anchor = src.index("busybox-static")
    start = src.rindex("apt-get install", 0, anchor)
    end = anchor
    while end < len(src) and src[end] != "\n" or src[end - 1:end + 1] == "\\\n":
        end += 1
    install = src[start:end]
    assert "busybox-static" in install, "לא נמצאה שורת ההתקנה עצמה"
    assert "ethtool" in install.split()
