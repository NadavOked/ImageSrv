"""#1726: גואי שנהרג באמצע משיכה חוזר תוך 5 שניות — בכל מסלול.

ברזל, ‏rc5 ‏(18/10, מחשב שיכפול 2): באמצע משיכה ל-2 מגירות נשלח `kill -9`
ל-`imagesrv-station-gui`. אחרי 20 שניות לא היה גואי, ואחרי 37 המסך עוד
הראה את הפריים האחרון — 17%, קפוא — עד הכיבוי. ה-respawn של #1569 ישב
ב-`gui_parent`, והסוכן קרא לו רק בין hello ל-hello; כתיבה, משיכה, שיכפול
מקומי וספירת הסיום מחזיקים את המעטפת שלו דקות, ושם אף אחד לא שאל.

התיקון: לגואי יש בעלים משלו, ‏`gui_keep` (‏guiparent.sh), תהליך ברקע
שמסתכל עליו כל `GUI_KEEP_TICK_S`: גשר שנגמר — ביציאה, בקריסה, או כשתהליך
הגואי שלו נעלם (SIGKILL) והגשר תקוע בפעולה — מופעל שוב, עד
‏`GUI_RESPAWN_MAX`, ואז המסך הסטטי. מסך השחזור הבודד (‏srgui.sh) מקבל את
אותו שומר בכותב המצב שלו.

בקרה שלילית: על הבסיס (‏origin/integration/v0760-rc5, ‏0df3d90f) שלושת
טסטי ההחזרה נכשלים — אין קיוסק שני תוך 5 שניות (ראו גוף ה-PR).
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from agentlib import flat_lib

LIB = flat_lib()
#: the owner's bound for a GUI to be back (#1726)
BACK_WITHIN_S = 5.0

POSIX = requires_native(posix=True, why="chmod +x, #! scripts and kill -9")

#: The bridge as the real one behaves (guibridge.sh gui_main): it starts the GUI
#: process and its state writer, records both pids where the keeper reads them, and ends when the GUI's
#: pipe does -- or, with HELD=1, stays held in an action after its GUI is gone.
#: Its exit clears a pending SMART question, as gui_cleanup does.
KIOSK = r'''#!/bin/sh
echo "run screen=$GUI_SCREEN" >> "$RUN_DIR/kiosk.runs"
mkdir -p "$RUN_DIR/gui"
sleep 300 & s=$!
( while :; do sleep 1; done ) & w=$!
echo "$s $w" > "$RUN_DIR/gui/station.pid"
echo "$s" >> "$RUN_DIR/stations"
echo "$w" >> "$RUN_DIR/writers"
trap 'rm -f "$RUN_DIR/gui/smart-request" "$RUN_DIR/gui/smart-decision"' EXIT
if [ "${HELD:-0}" = 1 ]; then sleep 300; else wait "$s"; fi
'''

STATION = "#!/bin/sh\nexit 0\n"   # gui_parent's `--help` load check

#: #1561's progress GUI: records how it was started, then lives until killed.
SR_GUI = r'''#!/bin/sh
printf '%s\n' "$*" >> "$RUN_DIR/gui.calls"
echo "$$" >> "$RUN_DIR/stations"
exec sleep 300
'''

#: ... and one that ends by itself with 0, the way "כבה עכשיו" ends the real one.
SR_GUI_ENDS = r'''#!/bin/sh
printf '%s\n' "$*" >> "$RUN_DIR/gui.calls"
exit 0
'''


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _start(tmp_path: Path, body: str, env: str = "") -> subprocess.Popen:
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True, exist_ok=True)
    kiosk = _write(tmp_path / "kiosk", KIOSK)
    station = _write(tmp_path / "station", STATION)
    sr_gui = _write(tmp_path / "sr-gui", SR_GUI)
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC=aa:bb:cc:dd:ee:ff IP=10.0.0.9 "
        "SERVER=http://127.0.0.1:1 IMAGESRV_TEST=1 WAIT_HELPER_S=5 "
        f"GUI_KIOSK={shlex.quote(posix(kiosk))} GUI_STATION={shlex.quote(posix(station))} "
        f"SR_GUI_BIN={shlex.quote(posix(sr_gui))} SR_GUI_TICK_S=0.2 {env}; "
        f"SYSROOT={shlex.quote(posix(tmp_path / 'sysroot'))}; "
        f". {posix(LIB)}/common.sh; . {posix(LIB)}/jsonq.sh; . {posix(LIB)}/waits.sh; "
        f". {posix(LIB)}/ui.sh; . {posix(LIB)}/localboot.sh; . {posix(LIB)}/guiparent.sh; "
        f". {posix(LIB)}/splash.sh; . {posix(LIB)}/guibridge.sh; . {posix(LIB)}/clonergui.sh; "
        f". {posix(LIB)}/srgui.sh; "
        "attended_power_check() { :; }; disk_port() { echo 1; }; disk_serial() { echo S1; }; "
        + body
    )
    out = (tmp_path / "out.txt").open("w", encoding="utf-8")
    return subprocess.Popen([BASH, "-c", 'export PATH="/usr/bin:/bin:$PATH"; ' + script],
                            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            cwd=str(REPO), start_new_session=True)


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _await(fn, ceiling: float):
    """Polls fn() until it is truthy or the ceiling passes; returns the last value."""
    end = time.monotonic() + ceiling
    while True:
        got = fn()
        if got or time.monotonic() >= end:
            return got
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] != "Z"


def _report(tmp_path: Path) -> str:
    run = tmp_path / "run"
    return "\n".join(f"--- {n}\n" + (p.read_text(encoding="utf-8") if p.exists() else "(none)")
                     for n, p in (("out", tmp_path / "out.txt"), ("log", run / "agent.log"),
                                  ("kiosk.runs", run / "kiosk.runs"), ("gui.calls", run / "gui.calls")))


def _kill_and_await_the_next(tmp_path: Path, proc: subprocess.Popen) -> tuple[int, int, float]:
    """kill -9 the first GUI process, then the time until a second one runs."""
    stations = tmp_path / "run" / "stations"
    first = _await(lambda: _lines(stations)[:1], 20)
    assert first, "the GUI never started\n" + _report(tmp_path)
    os.kill(int(first[0]), signal.SIGKILL)
    t0 = time.monotonic()
    second = _await(lambda: _lines(stations)[1:2], BACK_WITHIN_S)
    took = time.monotonic() - t0
    assert proc.poll() is None, "the agent's pull ended early\n" + _report(tmp_path)
    assert second, (f"no GUI {BACK_WITHIN_S:.0f} s after kill -9 -- the screen stays frozen "
                    "on its last frame (#1726)\n" + _report(tmp_path))
    return int(first[0]), int(second[0]), took


#: The restore branch of the main loop (imagesrv-agent): the GUI is started,
#: then do_restore_drawers holds the agent's shell for the whole write. The
#: write here is a sleep in the foreground -- gui_parent is not called again.
PULL = 'D_ROLE=cloner; gui_restore_parent; echo "PARENT rc=$?"; sleep 30; echo PULL-DONE'


@POSIX
def test_a_gui_killed_in_a_pull_is_back_within_5_seconds(tmp_path):
    proc = _start(tmp_path, PULL)
    try:
        _first, second, took = _kill_and_await_the_next(tmp_path, proc)
        assert took <= BACK_WITHIN_S
        assert _alive(second), "the GUI started again did not stay up\n" + _report(tmp_path)
        # the screen of now: the same cloner screen, its state read from the files
        assert _lines(tmp_path / "run" / "kiosk.runs") == ["run screen=cloner"] * 2, _report(tmp_path)
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert "native GUI exited -- starting it again (1 of 3)" in log
        assert not (tmp_path / "run" / "splash.on").exists()
    finally:
        _stop(proc)


@POSIX
def test_a_bridge_held_after_its_gui_is_killed_is_stopped_and_started_again(tmp_path):
    """SIGKILL detection, not only an exit code: the GUI process is gone while
    its bridge is held in an action (a curl, a tool) and never ends by itself."""
    proc = _start(tmp_path, PULL, env="HELD=1")
    try:
        _first, second, _took = _kill_and_await_the_next(tmp_path, proc)
        assert _alive(second)
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert "is gone and its bridge is held -- stopping the bridge" in log, _report(tmp_path)
        # the held bridge's state writer went with it: one writer of the state, not two
        writers = [int(w) for w in _lines(tmp_path / "run" / "writers")]
        assert len(writers) == 2 and not _alive(writers[0]) and _alive(writers[1]), writers
    finally:
        _stop(proc)


@POSIX
def test_the_restore_screen_killed_mid_write_is_back_within_5_seconds(tmp_path):
    """#1561's progress GUI ("שחזור לדיסק הזה"), the same bound."""
    proc = _start(tmp_path, 'sr_gui_open fresh; echo "OPEN rc=$?"; sleep 30')
    try:
        _first, second, _took = _kill_and_await_the_next(tmp_path, proc)
        assert _alive(second)
        calls = _lines(tmp_path / "run" / "gui.calls")
        assert len(calls) == 2 and calls[0] == calls[1], calls       # the same screen, the same state file
        assert calls[1].startswith("--screen progress") and "/gui/restore.state" in calls[1]
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert "restore: the GUI died -- starting it again (1 of 3)" in log
    finally:
        _stop(proc)


