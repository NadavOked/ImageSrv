"""#1918 -- the power button (Nadav, 20/10, the USB stick on an HP 8100): a
short press did nothing, and the only way off was the long press -- a forced
power-off with no sync. Linux has the ACPI event (button.ko -> the "Power
Button" input device, KEY_POWER); nobody listened.

Nadav's decision (20/10):

1. nothing writing (a choice screen, a result, a failure, a wait): a short
   press powers off by the road of "כבה את המחשב" -- safe_poweroff;
2. in the middle of a write (local clone, restore, capture, shrink): the
   screen asks "יש שיכפול פעיל — לעצור ולכבות?", red button and "המשך". The
   write goes on until an answer; "עצור וכבה" stops it by the stop that
   already exists (the local clone's, the round's), then powers off.

The GUI turns KEY_POWER into $RUN_DIR/gui/power.press (native-gui main.c) and
the question's answer into power.answer; the agent decides. These tests start
the agent's helpers the way imagesrv-agent's main loop does -- its own lib
list and its own start lines, read from the file -- so "nobody listens" is
what they see when the listener is not there: the press stays unanswered.
The GUI half (input.c reading a power-button device) is the lab test at the
bottom: gcc and /dev/uinput as root.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import evdev_lab
import hygiene
from native import requires_native
from test_cloner_gui import BASH, posix
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent" / "imagesrv-agent"
LIB = flat_lib()
BASH_ONLY = requires_native(("bash", BASH))

OFF = "power button: powering off with wake-on-LAN armed"   # safe_poweroff's own line, real


def lib_names() -> list[str]:
    names = re.findall(r'\. "\$LIB_DIR/([a-z0-9_]+)\.sh"', AGENT.read_text(encoding="utf-8"))
    assert len(names) > 50, names        # the parse found the agent's load chain, not nothing
    return names


def main_loop_starts() -> str:
    """The main loop's lines that start the agent's helpers once the role is
    known: from watchdog_start/monitor_start to the decision."""
    lines = AGENT.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("watchdog_start; monitor_start"))
    end = next(i for i, l in enumerate(lines[start:], start) if l.strip().startswith('case "$(decide)"'))
    return "\n".join(lines[start:end])


def run(tmp_path: Path, setup: str, during: str, *, role: str = "cloner", wait: float = 1.5,
        timeout: int = 90, **env) -> tuple[str, Path]:
    """The agent with its helpers started as in the main loop; the GUI's
    press; `during` (answers, a write that ends); then the journal. stdout =
    the journal, then '---STATE', then gui_hello_state (what every screen
    reads)."""
    run_dir = tmp_path / "run"; run_dir.mkdir(parents=True, exist_ok=True)
    pre = (f'export RUN_DIR={posix(run_dir)!r} LIB_DIR={posix(LIB)!r} IMAGESRV_TEST=1 '
           f'MAC=aa:bb:cc:dd:ee:ff POWER_TICK_S=0.1 D_ROLE={role}; ')
    pre += "".join(f'export {k}={v!r}; ' for k, v in env.items())
    pre += "".join(f'. "$LIB_DIR/{n}.sh"; ' for n in lib_names())
    script = (
        pre +
        'export LOG_FILE="$RUN_DIR/agent.log"; mkdir -p "$RUN_DIR/gui" "$RUN_DIR/targets" "$LC"; '
        'watchdog_start() { :; }; monitor_start() { :; }; arm_wol() { return 0; }; sync() { :; }; '
        + setup + '\n'
        + main_loop_starts() + '\n'
        + f': > "$RUN_DIR/gui/power.press"; sleep {wait}\n'      # the GUI: KEY_POWER
        + during + '\n'
        'kill ${_pwr_pid:-} 2>/dev/null; sleep 0.2\n'
        'cat "$LOG_FILE" 2>/dev/null; echo "---STATE"; gui_hello_state\n'
    )
    done = hygiene.run_group([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL, cwd=str(REPO))
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout, run_dir


def ask_of(run_dir: Path) -> str:
    f = run_dir / "power.ask"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


# --- 1. nothing writing: off, the way "כבה את המחשב" goes ---------------------------

@BASH_ONLY
@pytest.mark.parametrize("role,setup", [
    ("cloner", ""),                                                       # the boot choice
    ("cloner", 'echo idle > "$LC/phase"'),                                # the local clone's choice
    ("cloner", 'echo done > "$LC/phase"; mkdir -p "$RUN_DIR/targets/sdb"; echo done > "$RUN_DIR/targets/sdb/state"'),
    ("cloner", 'mkdir -p "$RUN_DIR/targets/sdb"; echo failed > "$RUN_DIR/targets/sdb/state"; echo failed > "$RUN_DIR/state"'),
    ("build", 'echo done > "$RUN_DIR/state"'),                            # the build menu after a capture
], ids=["boot-choice", "lc-choice", "lc-result", "round-failed", "build-menu"])
def test_a_short_press_with_nothing_writing_powers_off(tmp_path, role, setup):
    out, run_dir = run(tmp_path, setup, "", role=role)
    assert OFF in out, out
    assert "pressed, nothing is writing" in out, out
    assert not ask_of(run_dir), "a question with nothing writing"


# --- 2. in the middle of a write: a question, and the write goes on ------------------

WRITES = [
    ("cloner", 'echo copying > "$LC/phase"', "localclone|1"),
    ("cloner", 'echo shrinking > "$LC/phase"', "localclone|1"),
    ("cloner", 'echo verifying > "$LC/phase"', "localclone|0"),           # the read-back has no stop
    ("cloner", 'mkdir -p "$RUN_DIR/targets/sdb"; echo writing > "$RUN_DIR/targets/sdb/state"', "round|1"),
    ("build", 'printf "sda\\nשחזור\\nדיסק 1\\n3\\nwin11\\n" > "$RUN_DIR/gui/restore.head"; '
              'mkdir -p "$RUN_DIR/targets/sda"; echo writing > "$RUN_DIR/targets/sda/state"', "restore|1"),
    ("classroom", 'mkdir -p "$RUN_DIR/targets/sda"; echo writing > "$RUN_DIR/targets/sda/state"', "restore|0"),
    ("build", 'echo capturing > "$RUN_DIR/state"', "capture|0"),
]


@BASH_ONLY
@pytest.mark.parametrize("role,setup,kind", WRITES,
                         ids=["lc-copy", "lc-shrink", "lc-verify", "round", "restore", "class-restore", "capture"])
def test_a_press_during_a_write_asks_and_the_write_goes_on(tmp_path, role, setup, kind):
    out, run_dir = run(tmp_path, setup, "sleep 1", role=role)
    log, state = out.split("---STATE", 1)
    assert OFF not in log, log                                 # never off over a write
    assert ask_of(run_dir) == f"1|{kind}", (ask_of(run_dir), log)
    assert f"power_ask=1|{kind}" in state, state               # every screen draws it
    # the write goes on: no stop was asked of anything
    assert not (tmp_path / "run" / "lc" / "stop.req").exists()
    assert not list((run_dir / "targets").glob("*/cancel"))
    assert not (run_dir / "power.stop").exists()


@BASH_ONLY
def test_continue_keeps_the_write_and_takes_the_question_away(tmp_path):
    out, run_dir = run(tmp_path, 'echo copying > "$LC/phase"',
                       'printf "1|keep\\n" > "$RUN_DIR/gui/power.answer"; sleep 1')
    assert OFF not in out, out
    assert not ask_of(run_dir), out
    assert not (run_dir / "lc" / "stop.req").exists()
    assert "power button: continue" in out, out


@BASH_ONLY
def test_an_answer_to_another_question_is_dropped(tmp_path):
    out, run_dir = run(tmp_path, 'echo copying > "$LC/phase"',
                       'printf "7|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1')
    assert OFF not in out, out
    assert ask_of(run_dir) == "1|localclone|1", out           # still asked
    assert not (run_dir / "lc" / "stop.req").exists()
    assert "stale answer" in out, out


# --- 3. "עצור וכבה": the stop that exists, then off ------------------------------------

@BASH_ONLY
def test_stop_and_power_off_stops_the_local_clone_by_its_own_stop_then_powers_off(tmp_path):
    during = ('printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1; '
              'cp "$LC/stop.req" "$RUN_DIR/stop.seen" 2>/dev/null; '
              'grep -c "powering off" "$LOG_FILE" > "$RUN_DIR/off.before" || :; '
              'echo stopped > "$LC/phase"; sleep 1')                    # lc_run's end: phase stopped
    out, run_dir = run(tmp_path, 'echo copying > "$LC/phase"', during)
    assert (run_dir / "stop.seen").is_file(), out                         # the local clone was asked to stop
    assert (run_dir / "stop.seen").read_text().strip() == "stop", out     # lcgui.sh's lc-stop request
    assert (run_dir / "off.before").read_text().strip() == "0", out       # not while it still copied
    assert OFF in out, out                                                # then off
    assert "the localclone stopped" in out, out


@BASH_ONLY
def test_stop_and_power_off_cancels_every_writing_drawer_and_takes_the_rounds_end(tmp_path):
    setup = ('for d in sdb sdc; do mkdir -p "$RUN_DIR/targets/$d"; echo writing > "$RUN_DIR/targets/$d/state"; done; '
             'mkdir -p "$RUN_DIR/targets/sdd"; echo done > "$RUN_DIR/targets/sdd/state"')
    during = ('printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1; '
              'grep -c "powering off" "$LOG_FILE" > "$RUN_DIR/off.before" || :; '
              # cancel_drawer's end (verdict.sh): the table wiped, then "cancelled"
              'for d in sdb sdc; do echo cancelled > "$RUN_DIR/targets/$d/state"; done; '
              'echo "$(( $(date +%s) + 20 ))|partial|1|3|0" > "$RUN_DIR/end.poweroff"; sleep 1')
    out, run_dir = run(tmp_path, setup, during)
    for d in ("sdb", "sdc"):
        assert (run_dir / "targets" / d / "cancel").read_text(encoding="utf-8").strip() == "מכפתור ההפעלה של המחשב", out
    assert not (run_dir / "targets" / "sdd" / "cancel").exists()          # a finished drawer is left alone
    assert (run_dir / "off.before").read_text().strip() == "0", out
    assert (run_dir / "end.now").exists(), out                            # the round's own "כבה עכשיו"
    assert OFF not in out, out                                            # its end powers off, after its report


@BASH_ONLY
def test_a_write_with_no_stop_of_its_own_is_not_stopped(tmp_path):
    out, run_dir = run(tmp_path, 'echo capturing > "$RUN_DIR/state"',
                       'printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1', role="build")
    assert OFF not in out, out
    assert not (run_dir / "power.stop").exists(), out
    assert "has no stop -- refused" in out, out


#: the build machine's own restore, writing (srgui.sh's files; the stream faked)
RESTORE = ('printf "sda\\nשחזור\\nדיסק 1\\n3\\nwin11\\n" > "$RUN_DIR/gui/restore.head"; '
           'mkdir -p "$RUN_DIR/targets/sda"; echo writing > "$RUN_DIR/targets/sda/state"; '
           'sr_stop_stream() { echo "$1" >> "$RUN_DIR/stream.stopped"; }; ')


@BASH_ONLY
def test_the_restore_is_stopped_by_its_cancel_and_not_powered_off_before_the_wipe(tmp_path):
    """run_restore marks the target failed when its stream dies -- before
    sr_cancelled wipes the table. "Nothing writes" is no evidence there: the
    restore's own end powers off (srgui.sh), never the listener."""
    setup = RESTORE + 'echo 111-sda > "$RUN_DIR/gui/restore.id"'
    during = ('printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1; '
              ': > "$RUN_DIR/gui/restore.out"; sr_cancel_watch img1 sda & _cw=$!; sleep 1.5; kill $_cw; '
              'echo failed > "$RUN_DIR/targets/sda/state"; echo failed > "$RUN_DIR/state"; sleep 1')
    out, run_dir = run(tmp_path, setup, during, role="build")
    assert (run_dir / "power.stop").is_file(), out                        # "עצור וכבה" was taken
    assert (run_dir / "power.stop").read_text().split("|")[1].strip() == "restore", out
    assert (run_dir / "gui" / "restore.cancel").exists(), out             # "ביטול השחזור"'s own path
    assert (run_dir / "stream.stopped").read_text().strip().startswith("img1"), out
    assert OFF not in out, out                                            # the wipe comes first
    assert (run_dir / "power.stop").read_text().split("|")[2].strip() == "111-sda", out   # it names its restore


