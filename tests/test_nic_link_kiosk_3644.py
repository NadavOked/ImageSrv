"""#3644: the link banner's record reaches the kiosk's state file -- in the process the agent really starts.

#3277/#3612 drew the banner from ``nic_link=<iface>|<Mb/s>|<max>`` and its tests ran ``nic_link_record`` in ONE shell, with
``IFACE`` exported by the test. On the metal (cloner 1, 18/11) the record was never written: ``imagesrv-agent`` gets
``IFACE`` by sourcing ``net.conf`` -- a plain assignment -- and ``gui_parent`` starts ``imagesrv-kiosk`` as a separate
process with ``export SERVER MAC IP RUN_DIR LIB_DIR GUI_SCREEN``. ``IFACE`` was not in that list, so in the kiosk
``_nl_iface_ok`` said no, ``nic_link_record`` printed nothing, and nothing said so (the hello, built in the agent, was
fine -- that is why the server journalled ``nic_link_low`` while the screen had no banner).

So here the two processes are real: the "agent" sources a ``net.conf`` and runs the ``export`` line **parsed out of
guiparent.sh**; the "kiosk" is the wrapper **parsed out of build_initramfs.sh**, then ``gui_libs`` and
``cloner_gui_state`` -- the kiosk's own load chain. What the child sees is what the export line hands it, nothing else.

Positive evidence only: the record read back from the state file, and for the failure the line read back from the log.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_cloner_gui import AGENT, BASH, REPO, posix, sh, target

pytestmark = requires_native(("bash", BASH))

PARENT = (AGENT / "lib/gui/guiparent.sh").read_text(encoding="utf-8")
BUILDER = (REPO / "tools/build_initramfs.sh").read_text(encoding="utf-8")

#: ethtool as the card prints it: 100 now, 1000 supported.
ETHTOOL = """#!/bin/sh
cat <<'EOT'
Settings for eth0:
	Supported link modes:   10baseT/Full 100baseT/Full 1000baseT/Full
	Supported pause frame use: Symmetric
	Speed: 100Mb/s
EOT
"""


def _export_line() -> str:
    """The line gui_parent exports the kiosk's environment with -- the only door a variable has into the kiosk."""
    found = re.findall(r"^\s*(export SERVER MAC IP [^\n#]*)", PARENT, flags=re.M)
    assert len(found) == 1, found
    return found[0].strip()


def _kiosk_wrapper() -> str:
    """The real /usr/bin/imagesrv-kiosk, minus its last line (gui_main starts a GUI; the test asks for one state tick)."""
    body = re.search(r"imagesrv-kiosk\" << 'EOF'\n(.*?)\nEOF\n", BUILDER, flags=re.S)
    assert body, "the kiosk wrapper moved in build_initramfs.sh"
    lines = body.group(1).splitlines()
    assert lines[-1].strip() == 'gui_main "$@"', lines
    return "\n".join(lines[:-1]) + "\n"