@POSIX
def test_a_restore_screen_that_ends_by_itself_is_not_started_again(tmp_path):
    """0 is the GUI's own end ("כבה עכשיו"): the keeper leaves with it."""
    _write(tmp_path / "sr-gui-ends", SR_GUI_ENDS)
    proc = _start(tmp_path, f'SR_GUI_BIN={shlex.quote(posix(tmp_path / "sr-gui-ends"))}; '
                  'sr_gui_open fresh; wait "$SR_GUI_PID"; echo "KEEPER rc=$?"')
    try:
        proc.wait(timeout=60)
    finally:
        _stop(proc)
    out = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "KEEPER rc=0" in out, _report(tmp_path)
    assert len(_lines(tmp_path / "run" / "gui.calls")) == 1, _report(tmp_path)


@POSIX
def test_a_gui_killed_during_a_smart_question_ends_the_question_at_once(tmp_path):
    """The dying bridge clears the question (gui_cleanup). With the keeper alive
    the wait must not run on to WAIT_SMART_GUI_S: no answer is the colour's own
    default (smart_ask), as before #1726."""
    body = ('D_ROLE=cloner; gui_cloner_parent; GUI_DIR="$RUN_DIR/gui"; '
            'mkdir -p "$SYSROOT/sys/block/sda/device"; '
            'until [ -s "$RUN_DIR/stations" ]; do sleep 0.1; done; '
            # killed once the question is up -- before it, the new GUI would just be asked it
            '( n=0; until [ -e "$GUI_DIR/smart-request" ] || [ "$n" -ge 100 ]; do sleep 0.1; n=$((n + 1)); done; '
            'kill -9 "$(head -n1 "$RUN_DIR/stations")" ) & '
            't0=$(date +%s); gui_smart_choice sda warn reason; echo "SMART rc=$? after $(( $(date +%s) - t0 ))s"')
    proc = _start(tmp_path, body, env="WAIT_SMART_GUI_S=30")
    try:
        proc.wait(timeout=60)
    finally:
        _stop(proc)
    out = (tmp_path / "out.txt").read_text(encoding="utf-8")
    line = [ln for ln in out.splitlines() if ln.startswith("SMART rc=")]
    assert line and line[0].startswith("SMART rc=1 after"), _report(tmp_path)
    assert int(line[0].rsplit(" ", 1)[1].rstrip("s")) < 15, line   # not the 30 s of no answer