@BASH_ONLY
def test_a_stop_left_from_an_earlier_restore_does_not_stop_the_next_one(tmp_path):
    """Review of #1918: a power.stop from a restore before (its power-off did
    not take, the machine went on) must not cancel and power off the next
    restore of the same boot. The stop names its restore; another's is dropped."""
    setup = RESTORE + 'echo 222-sda > "$RUN_DIR/gui/restore.id"; : > "$RUN_DIR/gui/restore.out"'
    during = ('sr_cancel_watch img2 sda & _cw=$!; sleep 0.3; '            # the new restore's watcher, running
              'echo "$(date +%s)|restore|111-sda" > "$RUN_DIR/power.stop"; '   # the earlier restore's stop
              'sleep 2; kill $_cw 2>/dev/null; sleep 0.3')
    out, run_dir = run(tmp_path, setup, during, role="build", wait=0.5)
    assert not (run_dir / "gui" / "restore.cancel").exists(), out          # the new restore goes on
    assert not (run_dir / "stream.stopped").exists(), out
    assert not (run_dir / "power.stop").exists(), out                       # the stale stop is gone
    assert "a restore that is over (111-sda)" in out, out


#: a capture ordered from the GUI by a signed-in admin: the hello's task, the
#: GUI's session (jq is faked -- jsonq.sh is the one place that touches it)
ADMIN_CAPTURE = (
    'echo capturing > "$RUN_DIR/state"; : > "$RUN_DIR/gui/console.jar"; echo x > "$RUN_DIR/gui/console.jar"; '
    'json_get() { case "$1:$2" in *response.json:.task.type) echo capture ;; *response.json:.task.id) echo tsk_ab12 ;; '
    '  *console_login.json:.role) echo admin ;; *) echo null ;; esac; }; '
)


