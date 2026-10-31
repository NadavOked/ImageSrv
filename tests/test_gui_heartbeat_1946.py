"""#1946 -- a native GUI that is stopped (SIGSTOP) or wedged is found by its
heartbeat, killed and started again; one that beats is left alone.

QA-G19 (22/10, cloner 2): ``kill -STOP`` on the GUI mid-pull froze the screen on
24% for ~10 minutes and nothing noticed -- every "is the GUI alive" was
``kill -0``, which a stopped process answers. Now the GUI sets the mtime of
``$RUN_DIR/gui/beat`` after every frame and every turn of its event loop
(main.c, at least every 2 s), and both keepers -- ``gui_keep`` (guiparent.sh)
and the restore screen's writer (srgui.sh) -- kill a GUI whose beat is older
than ``GUI_BEAT_MAX_S``; the #1726 respawn then starts a new one.

Behaviour, through the real keepers with the stand-ins of
test_gui_keeper_1726.py (a GUI here is a shell loop that touches the beat), and
the compiled GUI itself on the lab.

**Negative control** (guiparent.sh, srgui.sh, ui.sh, main.c of 9001fc91;
Testrunner 22/10): all three fail -- no second GUI within the bound, the stopped
one stays; the compiled GUI writes no beat. Each "stopped" test first lets a
beating GUI run past the bound and requires it NOT to be restarted.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

import test_gui_keeper_1726 as keeper
from native import requires_native
from test_cloner_gui import _build_gui, _pkgconfig

#: GUI_BEAT_MAX_S here, and the bound for a stopped GUI to be replaced: the beat's
#: age is read in whole seconds (stat %Y), plus a keeper tick, plus the start.
BEAT_MAX_S = 2
REPLACED_WITHIN_S = BEAT_MAX_S + 6

#: test_gui_keeper_1726's bridge, whose GUI beats like main.c does.
BEATING_KIOSK = keeper.KIOSK.replace(
    "sleep 300 & s=$!",
    '( while :; do touch "$RUN_DIR/gui/beat"; sleep 0.2; done ) & s=$!')
#: ... and the restore screen's GUI, beating.
BEATING_SR_GUI = (
    "#!/bin/sh\n"
    "printf '%s\\n' \"$*\" >> \"$RUN_DIR/gui.calls\"\n"
    'echo "$$" >> "$RUN_DIR/stations"\n'
    'while :; do touch "$RUN_DIR/gui/beat"; sleep 0.2; done\n'
)


@pytest.fixture
def beating(monkeypatch):
    assert "sleep 300 & s=$!" in keeper.KIOSK   # the stand-in this file rewrites
    monkeypatch.setattr(keeper, "KIOSK", BEATING_KIOSK)
    monkeypatch.setattr(keeper, "SR_GUI", BEATING_SR_GUI)


def _stations(tmp_path: Path) -> list[str]:
    return keeper._lines(tmp_path / "run" / "stations")


def _stop_and_await_the_next(tmp_path: Path, proc: subprocess.Popen) -> tuple[int, int]:
    first = keeper._await(lambda: _stations(tmp_path)[:1], 20)
    assert first, "the GUI never started\n" + keeper._report(tmp_path)
    time.sleep(BEAT_MAX_S + 2)        # beating past the bound: not restarted for being old
    assert len(_stations(tmp_path)) == 1, "a GUI that beats was restarted\n" + keeper._report(tmp_path)
    pid = int(first[0])
    os.kill(pid, signal.SIGSTOP)      # QA-G19's injection
    second = keeper._await(lambda: _stations(tmp_path)[1:2], REPLACED_WITHIN_S)
    assert proc.poll() is None, "the agent's flow ended early\n" + keeper._report(tmp_path)
    assert second, (f"a stopped GUI was not replaced within {REPLACED_WITHIN_S} s -- "
                    "the screen stays frozen (#1946)\n" + keeper._report(tmp_path))
    gone = keeper._await(lambda: not keeper._alive(pid), 5)
    assert gone, f"the stopped GUI {pid} is still there next to the new one"
    return pid, int(second[0])


@keeper.POSIX
def test_a_stopped_gui_in_a_pull_is_killed_and_started_again(tmp_path, beating):
    proc = keeper._start(tmp_path, keeper.PULL, env=f"GUI_BEAT_MAX_S={BEAT_MAX_S}")
    try:
        first, second = _stop_and_await_the_next(tmp_path, proc)
        assert keeper._alive(second)
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert f"native GUI {first}: heartbeat older than {BEAT_MAX_S}s (stopped or wedged)" in log, \
            keeper._report(tmp_path)
        assert keeper._lines(tmp_path / "run" / "kiosk.runs") == ["run screen=cloner"] * 2
    finally:
        keeper._stop(proc)


@keeper.POSIX
def test_a_stopped_restore_screen_is_killed_and_started_again(tmp_path, beating):
    proc = keeper._start(tmp_path, 'sr_gui_open fresh; echo "OPEN rc=$?"; sleep 40',
                         env=f"GUI_BEAT_MAX_S={BEAT_MAX_S}")
    try:
        _first, second = _stop_and_await_the_next(tmp_path, proc)
        assert keeper._alive(second)
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert f"heartbeat older than {BEAT_MAX_S}s (stopped or wedged)" in log, keeper._report(tmp_path)
        assert "restore: the GUI died -- starting it again (1 of 3)" in log
        calls = keeper._lines(tmp_path / "run" / "gui.calls")
        assert len(calls) == 2 and calls[0] == calls[1], calls      # the same screen, the same state
    finally:
        keeper._stop(proc)


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native-gui is built on the lab only",
)
def test_the_compiled_gui_beats_on_a_still_screen_and_not_when_stopped(tmp_path):
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    state.write_text("cloner=Office 2024\ndrawer=1|sda|writing|45|100|\n", newline="\n")
    beat = tmp_path / "beat"
    env = dict(os.environ, IMAGESRV_GUI_BEAT=str(beat))
    fonts = keeper.REPO / "native-gui" / "fonts.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    with (tmp_path / "gui.err").open("wb") as err:
        proc = subprocess.Popen(
            [str(binary), "--backend", "mem", "--fb-file", str(tmp_path / "fb.mem"), "--no-input",
             "--screen", "cloner", "--state", str(state), "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
            stdout=subprocess.DEVNULL, stderr=err, stdin=subprocess.DEVNULL, env=env)
        try:
            assert keeper._await(beat.exists, 20), "no beat file -- " + (tmp_path / "gui.err").read_text()
            t0 = beat.stat().st_mtime_ns
            moved = keeper._await(lambda: beat.stat().st_mtime_ns > t0, 5)   # the loop wakes every <= 2 s
            assert moved, "the beat did not move on a still screen"
            proc.send_signal(signal.SIGSTOP)
            time.sleep(0.3)
            t1 = beat.stat().st_mtime_ns
            time.sleep(3)
            assert beat.stat().st_mtime_ns == t1, "a stopped GUI's beat moved -- it proves nothing"
            proc.send_signal(signal.SIGCONT)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
