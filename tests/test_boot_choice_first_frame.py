"""Metal, rc6, 18/10, cloner 2 (Nadav): "I turned the cloning machine on. By
the time I had a picture the 20 seconds were gone, and it went on from the
server."

The agent's journal on that boot::

    [15:12:56] local clone: boot choice on screen (20s to the server unless touched)
    native-gui: display 1920x1080

The agent said "on screen" and started its LC_BOOT_S count, and only after
that did the GUI open the display. What the fix promises, each one here:

1. The count starts with the boot choice's first frame on the display, not
   before: the GUI leaves ``$GUI_DIR/lcboot.shown`` (the second it presented
   that frame, main.c) and the agent waits for it. Until then ``$LC/boot``
   says "not started" (0), and the screen draws the whole count.
2. A GUI that never shows it does not hold the machine: after
   LC_SHOWN_WAIT_S the count starts without it, named in the journal -- an
   unattended cloner still goes on from the server (principle 1, #17).
3. The GUI writes the stamp only for the boot choice, once.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, _build_gui
from test_local_clone_agent import run
from test_shrink_gui import GUI_TOOLS

REPO = Path(__file__).resolve().parent.parent

pytestmark = requires_native(("bash", BASH))

#: two drawers, a cloner -- the boot choice is offered
CLONER = 'list_drawers() { echo sda; echo sdb; }; D_ROLE=cloner; lc_session() { echo SESSION; LC_END=; }; '
#: the GUI's stamp, where main.c writes it ($GUI_DIR = $RUN_DIR/gui)
STAMP = '"$RUN_DIR/gui/lcboot.shown"'


def gui_shows_after(seconds: float) -> str:
    """A GUI process that presents the boot choice `seconds` after the agent
    put it in $LC/screen -- the display that came up late on cloner 2."""
    return (f'( while [ "$(cat "$LC/screen" 2>/dev/null)" != boot ]; do sleep 0.1; done; sleep {seconds}; '
            f'mkdir -p "$RUN_DIR/gui"; date +%s > {STAMP}; exec sleep 300 ) & _gui_pid=$!; ')


def took(out: str) -> int:
    return int(next(l for l in out.splitlines() if l.startswith("took="))[len("took="):])


def first_count(into: str) -> str:
    """A background reader: the first $LC/boot that is not "0|0" (not counting),
    into $RUN_DIR/<into>; none within the ceiling = no file, read back as "".
    An empty read is not an answer (#2099): `echo ... > "$LC/boot"` truncates
    before it writes, and on a busy host the reader landed in between and took
    "" for the count. A ceiling of 15 s, so it never outlives the test (#1736)."""
    return ('( n=0; while [ $n -lt 150 ]; do b=$(cat "$LC/boot" 2>/dev/null); '
            f'case "$b" in "0|0"|"") n=$((n + 1)); sleep 0.1 ;; *) echo "$b" > "$RUN_DIR/{into}"; break ;; esac; done ) '
            '> /dev/null 2>&1 & ')


def test_the_count_starts_with_the_first_frame_not_before(tmp_path):
    """The display is up 4 s after the choice was offered; the count is 3 s.
    Before the fix the machine went on at 3 s -- before anyone saw the screen."""
    out = run(tmp_path, CLONER + gui_shows_after(4) + 'local_clone_arm; '
              '( sleep 2; cat "$LC/boot" > "$RUN_DIR/early" ) > /dev/null 2>&1 & ' + first_count("late") +
              't0=$(date +%s); local_clone_offer; echo "took=$(( $(date +%s) - t0 ))"; sleep 1; '
              f'echo "early=$(cat "$RUN_DIR/early")"; echo "late=$(cat "$RUN_DIR/late")"; echo "stamp=$(cat {STAMP})"; '
              'kill $_gui_pid', LC_BOOT_S="3")
    assert out.returncode == 0, out.stderr
    lines = dict(l.split("=", 1) for l in out.stdout.splitlines() if l.split("=", 1)[0] in ("early", "late", "stamp"))
    assert lines["early"] == "0|0", out.stdout          # offered, not on the screen yet: not counting
    assert lines["late"] == f"{int(lines['stamp']) + 3}|0", out.stdout   # the deadline is the frame's second + LC_BOOT_S
    assert took(out.stdout) >= 6, out.stdout            # 4 s to the frame, then the 3 s count
    assert "boot choice on screen (3s to the server unless touched)" in out.stdout, out.stdout
    assert "no touch for 3s -- from the server" in out.stdout and "SESSION" not in out.stdout, out.stdout


def test_a_gui_that_never_shows_it_does_not_hold_the_machine(tmp_path):
    """No stamp, and a stamp that is not a number (a half-written one): after
    LC_SHOWN_WAIT_S the count starts without the frame, aloud, and the machine
    goes on from the server -- never a wait for ever."""
    for name, write in (("none", ""), ("garbage", f'mkdir -p "$RUN_DIR/gui"; echo 17x > {STAMP}; ')):
        d = tmp_path / name; d.mkdir()
        t0 = time.monotonic()
        out = run(d, CLONER + 'sleep 300 & _gui_pid=$!; local_clone_arm; ' + write +
                  't0=$(date +%s); local_clone_offer; echo "took=$(( $(date +%s) - t0 ))"; kill $_gui_pid',
                  LC_BOOT_S="2", LC_SHOWN_WAIT_S="2")
        assert out.returncode == 0, (name, out.stderr)
        assert "the boot choice was not on the screen after 2s -- the 2s count starts without it" in out.stdout, (name, out.stdout)
        assert "boot choice on screen" not in out.stdout, (name, out.stdout)
        assert "no touch for 2s -- from the server" in out.stdout, (name, out.stdout)
        assert took(out.stdout) >= 3, (name, out.stdout)    # the wait, then the count
        assert time.monotonic() - t0 < 30


def test_a_frame_shown_before_the_offer_counts_from_the_offer(tmp_path):
    """The GUI drew the choice while the agent was still elsewhere (the stamp is
    older than the offer): the count runs its whole LC_BOOT_S from the offer --
    an old stamp does not end it at once."""
    out = run(tmp_path, CLONER + 'sleep 300 & _gui_pid=$!; local_clone_arm; '
              f'mkdir -p "$RUN_DIR/gui"; echo $(( $(date +%s) - 100 )) > {STAMP}; '
              + first_count("mid") +
              't0=$(date +%s); echo "t0=$t0"; local_clone_offer; sleep 1; echo "mid=$(cat "$RUN_DIR/mid")"; kill $_gui_pid',
              LC_BOOT_S="4")
    assert out.returncode == 0, out.stderr
    assert "boot choice on screen" in out.stdout and "no touch for 4s -- from the server" in out.stdout, out.stdout
    vals = dict(l.split("=", 1) for l in out.stdout.splitlines() if l.split("=", 1)[0] in ("t0", "mid"))
    assert vals["mid"].endswith("|0") and int(vals["mid"].split("|")[0]) >= int(vals["t0"]) + 4, out.stdout


def test_a_stamp_left_from_before_is_cleared_when_the_choice_is_armed(tmp_path):
    """The GUI writes the stamp once (only when it is not there); a stamp left
    in the run dir -- an agent started again in the same boot -- is not this
    choice's frame."""
    out = run(tmp_path, CLONER + f'mkdir -p "$RUN_DIR/gui"; date +%s > {STAMP}; local_clone_arm; '
              f'test -e {STAMP} && echo KEPT || echo CLEARED')
    assert out.returncode == 0, out.stderr
    assert "CLEARED" in out.stdout, out.stdout


# --- the GUI's side: the stamp after the frame is presented (main.c) --------------

def _gui_run(binary: Path, d: Path, state_text: str, seconds: float) -> None:
    """The real GUI on the mem backend, a fixed state file, for `seconds` after
    its first whole frame."""
    d.mkdir(parents=True, exist_ok=True)
    state = d / "state"
    state.write_text(state_text, newline="\n")
    fb = d / "fb.mem"
    fb.unlink(missing_ok=True)   # a whole frame of THIS run, not the last one
    env = dict(os.environ)
    if (REPO / "native-gui" / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(REPO / "native-gui" / "fonts.conf")
    with (d / "gui.err").open("wb") as errf:
        proc = subprocess.Popen(
            [str(binary), "--backend", "mem", "--fb-file", str(fb), "--no-input", "--screen", "cloner",
             "--state", str(state), "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
            stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL, env=env)
        try:
            ready = time.monotonic() + 20
            while time.monotonic() < ready and not (fb.exists() and fb.stat().st_size > 4096):
                time.sleep(0.1)
            time.sleep(seconds)
            assert proc.poll() is None, (d / "gui.err").read_text(errors="replace")
        finally:
            proc.terminate()
            proc.wait(timeout=10)


@GUI_TOOLS
def test_the_gui_stamps_the_first_frame_of_the_boot_choice_once(tmp_path):
    binary = _build_gui(tmp_path)
    now = int(time.time())
    boot = f"lc_screen=boot\nlc_boot=0|0|{now}\nlc_local=1\n"
    _gui_run(binary, tmp_path / "boot", boot, 1.5)
    stamp = tmp_path / "boot" / "lcboot.shown"
    assert stamp.exists(), (tmp_path / "boot" / "gui.err").read_text(errors="replace")
    first = stamp.read_text()
    assert first.endswith("\n") and now - 5 <= int(first) <= int(time.time()), first
    assert not (tmp_path / "boot" / "lcboot.shown.next").exists()
    # a GUI started again on the same choice: the first frame was already stamped
    time.sleep(1.1)
    _gui_run(binary, tmp_path / "boot", boot, 1.5)
    assert stamp.read_text() == first


@GUI_TOOLS
def test_another_screen_is_not_the_boot_choice(tmp_path):
    binary = _build_gui(tmp_path)
    _gui_run(binary, tmp_path / "standby", "task=none\n", 1.5)          # the cloner's standby screen
    assert not (tmp_path / "standby" / "lcboot.shown").exists()