@BASH_ONLY
def test_a_capture_is_stopped_by_the_consoles_own_cancel_then_powers_off(tmp_path):
    """Nadav 20/10: "עצור וכבה" in a capture = "ביטול הקליטה" (POST
    /api/console/tasks/<id>/cancel, the GUI's admin session). The server ends
    the upload, the capture fails and puts the source back, then off."""
    setup = ADMIN_CAPTURE + ('console_post() { echo "$*" >> "$RUN_DIR/posted"; '
                             'cp "$CONSOLE_JAR" "$RUN_DIR/jar.used"; printf 200; }')
    during = ('printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1; '
              'grep -c "powering off" "$LOG_FILE" > "$RUN_DIR/off.before" || :; '
              'echo failed > "$RUN_DIR/state"; sleep 1')                 # _capture_failed: source back, then failed
    out, run_dir = run(tmp_path, setup, during, role="build")
    assert (run_dir / "posted").is_file(), out
    assert (run_dir / "posted").read_text().split()[0] == "tasks/tsk_ab12/cancel", out
    assert (run_dir / "jar.used").is_file(), out                         # the GUI's own session
    assert (run_dir / "off.before").read_text().strip() == "0", out      # not while it still captured
    assert OFF in out, out


@BASH_ONLY
def test_a_capture_the_server_did_not_cancel_goes_on_and_says_so(tmp_path):
    setup = ADMIN_CAPTURE + 'console_post() { printf 409; }'
    out, run_dir = run(tmp_path, setup, 'printf "1|stop\\n" > "$RUN_DIR/gui/power.answer"; sleep 1', role="build")
    log, state = out.split("---STATE", 1)
    assert OFF not in log, log
    assert not (run_dir / "power.stop").exists(), log
    assert any(l.startswith("toast=") and "409" in l for l in state.splitlines()), state