def _tick(tmp_path: Path, speed: str | None = "100\n", drawers: bool = False, net_conf: bool = True,
          ticks: int = 1) -> tuple[list[str], str]:
    """Agent process -> export line -> kiosk process -> ``cloner_gui_state`` ``ticks`` times. (state lines, agent log)."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    root = tmp_path / "sysroot"
    (root / "sys/class/net/eth0").mkdir(parents=True)
    (root / "sys/block/sda/device").mkdir(parents=True)
    (root / "sys/block/sda/size").write_text("1000\n", newline="\n")
    if speed is not None:
        (root / "sys/class/net/eth0/speed").write_text(speed, newline="\n")
    fake = tmp_path / "ethtool"; fake.write_text(ETHTOOL, newline="\n"); fake.chmod(0o755)
    if drawers:
        (run / "response.json").write_text('{"schema":1}', newline="\n")
        target(run, "sda", "writing", 100, 40, "5")
    if net_conf:   # what init leaves for the agent (agent/init:250)
        (run / "net.conf").write_text('IFACE=eth0\nIP=10.55.0.9\nNICS_DOWN=""\n', newline="\n")
    kiosk = tmp_path / "kiosk.sh"
    kiosk.write_text(_kiosk_wrapper() + "gui_libs\nGUI_DIR=${GUI_DIR:-$RUN_DIR/gui}\n"
                     + "cloner_gui_state || exit 1\n" * ticks, newline="\n")
    script = (
        # the test's own seams, in the environment of everything (they are not what is under test)
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} SYSROOT={posix(root)!r} LIB_DIR={posix(flat_lib())!r} '
        f'ETHTOOL={posix(fake)!r} IMAGESRV_TEST=1; '
        # imagesrv-agent:66-69: net.conf is sourced -- a plain assignment, IFACE is NOT exported by it
        'if [ -f "$RUN_DIR/net.conf" ]; then . "$RUN_DIR/net.conf"; fi; '
        'MAC=aa:bb:cc:dd:ee:ff; SERVER=http://127.0.0.1:9; GUI_SCREEN=cloner; '
        f'{_export_line()}; '
        # imagesrv-kiosk is an exec'd process: it has the exported variables and no others
        f'sh {posix(kiosk)}'
    )
    out = sh(script)
    assert out.returncode == 0, (out.stdout, out.stderr)
    log = run / "agent.log"
    return (gui / "state").read_text().splitlines(), log.read_text() if log.exists() else ""


def test_the_export_line_is_what_this_test_parses():
    """A change that moves the line or the wrapper must fail here by name, not turn the rest into a no-op."""
    assert _export_line().startswith("export SERVER MAC IP ")
    assert "gui_libs" not in _kiosk_wrapper() and ". " in _kiosk_wrapper()


@pytest.mark.parametrize("drawers", [False, True], ids=["waiting-screen", "round-with-a-drawer"])
@pytest.mark.parametrize("speed, want", [("100\n", "nic_link=eth0|100|1000|"), ("1000\n", "nic_link=eth0|1000|1000|")])   # #3663: the empty flag = no verdict yet
def test_the_record_is_written_by_the_kiosk_at_100_and_at_1000(tmp_path, speed, want, drawers):
    lines, log = _tick(tmp_path, speed=speed, drawers=drawers)
    assert want in lines, (lines, log)
    assert "nic_link" not in log, log      # a record that is written says nothing


def test_an_unreadable_speed_is_an_empty_field_not_a_missing_record(tmp_path):
    lines, _ = _tick(tmp_path, speed=None)
    assert "nic_link=eth0||1000|" in lines, lines    # the max is still the card's own; no speed = no banner (and no verdict)


def test_a_kiosk_that_cannot_name_its_interface_says_so_in_the_log_once(tmp_path):
    lines, log = _tick(tmp_path, net_conf=False, ticks=3)    # no net.conf and nothing exported: nothing tells the kiosk
    assert not [l for l in lines if l.startswith("nic_link=")], lines
    said = [l for l in log.splitlines() if "nic_link" in l]
    assert len(said) == 1 and "IFACE" in said[0] and "#3644" in said[0], log      # once, not on every tick
    assert [l for l in lines if l.startswith("drawer=") or l.startswith("cdisk=")], lines   # and the drawers are still drawn


def test_the_missing_function_is_a_log_line_and_the_drawers_still_come(tmp_path):
    """The case the issue named first: a process whose load chain lacks sysinfo.sh. The old `|| :` made it silence."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (tmp_path / "sysroot").mkdir()
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} IMAGESRV_TEST=1 LIB_DIR={posix(flat_lib())!r} '
             f'SYSROOT={posix(tmp_path / "sysroot")!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; unset -f nic_link_record; cloner_gui_state; cloner_gui_state')
    assert out.returncode == 0, out.stderr
    log = (run / "agent.log").read_text() if (run / "agent.log").exists() else ""   # no log file at all is the silence this test is about
    said = [l for l in log.splitlines() if "nic_link_record" in l]
    assert len(said) == 1 and "not defined" in said[0], log
    assert (gui / "state").read_text().splitlines()[0].startswith("cloner="), "the state was still written"


def test_the_log_line_never_becomes_a_state_record(tmp_path):
    """`log` prints to stdout while no GUI owns the console (common.sh), and `cloner_nic_link`'s stdout IS the state file:
    the same trap as #3637 (a log line inside a body). One tick -- the next tick rewrites the file and the line would be gone."""
    lines, log = _tick(tmp_path, net_conf=False, ticks=1)
    assert "no usable IFACE" in log, log                       # it was said ...
    assert not [l for l in lines if l.startswith("imagesrv:")], lines   # ... in the log, and nowhere else