@POSIX
def test_the_handoff_ends_the_keeper_without_a_restart(tmp_path):
    """restore-start is the bridge's planned exit -- not a crash to undo."""
    kiosk = _write(tmp_path / "kiosk-handoff",
                   '#!/bin/sh\necho run >> "$RUN_DIR/kiosk.runs"\n'
                   'printf "restore-start\\nimg_1\\n" > "$RUN_DIR/gui/handoff"\nexit 0\n')
    proc = _start(tmp_path, f'GUI_KIOSK={shlex.quote(posix(kiosk))}; D_ROLE=build; '
                  'gui_parent; echo "PARENT rc=$?"; wait "$_gui_pid"; echo "KEEPER rc=$?"')
    try:
        proc.wait(timeout=60)
    finally:
        _stop(proc)
    out = (tmp_path / "out.txt").read_text(encoding="utf-8")
    assert "PARENT rc=0" in out and "KEEPER rc=0" in out, _report(tmp_path)
    assert _lines(tmp_path / "run" / "kiosk.runs") == ["run"]
    assert not (tmp_path / "run" / "splash.on").exists()


def test_the_bridge_records_its_gui_process_for_the_keeper():
    """The keeper reads $GUI_DIR/station.pid; the bridge writes it as it starts the GUI."""
    bridge = (LIB / "guibridge.sh").read_text(encoding="utf-8")
    start = bridge.index("/usr/bin/imagesrv-station-gui")
    assert 'GUI_PID=$!; echo "$GUI_PID $GUI_STATE_PID" > "$GUI_DIR/station.pid"' in bridge[start:start + 400]
    assert bridge.index("GUI_STATE_PID=$!") < start          # the writer is started first, its pid is known
    keeper = (LIB / "guiparent.sh").read_text(encoding="utf-8")
    assert '"$RUN_DIR/gui/station.pid"' in keeper