@BASH_ONLY
def test_the_question_offers_the_stop_only_with_an_admin_session(tmp_path):
    out, run_dir = run(tmp_path, ADMIN_CAPTURE, "sleep 1", role="build")
    assert ask_of(run_dir) == "1|capture|1", out
    out2, run_dir2 = run(tmp_path / "no-session", ADMIN_CAPTURE + 'rm -f "$RUN_DIR/gui/console.jar"', "sleep 1", role="build")
    assert ask_of(run_dir2) == "1|capture|0", out2


# --- 4. a power-off that does not take is said ----------------------------------------

@BASH_ONLY
def test_a_refused_power_off_is_said_on_every_screen(tmp_path):
    out, run_dir = run(tmp_path, 'arm_wol() { return 1; }; send_hello() { :; }', "")
    log, state = out.split("---STATE", 1)
    assert "the machine stayed on" in log, log
    toasts = [l for l in state.splitlines() if l.startswith("toast=")]
    assert toasts and "לא כובה" in toasts[0], state


# --- 5. the GUI: input.c hears the power button (lab: gcc + /dev/uinput) ---------------

INPUT_C = REPO / "native-gui" / "src" / "input.c"
LAB = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("/dev/uinput (root)", evdev_lab.uinput_available()),
    posix=True,
    why="input.c נבנה ומורץ מול uinput במעבדה בלבד",
)
KEY_POWER = 116

