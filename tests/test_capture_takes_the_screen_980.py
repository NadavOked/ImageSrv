"""#980 -- a capture ordered from the console after a restore that left the machine on gets its own screen.

Metal 17/11 (bld1117): the restore ended with the machine staying on ("השחזור הושלם -- המחשב לא כובה", WoL not armed
on a VM). A capture then started from the console: the server's stages moved (stream -> verify -> done) and the
machine's screen kept the restore's last card. Why: the restore's GUI (srgui.sh, `restore.final`) was alive and
`gui_parent` was shut by the restore's handoff (`_gui_disabled=1`), so the capture ran with no GUI of its own.

`do_task` (task.sh) now releases that GUI -- the keeper first, then the GUI (`capture_gui_take`) -- before it asks
`gui_parent` for the capture's own. The real `do_task` runs here, with the capture itself stubbed out and two `sleep`s
standing for the keeper and the GUI; what the kiosk then draws (the progress card of a running task) is native-gui's,
and needs a screen: see the PR.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "waits", "srgui", "task")


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    flat = posix(flat_lib())
    src = "".join(f". {flat}/{n}.sh; " for n in LIBS)
    script = (f"export RUN_DIR={posix(run)!r} LIB_DIR={flat!r} IMAGESRV_TEST=1 WAIT_HELPER_S=3; "
              f"LOG_FILE={posix(run)!r}/agent.log; export LOG_FILE; " + src + body)
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL)


#: a keeper and a GUI that really are processes: `sleep`s, the GUI's pid written where srgui.sh writes it.
SETUP = '''
mkdir -p "$RUN_DIR/gui"
sleep 60 >/dev/null 2>&1 & SR_GUI_PID=$!; _ks=$SR_GUI_PID
sleep 60 >/dev/null 2>&1 & _gui_g=$!
echo "$_gui_g" > "$RUN_DIR/gui/restore.gpid"
echo "$SR_GUI_PID" > "$RUN_DIR/gui.pid"
printf 'message=x|y\n' > "$RUN_DIR/gui/restore.final"
'''

#: do_task's surroundings, stubbed: a capture task whose disk read fails at once (the screen is the subject, not the
#: capture). `gui_parent` records what it finds -- the flag, and whether the restore's GUI is still a process.
STUBS = '''
RESP=x; MAC=aa:bb:cc:dd:00:01; SERVER=http://s
json_get() { case "$2" in .task.type) echo capture ;; .task.id) echo tsk_1 ;; *) echo x ;; esac; }
json_disk() { echo sda; }
job_reset() { :; }
ui_screen_ours() { return 0; }
progress_loop() { sleep 0; }
capture_disk() { return 1; }
report_final() { return 0; }
ui_error_hold() { :; }
hold_unheard() { return 1; }
gui_parent() {
    kg=gone; kill -0 "$_gui_g" 2>/dev/null && kg=alive
    kk=gone; { [ -n "${SR_GUI_PID:-}" ] && kill -0 "$SR_GUI_PID" 2>/dev/null; } && kk=alive
    echo "gui_parent disabled=${_gui_disabled:-0} restore_gui=$kg keeper=$kk final=$([ -e "$RUN_DIR/gui/restore.final" ] && echo kept || echo gone)"
    return 0
}
'''

END = '''
kill "$_gui_g" ${SR_GUI_PID:+"$SR_GUI_PID"} ${_ks:+"$_ks"} 2>/dev/null; :
'''


def test_a_restore_screen_left_up_is_released_before_the_capture_asks_for_a_gui(tmp_path):
    out = _run(tmp_path, SETUP + STUBS + "_gui_disabled=1; _gui_pid=; do_task; echo rc=$?; " + END)
    assert out.returncode == 0, out.stderr
    assert "gui_parent disabled=0 restore_gui=gone keeper=gone final=gone" in out.stdout, out.stdout
    log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
    assert "the screen of the restore that ended on this boot is released" in log, log


def test_a_live_kiosk_is_left_alone(tmp_path):
    """The operator started the capture from the GUI's own menu: `_gui_disabled` is 0, the kiosk is the keeper."""
    out = _run(tmp_path, SETUP + STUBS + "_gui_disabled=0; do_task; echo rc=$?; " + END)
    assert out.returncode == 0, out.stderr
    assert "gui_parent disabled=0 restore_gui=alive keeper=alive final=kept" in out.stdout, out.stdout


def test_with_no_restore_screen_nothing_is_touched(tmp_path):
    """No restore GUI of this boot (`SR_GUI_PID` empty): not a process killed, not a flag flipped."""
    out = _run(tmp_path, SETUP + STUBS + "SR_GUI_PID=; _gui_disabled=1; do_task; echo rc=$?; " + END)
    assert out.returncode == 0, out.stderr
    assert "gui_parent disabled=1 restore_gui=alive keeper=gone final=kept" in out.stdout, out.stdout
