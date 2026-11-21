"""#3754: the link banner's record reaches the kiosk that init started BEFORE the DHCP (#1709) -- the early path, end to end.

#3644 put ``IFACE`` into the export line of ``gui_parent`` and tested exactly that line. On the metal (cloner 1, 19/11) the
kiosk was not started by ``gui_parent``: ``gui_early_boot`` (init, in the background, before the module load) started it, when
``net.conf`` did not exist yet and ``MAC``/``IP`` were empty. A process has the environment it was born with, so that kiosk
never had ``IFACE`` -- ``MAC`` and ``IP`` reach it later through ``$RUN_DIR/gui.screen`` (``gui_early_role`` ->
``gui_early_env``), and ``IFACE`` was not in that file. Log: ``no usable IFACE in this process (IFACE='')``.

Three real processes, in this order, as on the machine:
  1. the kiosk, started with the environment **parsed out of guiearly.sh** (``export SERVER MAC IP ... GUI_SCREEN``, MAC and
     IP empty) and **no net.conf yet**; it runs the real wrapper (build_initramfs.sh), ``gui_early_loop`` and then one
     ``cloner_gui_state`` (only the endless state loop is replaced by one tick);
  2. a moment later "init" leaves ``net.conf`` and "the agent" sources it (a plain assignment, nothing exported) and runs the real
     ``gui_early_role`` -- in a shell of its own, so nothing leaks to the kiosk but the file.
Positive evidence only: the record read back from the state file; for the failure, the log line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh
from test_nic_link_kiosk_3644 import ETHTOOL, _kiosk_wrapper

pytestmark = requires_native(("bash", BASH))

EARLY = (AGENT / "lib/gui/guiearly.sh").read_text(encoding="utf-8")


def _early_export_line() -> str:
    """The line gui_early_boot exports the early kiosk's environment with -- the only door a variable has into it."""
    found = re.findall(r"^\s*(export SERVER MAC IP [^\n#]*)", EARLY, flags=re.M)
    assert len(found) == 1, found
    return found[0].strip()


def _run(tmp_path: Path, handoff: str = "real") -> tuple[list[str], str]:
    """(gui/state lines, agent.log). ``handoff`` = "real" (gui_early_role) or "three-lines" (the file as it was before #3754)."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    root = tmp_path / "sysroot"
    (root / "sys/class/net/eth0").mkdir(parents=True)
    (root / "sys/block/sda/device").mkdir(parents=True)
    (root / "sys/block/sda/size").write_text("1000\n", newline="\n")
    (root / "sys/class/net/eth0/speed").write_text("100\n", newline="\n")
    fake = tmp_path / "ethtool"; fake.write_text(ETHTOOL, newline="\n"); fake.chmod(0o755)
    kiosk = tmp_path / "kiosk.sh"
    kiosk.write_text(
        _kiosk_wrapper() + "gui_libs\nGUI_DIR=${GUI_DIR:-$RUN_DIR/gui}\n"
        f'echo "IFACE at start: [${{IFACE-unset}}]" > {posix(run)}/kiosk.start\n'
        "cloner_gui_state_loop() { cloner_gui_state; }   # the test's only seam: one tick instead of the endless loop\n"
        "gui_early_loop\n", newline="\n")
    role = ('printf "cloner\\naa:bb:cc:dd:ee:ff\\n10.55.0.9\\n" > "$RUN_DIR/gui.screen"' if handoff == "three-lines"
            else "gui_early_role")
    agent = tmp_path / "agent.sh"
    agent.write_text(
        '. "$LIB_DIR/common.sh"; . "$LIB_DIR/guiearly.sh"\n'
        'printf \'IFACE=eth0\\nIP=10.55.0.9\\nNICS_DOWN=""\\n\' > "$RUN_DIR/net.conf"   # init:250\n'
        '. "$RUN_DIR/net.conf"                                                        # imagesrv-agent:68 -- not exported\n'
        'MAC=aa:bb:cc:dd:ee:ff; D_ROLE=cloner\n' + role + "\n", newline="\n")
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} SYSROOT={posix(root)!r} LIB_DIR={posix(flat_lib())!r} '
        f'ETHTOOL={posix(fake)!r} IMAGESRV_TEST=1; '
        # gui_early_boot: before DHCP -- no MAC, no IP, no IFACE, no net.conf
        'SERVER=http://127.0.0.1:9; MAC=; IP=; GUI_SCREEN=boot; '
        f'{_early_export_line()}; '
        f'timeout 60 sh {posix(kiosk)} & kp=$!; '
        'sleep 1; '
        # init finished the DHCP; the agent is another process and exports nothing to the kiosk
        f'( unset IFACE MAC IP SERVER GUI_SCREEN; sh {posix(agent)} ) || exit 3; '
        'wait $kp'
    )
    out = sh(script, timeout=90)
    assert out.returncode == 0, (out.stdout, out.stderr)
    assert "unset" in (run / "kiosk.start").read_text(), "the early kiosk must be born without IFACE (that is the case)"
    log = run / "agent.log"
    return (gui / "state").read_text().splitlines(), log.read_text() if log.exists() else ""


def test_the_export_line_is_what_this_test_parses():
    """A change that moves the line must fail here by name, not turn the rest into a no-op. IFACE is NOT on it: it cannot be."""
    assert _early_export_line().endswith("GUI_SCREEN") and "IFACE" not in _early_export_line()


def test_the_early_kiosk_writes_the_link_record_after_the_role_hand_over(tmp_path):
    lines, log = _run(tmp_path)
    assert "nic_link=eth0|100|1000|" in lines, (lines, log)   # #3663: the empty flag = no verdict yet
    assert "nic_link" not in log, log                         # a record that is written says nothing


def test_a_hand_over_without_the_interface_still_says_so_in_the_log(tmp_path):
    """The three-line file (before #3754): the role, MAC and IP are taken, the record is not, and the log says why (#3644)."""
    lines, log = _run(tmp_path, handoff="three-lines")
    assert "cloner=" in "".join(lines) and "screen=cloner" in lines, lines    # the role was taken
    assert not [l for l in lines if l.startswith("nic_link=")], lines
    assert "no usable IFACE" in log, log
