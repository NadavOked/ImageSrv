"""#1483 -- metal, cloner 2 (HP 8100), 16/10: a local clone in which every
target failed ended on "השיכפול נכשל — אף יעד לא הושלם". Enter on the focused
"כבה את המחשב" did nothing, and the screen said nothing.

What the fix promises, each one tested here:

1. ``safe_poweroff`` powers off with ``poweroff -f``. PID 1 is ``agent/init``,
   a shell script: busybox ``poweroff`` without ``-f`` only signals PID 1,
   the kernel drops a signal an init without a handler did not ask for, and
   the command exits 0 having done nothing (#1378 dropped the ``-f``). A
   ``poweroff`` that returns at all did not power off -- a failure, aloud,
   and named as itself: the idle path (cloner 2) and the task-complete path
   (HP-BUILD: "wol: eth0 armed" then "wol not armed, staying powered on")
   both logged "powering off" on metal and stayed on.
2. After a run in which every target failed, Enter reaches ``lc_poweroff``
   within seconds: the result screen (phase ``done``) appears only when the
   loop that reads ``end.req`` is the next thing to run, not before the
   report to the server (curl, up to 10 s) and the beat's wait.
3. The key is never ignored in silence: the screen says it is powering off,
   and a refusal -- Wake-on-LAN not armed, ``poweroff`` that returned, a
   click the bridge cannot accept -- is said on it (``toast=``), with why.
"""

from __future__ import annotations

import re
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import LIB, run

pytestmark = requires_native(("bash", BASH))

#: no hdparm, no /dev of the host: disk_serial/disk_port read an empty sysfs
FAKE = ('export SYSROOT="$RUN_DIR/sys" DEVROOT="$RUN_DIR/dev"; mkdir -p "$SYSROOT" "$DEVROOT"; '
        'mkdir -p "$LC" "$RUN_DIR/gui"; GUI_DIR="$RUN_DIR/gui"; ')


# --- 1. safe_poweroff -----------------------------------------------------------------

def _safe_poweroff(tmp_path: Path, poweroff_body: str) -> tuple[int, str]:
    """The real safe_poweroff, out of test mode, with poweroff/sync replaced by
    shell functions (a function wins over any binary: nothing is powered off)."""
    out = run(tmp_path, FAKE + 'arm_wol() { return 0; }; sync() { :; }; '
              f'poweroff() {{ echo "POWEROFF-ARGS=[$*]"; {poweroff_body} }}; '
              'export LOG_FILE="$RUN_DIR/agent.log"; '
              'safe_poweroff "test"; echo "rc=$?"; echo "why=${POWEROFF_WHY:-}"; cat "$LOG_FILE" 2>/dev/null',
              IMAGESRV_TEST="0")
    assert out.returncode == 0, out.stderr
    rc = int(re.search(r"^rc=(\d+)$", out.stdout, re.M).group(1))
    return rc, out.stdout


def test_safe_poweroff_forces_the_power_off_and_a_return_is_a_failure(tmp_path):
    # busybox poweroff without -f: a signal to a shell PID 1, exit 0, still on.
    rc, out = _safe_poweroff(tmp_path, "return 0;")
    assert "POWEROFF-ARGS=[-f]" in out, out
    assert rc != 0, out                      # it came back: the machine is on
    assert "why=poweroff" in out, out
    assert "the machine is still on" in out, out


def test_a_poweroff_that_came_back_is_on_every_screen(tmp_path):
    """The idle loop on cloner 2 (16/10) said "powering off" every ~45 s and
    the screen never said anything. gui_hello_state is in every state file
    (station, cloner standby, local clone): the failure is drawn as toast=."""
    out = run(tmp_path, FAKE + 'arm_wol() { return 0; }; sync() { :; }; poweroff() { return 0; }; '
              'export LOG_FILE="$RUN_DIR/agent.log"; safe_poweroff "idle"; gui_hello_state; '
              'echo "---"; arm_wol() { return 1; }; send_hello() { :; }; safe_poweroff "idle"; gui_hello_state',
              IMAGESRV_TEST="0")
    assert out.returncode == 0, out.stderr
    after_return, after_wol = out.stdout.split("---", 1)
    toasts = [l for l in after_return.splitlines() if l.startswith("toast=")]
    assert toasts and "לא כובה" in toasts[0] and "rc=0" in toasts[0], after_return
    # a later attempt clears it; Wake-on-LAN refusals keep their own channel (#1378)
    assert "toast=" not in after_wol, after_wol


def test_a_task_complete_poweroff_that_came_back_is_not_called_wol(tmp_path):
    """HP-BUILD, end of a good capture, 16/10 -- in the same second:
    "wol: eth0 armed" / "powering off with wake-on-LAN armed" / "task complete
    -- wol not armed, staying powered on", and the machine stayed on. The real
    finish_and_stop and task_done_stays_on: the reason said is the real one."""
    out = run(tmp_path, FAKE + f'. {posix(LIB)}/task.sh; ui_clear() {{ :; }}; ui_header() {{ :; }}; '
              'arm_wol() { log "wol: eth0 armed"; return 0; }; sync() { :; }; '
              'poweroff() { echo "POWEROFF-ARGS=[$*]"; return 0; }; '
              'export LOG_FILE="$RUN_DIR/agent.log" D_ROLE=build AFTER_TASK_FILE=/nonexistent/after-task; '
              'finish_and_stop; task_done_stays_on "Capture complete."; echo "---LOG"; cat "$LOG_FILE"',
              IMAGESRV_TEST="0")
    assert out.returncode == 0, out.stderr
    screen, log = out.stdout.split("---LOG", 1)
    assert "POWEROFF-ARGS=[-f]" in screen, screen
    assert "wol not armed" not in log, log
    assert "poweroff -f returned rc=0" in log and "the power-off did not take" in log, log
    assert "still on" in screen and "Wake-on-LAN could not be armed" not in screen, screen


