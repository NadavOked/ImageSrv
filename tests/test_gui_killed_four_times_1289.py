"""#1289, ARGUS (17/11, mc1114-c2): `kill` on the kiosk four times, ~5 s apart. The GUI came back three times (1-3 of 3) and
after the fourth: no kiosk, and in `agent.log` no line, no report to the server, no boot from the local disk.

This drives the keeper (gui_keep) and the main loop's next `gui_parent` the way that run did: the bridge is killed by
signal (not an exit code), four times, and the test reads what the agent says afterwards. **It passes on the current code**:
the chain keeper -> "gave up" line -> gui_parent false -> gui_unavailable_local (BootNext, report, die_local) holds under
four signalled bridges. It pins that contract; it does not explain the silence on the VM (see the PR).
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from test_gui_keeper_1726 import (POSIX, _alive, _await, _lines, _report, _start, _stop, _write)

#: the bridge as test_gui_keeper_1726 mocks it, with its own pid kept so a test can kill THE BRIDGE (what `kill imagesrv-kiosk` hits).
KIOSK_PID = r'''#!/bin/sh
echo "$$" >> "$RUN_DIR/bridges"
echo "run screen=$GUI_SCREEN" >> "$RUN_DIR/kiosk.runs"
mkdir -p "$RUN_DIR/gui"
sleep 300 & s=$!
echo "$s $s" > "$RUN_DIR/gui/station.pid"
echo "$s" >> "$RUN_DIR/stations"
wait "$s"
'''

#: what the build's main loop does when `gui_parent` says no (imagesrv-agent build_console_screen): the report and the local boot,
#: here as recorded calls -- the real ones need a server and an EFI variable store.
LOOP = r'''
boot_next_local() { echo "set:ok"; }
gui_failed_report() { echo "report $*" >> "$RUN_DIR/reports"; return 0; }
die_local() { echo "DIE_LOCAL $1" >> "$RUN_DIR/reports"; exit 0; }
sleep() { command sleep "$@"; }
D_ROLE=cloner; _gui_pid=; _gui_disabled=0
GUI_FAIL_SHOW_S=0
n=0
while :; do
    if ! gui_parent; then gui_unavailable_local cloner; fi
    command sleep 1
done
'''


def _bridges(tmp_path: Path) -> list[int]:
    return [int(x) for x in _lines(tmp_path / "run" / "bridges")]


@POSIX
def test_the_gui_killed_four_times_gives_the_message_the_report_and_the_local_boot(tmp_path):
    import test_gui_keeper_1726 as keeper
    original = keeper.KIOSK
    keeper.KIOSK = KIOSK_PID
    try:
        proc = _start(tmp_path, LOOP, env="GUI_RESPAWN_MAX=3 GUI_KEEP_TICK_S=1")
    finally:
        keeper.KIOSK = original
    run = tmp_path / "run"
    try:
        for i in range(1, 5):
            first = _await(lambda: len(_bridges(tmp_path)) >= i, 20)
            assert first, f"bridge {i} never started\n" + _report(tmp_path)
            time.sleep(2)                                   # the GUI is up for a moment, as in the run
            os.kill(_bridges(tmp_path)[i - 1], signal.SIGTERM)
        log = lambda: (run / "agent.log").read_text(encoding="utf-8") if (run / "agent.log").exists() else ""
        assert _await(lambda: "after 3 restarts -- the static screen instead" in log(), 15), \
            "the keeper never said it gave up\n" + _report(tmp_path) + "\n" + log()
        assert _await(lambda: "native GUI unavailable" in log(), 20), \
            "the main loop never reached gui_unavailable_local\n" + _report(tmp_path) + "\n" + log()
        reports = "\n".join(_lines(run / "reports"))
        assert "report cloner set" in reports and "DIE_LOCAL" in reports, (reports, log())
        assert len(_bridges(tmp_path)) == 4, _bridges(tmp_path)   # the first and three restarts: no fifth
    finally:
        _stop(proc)