DRIVER = r'''
#include "%(input_c)s"
#include <stdio.h>
#include <stdlib.h>
/* argv: MS PATH -- probe PATH as input_open would, pump for MS ms, print
 * every key event as "KEY <n>". "ready <delete>" first: KEYSYM_DELETE's
 * value, so the test knows what the next keysym is without naming it. */
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    Input *in = calloc(1, sizeof *in);
    in->w = 64; in->h = 32;
    if (probe(in, argv[2]) != 0) { printf("rejected\n"); fflush(stdout); return 3; }
    printf("ready %%d\n", (int)KEYSYM_DELETE); fflush(stdout);
    struct pollfd fds[MAX_DEV]; int ms = atoi(argv[1]);
    for (int t = 0; t < ms; t += 50) {
        int n = input_fill_pollfds(in, fds, MAX_DEV);
        if (poll(fds, n, 50) > 0) input_pump(in, fds, n);
        Event e;
        while (input_next(in, &e)) if (e.type == UIEV_KEY) printf("KEY %%d\n", e.key);
    }
    fflush(stdout); input_close(in); return 0;
}
'''


@pytest.mark.uinput
@LAB
def test_the_gui_hears_a_power_button_device_once_per_press(tmp_path):
    """An ACPI "Power Button" declares KEY_POWER and nothing else: before
    #1918 input.c took it for no device at all and never opened it."""
    src = tmp_path / "drv.c"; src.write_text(DRIVER % {"input_c": INPUT_C.as_posix()})
    binary = tmp_path / "drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run([cc, "-O2", "-Wall", "-std=c11", "-D_GNU_SOURCE", "-o", str(binary), str(src)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    EV_KEY = evdev_lab.EV_KEY
    with evdev_lab.UinputDevice("ic1918-power-button", keys=(KEY_POWER,)) as dev:
        out_path = tmp_path / "drv.out"
        with open(out_path, "wb") as out:
            proc = subprocess.Popen([str(binary), "1500", str(dev.node)], stdout=out,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while b"ready" not in out_path.read_bytes():
                assert proc.poll() is None, f"driver exited rc={proc.returncode}: {out_path.read_text()}"
                assert time.monotonic() < deadline, out_path.read_text()
                time.sleep(0.02)
            dev.emit((EV_KEY, KEY_POWER, 1)); time.sleep(0.05)
            dev.emit((EV_KEY, KEY_POWER, 0)); time.sleep(0.05)      # the release is not a press
            dev.emit((EV_KEY, KEY_POWER, 1)); time.sleep(0.05)
            dev.emit((EV_KEY, KEY_POWER, 2)); time.sleep(0.05)      # nor an autorepeat
            assert proc.wait(timeout=10) == 0
    lines = out_path.read_text().splitlines()
    delete = int(lines[0].split()[1])
    assert lines[1:] == [f"KEY {delete + 1}", f"KEY {delete + 1}"], lines   # KEYSYM_POWER, twice