# --- 2. Enter after an all-failed run -> lc_poweroff within seconds ----------------------

#: The session's world: the inventory is the stub that hands lc_session the
#: start request (lc_session clears requests on entry); the source has no
#: inventory line, so lc_run's first check fails every target -- an
#: all-failed run through the real lc_run/lc_end/lc_report. The server does
#: not answer: curl takes 6 s, as --max-time 10 does against a silent host.
#: The operator is the real bridge (lc_gui_dispatch), pressing Enter the
#: moment the result screen (phase done) is up. lc_poweroff is the real one;
#: safe_poweroff records when it was reached, and what the screen said.
SESSION = (
    'attended_start() { :; }; attended_stop() { :; }; '
    'lc_inventory() { mkdir -p "$LC/inv"; [ -f "$RUN_DIR/started.once" ] && return 0; '
    '  : > "$RUN_DIR/started.once"; echo "sda|sdb|0" > "$LC/start.req"; }; '
    'curl() { sleep 6; printf 000; }; '
    'safe_poweroff() { date +%s.%N > "$RUN_DIR/off.at"; cat "$LC/run/notice" > "$RUN_DIR/off.notice" 2>/dev/null; '
    '  kill $_gui_pid; exit 0; }; '
    'sleep 300 > /dev/null 2>&1 & _gui_pid=$!; '
    '( while [ "$(cat "$LC/phase" 2>/dev/null)" != done ]; do sleep 0.1; done; '
    '  date +%s.%N > "$RUN_DIR/pressed.at"; token=lc-poweroff; lc_gui_dispatch && echo ok > "$RUN_DIR/dispatched" '
    ') > /dev/null 2>&1 & '
    '( sleep 40; kill $_gui_pid ) > /dev/null 2>&1 & '   # the old code's endless wait ends on an assertion
    'export LOG_FILE="$RUN_DIR/agent.log" WAIT_POLL_S=1; '
    'lc_session; echo "session returned: $LC_END"'
)


def test_enter_on_the_result_of_an_all_failed_run_powers_off_within_seconds(tmp_path):
    out = run(tmp_path, FAKE + SESSION, timeout=90)
    run_dir = tmp_path / "run"
    assert (run_dir / "dispatched").is_file(), out.stdout + out.stderr      # the bridge took the key
    assert (run_dir / "targets" / "sdb" / "state").read_text().strip() == "failed"
    assert (run_dir / "off.at").is_file(), out.stdout + out.stderr          # lc_poweroff was reached
    delay = float((run_dir / "off.at").read_text()) - float((run_dir / "pressed.at").read_text())
    assert delay < 3, f"Enter -> safe_poweroff took {delay:.1f}s"
    # the screen said it, from the moment the key was taken
    assert "מכבה" in (run_dir / "off.notice").read_text(encoding="utf-8")


# --- 3. a refusal is said on the screen ---------------------------------------------------

def _state(tmp_path: Path, script: str) -> list[str]:
    out = run(tmp_path, FAKE + 'mkdir -p "$LC/run"; : > "$LC/run/targets"; echo local > "$LC/screen"; '
              'echo done > "$LC/phase"; export LOG_FILE="$RUN_DIR/agent.log"; ' + script +
              '; lc_gui_state; cat "$GUI_DIR/state"', IMAGESRV_TEST="0")
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_wake_on_lan_not_armed_is_said_on_the_screen(tmp_path):
    lines = _state(tmp_path, 'arm_wol() { return 1; }; send_hello() { :; }; '
                   'poweroff() { echo "MUST NOT RUN"; }; lc_poweroff')
    assert "MUST NOT RUN" not in lines
    toasts = [l for l in lines if l.startswith("toast=")]
    assert toasts and "לא כובה" in toasts[-1] and "Wake-on-LAN" in toasts[-1], lines


def test_a_poweroff_that_returned_is_said_on_the_screen(tmp_path):
    lines = _state(tmp_path, 'arm_wol() { return 0; }; sync() { :; }; poweroff() { return 0; }; lc_poweroff')
    toasts = [l for l in lines if l.startswith("toast=")]
    assert toasts and "לא כובה" in toasts[-1], lines


def test_a_poweroff_click_the_bridge_refuses_is_said_on_the_screen(tmp_path):
    # the result is not on the agent's side yet (still verifying): refused, aloud
    lines = _state(tmp_path, 'echo verifying > "$LC/phase"; token=lc-poweroff; '
                   'lc_gui_dispatch && echo "TAKEN"; [ -f "$LC/end.req" ] && echo "END-REQ"')
    assert "TAKEN" not in lines and "END-REQ" not in lines
    toasts = [l for l in lines if l.startswith("toast=")]
    assert toasts and "לא כובה" in toasts[-1], lines
