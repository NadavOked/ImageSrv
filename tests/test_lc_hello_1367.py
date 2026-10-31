"""#1367 -- a cloner with three drawers stopped saying hello before a round
opened and stayed silent for an hour: it did not join the wave, and the
console's power-off (#65/#980, delivered in the hello answer) never reached it.

What the fix promises, each one tested here:

1. While the local clone screens wait for a person -- the paused boot choice,
   the local screen, its result -- the machine keeps saying hello (the
   attended beat, as in the SMART questions), and what the beat brings back
   is not thrown away: a power request is carried out, an open round is put
   on the screen.
2. Those waits have a ceiling: no touch for LC_QUIET_S and the machine goes
   on from the server, aloud in the log.
3. smartctl has a ceiling (it runs inside build_hello, through the probe, and
   before the gate): a drive that does not answer is "not checked", named --
   never a freeze.
4. The screen says that a round opened (native: lc_round_note).

Every agent test has a fallback that ends the old code's endless wait after
~20 s, so the negative control fails on an assertion, not on a timeout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix
from test_local_clone_agent import BOOT, LIB, run

pytestmark = requires_native(("bash", BASH))

#: the beat writes what it would have sent; nothing touches the network
BEATS = 'attended_hello() { echo "$1" >> "$RUN_DIR/beats"; }; '
#: power_check's real end is poweroff -f
POWER = 'power_check() { echo "POWER $(json_get "$RESP" .power_action)"; attended_stop; kill $_gui_pid; exit 0; }; '


def _beats(out: str) -> tuple[int, int]:
    line = next(l for l in out.splitlines() if l.startswith("beats="))
    a, b = line[len("beats="):].split(" after=")
    return int(a or 0), int(b or 0)


# --- 1. the beat --------------------------------------------------------------------

def test_the_paused_choice_keeps_saying_hello_and_stops_when_it_ends(tmp_path):
    out = run(tmp_path, BOOT + BEATS + 'local_clone_arm; '
              '( sleep 1; echo pause > "$LC/boot.decision"; sleep 5; echo server > "$LC/boot.decision" ) > /dev/null 2>&1 & '
              'local_clone_offer; n=$(cat "$RUN_DIR/beats" 2>/dev/null | wc -l); sleep 3; '
              'echo "beats=$n after=$(cat "$RUN_DIR/beats" 2>/dev/null | wc -l)"; '
              'sort -u "$RUN_DIR/beats" 2>/dev/null; kill $_gui_pid',
              LC_BOOT_S="3", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    n, after = _beats(out.stdout)
    assert n >= 3, out.stdout                      # ~6 s on the choice, a beat a second
    assert after == n, out.stdout                  # the beat ended with the screen
    assert "Local clone screen: from the server, or a local clone" in out.stdout


def test_the_local_screen_says_hello_with_its_own_question(tmp_path):
    out = run(tmp_path, BOOT + BEATS + 'lc_inventory() { :; }; local_clone_arm; '
              '( sleep 1; echo local > "$LC/boot.decision"; sleep 4; echo back > "$LC/end.req"; '
              '  sleep 2; echo server > "$LC/boot.decision" ) > /dev/null 2>&1 & '
              'local_clone_offer; sort -u "$RUN_DIR/beats" 2>/dev/null; kill $_gui_pid',
              LC_BOOT_S="20", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    assert "Local clone at the machine -- not from the server" in out.stdout, out.stdout


# --- 2. the ceiling -----------------------------------------------------------------

FALLBACK = '( sleep 20; echo back > "$LC/end.req"; sleep 2; echo server > "$LC/boot.decision" ) > /dev/null 2>&1 & '


def test_a_paused_choice_nobody_makes_goes_on_from_the_server(tmp_path):
    t0 = time.monotonic()
    out = run(tmp_path, BOOT + BEATS + 'local_clone_arm; ( sleep 1; echo pause > "$LC/boot.decision" ) > /dev/null 2>&1 & '
              + FALLBACK + 'local_clone_offer; kill $_gui_pid',
              LC_BOOT_S="3", LC_QUIET_S="2", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    assert "no touch for 2s on the paused choice -- from the server" in out.stdout, out.stdout
    assert "going on from the server" in out.stdout
    assert time.monotonic() - t0 < 15


def test_a_touch_on_the_screen_holds_the_paused_choice(tmp_path):
    """The kiosk's idle.touch (a key or click) restarts the count."""
    out = run(tmp_path, BOOT + BEATS + 'local_clone_arm; t0=$(date +%s); '
              '( sleep 1; echo pause > "$LC/boot.decision"; for i in 1 2 3 4 5 6; do sleep 1; touch "$RUN_DIR/idle.touch"; done ) > /dev/null 2>&1 & '
              + FALLBACK + 'local_clone_offer; echo "dt=$(( $(date +%s) - t0 ))"; kill $_gui_pid',
              LC_BOOT_S="3", LC_QUIET_S="3", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    assert "no touch for 3s on the paused choice" in out.stdout, out.stdout
    dt = int(next(l for l in out.stdout.splitlines() if l.startswith("dt="))[3:])
    assert 9 <= dt < 15, out.stdout                # 1 + 6 s of touches + 3 s of quiet


def test_the_local_screen_left_alone_goes_on_from_the_server(tmp_path):
    out = run(tmp_path, BOOT + BEATS + 'lc_inventory() { :; }; local_clone_arm; '
              '( sleep 1; echo local > "$LC/boot.decision" ) > /dev/null 2>&1 & '
              + FALLBACK + 'local_clone_offer; kill $_gui_pid',
              LC_BOOT_S="20", LC_QUIET_S="2", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    assert "no touch for 2s on the local clone screen -- from the server" in out.stdout, out.stdout
    assert out.stdout.count("boot choice offered") == 1   # on to the server, not back to the choice
    assert "going on from the server" in out.stdout


# --- 1b. what the beat brings back ----------------------------------------------------

def test_a_power_request_on_the_choice_is_carried_out(tmp_path):
    out = run(tmp_path, BOOT + BEATS + POWER + 'local_clone_arm; '
              '( sleep 1; echo pause > "$LC/boot.decision"; sleep 1; '
              '  echo \'{"power_action":"poweroff"}\' > "$RUN_DIR/power.resp" ) > /dev/null 2>&1 & '
              + FALLBACK + 'local_clone_offer; echo NOT-POWERED; kill $_gui_pid',
              LC_BOOT_S="3", ATTENDED_BEAT_S="1")
    assert "POWER poweroff" in out.stdout and "NOT-POWERED" not in out.stdout, out.stdout + out.stderr
    assert "the console asked to poweroff" in out.stdout


def test_a_power_request_that_came_during_the_copy_is_dropped_aloud(tmp_path):
    out = run(tmp_path, BOOT + BEATS + POWER + 'lc_inventory() { :; }; '
              'lc_run() { echo \'{"power_action":"poweroff"}\' > "$RUN_DIR/power.resp"; echo done > "$LC/phase"; }; '
              'local_clone_arm; ( sleep 1; echo local > "$LC/boot.decision"; sleep 2; echo "sda|sdb|0" > "$LC/start.req"; '
              '  sleep 3; echo back > "$LC/end.req"; sleep 2; echo server > "$LC/boot.decision" ) > /dev/null 2>&1 & '
              'local_clone_offer; echo STAYED-ON; kill $_gui_pid',
              LC_BOOT_S="20", ATTENDED_BEAT_S="1")
    assert out.returncode == 0, out.stderr
    assert "a power request came during the copy, not confirmed -- not carried out" in out.stdout, out.stdout
    assert "POWER" not in out.stdout and "STAYED-ON" in out.stdout


@pytest.mark.parametrize("answer,power,round_", [
    ('{"known":true,"power_action":"poweroff","session":{"state":"open"}}', True, "open"),
    ('{"known":true,"session":null}', False, "none"),
    ('not json', False, ""),                       # unreadable is not "no round"
])
def test_the_beat_keeps_what_its_answer_hands_out_once(tmp_path, answer, power, round_):
    (tmp_path / "answer.json").write_text(answer, encoding="utf-8")
    out = run(tmp_path, 'build_hello() { echo \'{"schema":2}\'; }; '
              f'http_post_json() {{ cat {posix(tmp_path / "answer.json")!r}; }}; SERVER=http://x; '
              'attended_hello "a question"; echo "rc=$?"; '
              'test -s "$RUN_DIR/power.resp" && echo POWER-KEPT; echo "round=[$(cat "$RUN_DIR/attended.round")]"')
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert ("POWER-KEPT" in out.stdout) == power, out.stdout
    assert f"round=[{round_}]" in out.stdout, out.stdout


def test_the_screen_is_told_the_round_is_open(tmp_path):
    out = run(tmp_path, 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC"; echo boot > "$LC/screen"; '
              'echo "0|1" > "$LC/boot"; echo open > "$RUN_DIR/attended.round"; lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    assert "lc_round=open" in out.stdout.splitlines(), out.stdout


# --- 3. smartctl's ceiling -------------------------------------------------------------

def _hanging_smartctl(tmp_path: Path, secs: int = 20) -> Path:
    p = tmp_path / "smartctl"
    p.write_text(f"#!/bin/sh\nsleep {secs}\n", newline="\n")
    p.chmod(0o755)
    return p


def _sh(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def test_the_gate_names_a_drive_that_does_not_answer_and_goes_on(tmp_path):
    run_dir = tmp_path / "run"; run_dir.mkdir()
    out = _sh(f'export RUN_DIR={posix(run_dir)!r} DEVROOT={posix(tmp_path)!r} '
              f'SMARTCTL={posix(_hanging_smartctl(tmp_path))!r} SMARTCTL_MAX_S=2; '
              f'. {posix(LIB)}/common.sh; . {posix(LIB)}/waits.sh; . {posix(LIB)}/smart.sh; '
              't0=$(date +%s); v=$(smart_probe sda); '
              'echo "v=$v reason=$(smart_field sda 2) dt=$(( $(date +%s) - t0 ))"')
    assert out.returncode == 0, out.stderr
    assert "v=unchecked reason=timeout" in out.stdout, out.stdout
    assert int(out.stdout.split("dt=")[1].split()[0]) < 15
    assert "לא ענה תוך 2 שניות" in (run_dir / "agent.log").read_text(encoding="utf-8")


def test_the_hello_probe_names_a_drive_that_does_not_answer(tmp_path):
    (tmp_path / "sys" / "block" / "sda").mkdir(parents=True)
    run_dir = tmp_path / "run"; run_dir.mkdir()
    out = _sh(f'export SYSROOT={posix(tmp_path)!r} RUN_DIR={posix(run_dir)!r} DEVROOT={posix(tmp_path)!r} PROBE_STUBS=1 '
              f'SMARTCTL={posix(_hanging_smartctl(tmp_path))!r} SMARTCTL_MAX_S=2; '
              f'. {posix(LIB)}/common.sh; . {posix(LIB)}/waits.sh; . {posix(LIB)}/probe.sh; '
              't0=$(date +%s); d=$(probe_disks); echo "$d"; echo "dt=$(( $(date +%s) - t0 ))"')
    assert out.returncode == 0, out.stderr
    assert '"smart_errors":{"error":"smartctl לא ענה תוך 2 שניות על sda"}' in out.stdout, out.stdout
    assert int(out.stdout.split("dt=")[1].split()[0]) < 15


def test_the_crc_delta_after_the_write_does_not_wait_for_a_stuck_drive(tmp_path):
    run_dir = tmp_path / "run"; (run_dir / "smart").mkdir(parents=True); (run_dir / "targets" / "sda").mkdir(parents=True)
    (run_dir / "smart" / "sda.v").write_text("ok ok 0 0 0 5\n", newline="\n")
    out = _sh(f'export RUN_DIR={posix(run_dir)!r} DEVROOT={posix(tmp_path)!r} '
              f'SMARTCTL={posix(_hanging_smartctl(tmp_path))!r} SMARTCTL_MAX_S=2; '
              f'. {posix(LIB)}/common.sh; . {posix(LIB)}/waits.sh; . {posix(LIB)}/smart.sh; . {posix(LIB)}/crcdelta.sh; '
              't0=$(date +%s); crc_delta_measure sda; echo "dt=$(( $(date +%s) - t0 ))"')
    assert out.returncode == 0, out.stderr
    assert "crc delta not measured (smartctl did not answer within 2s)" in out.stdout, out.stdout
    assert not (run_dir / "targets" / "sda" / "crc_delta").exists()    # not read is not 0 (principle 5)
    assert int(out.stdout.split("dt=")[1].split()[0]) < 15


# --- 4. the screen (native) --------------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "lcui.h"
#include "state.h"
/* weak: the same driver builds against the code before the fix, where the
 * function does not exist -- the negative control fails on behaviour, not on
 * a missing symbol */
const char *lc_round_note(const App *a) __attribute__((weak));
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;
    (void)argc;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2;
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_flush(s);
    const unsigned char *px = cairo_image_surface_get_data(s); int n = cairo_image_surface_get_stride(s) * 800;
    unsigned long long h = 1469598103934665603ULL;      /* FNV-1a of the frame's pixels */
    for (int i = 0; i < n; i++) { h ^= px[i]; h *= 1099511628211ULL; }
    cairo_surface_destroy(s);
    const char *note = lc_round_note ? lc_round_note(&a) : NULL; Rgb dot;
    printf("note=%s\nstatus=%s\nframe=%016llx\n", note ? note : "-", lc_status_word(&a, &dot), h);
    return 0;
}
'''

needs_gui = pytest.mark.skipif(
    not ((shutil.which("cc") or shutil.which("gcc")) and _pkgconfig("pangocairo", "cairo", "libdrm")),
    reason="native-gui נבנה על המעבדה בלבד")


@pytest.fixture(scope="module")
def note_driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcround")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _note(driver: Path, tmp_path: Path, state: str) -> dict:
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


LOCAL = ("lc_screen=local\nlc_inv=done\nlc_phase=%s\n"
         "lc_disk=1|sda|A|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
         "lc_disk=3|sdc|C|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n")


@needs_gui
@pytest.mark.parametrize("state,want", [
    ("lc_round=open\nlc_screen=boot\nlc_boot=0|1|1000\n", "בחרו 'מהשרת'"),
    ("lc_round=open\n" + LOCAL % "idle", "'חזרה' ואז 'מהשרת'"),
])
def test_an_open_round_is_said_on_the_screen(note_driver, tmp_path, state, want):
    got = _note(note_driver, tmp_path, state)
    assert want in got["note"] and "נפתח סבב בשרת" in got["note"], got


@needs_gui
def test_during_the_copy_the_round_rides_the_status_bar(note_driver, tmp_path):
    got = _note(note_driver, tmp_path, "lc_round=open\n" + LOCAL % "copying")
    assert got["status"].endswith("· נפתח סבב בשרת"), got


@needs_gui
@pytest.mark.parametrize("round_", ["", "none", "running"])
def test_no_open_round_says_nothing(note_driver, tmp_path, round_):
    got = _note(note_driver, tmp_path, f"lc_round={round_}\nlc_screen=boot\nlc_boot=0|1|1000\n")
    assert got["note"] == "-", got
    got = _note(note_driver, tmp_path, f"lc_round={round_}\n" + LOCAL % "copying")
    assert "סבב" not in got["status"], got


@needs_gui
@pytest.mark.parametrize("frame", [
    "lc_screen=boot\nlc_boot=0|1|1000\n",
    LOCAL % "idle",
    LOCAL % "copying",
])
def test_the_frame_with_an_open_round_is_not_the_frame_without(note_driver, tmp_path, frame):
    """Pixels: whatever the words, the screen is drawn differently -- and only for "open"."""
    plain = _note(note_driver, tmp_path, frame)["frame"]
    assert plain == _note(note_driver, tmp_path, "lc_round=none\n" + frame)["frame"]   # nothing open = nothing said
    assert plain != _note(note_driver, tmp_path, "lc_round=open\n" + frame)["frame"]


# --- 5. a power request confirmed over the copy (Nadav 14/10) --------------------------

#: a copy in the middle: the screen is up, two targets, the source
COPYING = ('mkdir -p "$LC/run"; echo local > "$LC/screen"; echo copying > "$LC/phase"; '
           'printf "sda\nsdb\n" > "$LC/run/targets"; echo sdc > "$LC/run/src"; date +%s > "$LC/run/started"; '
           'target_init sda 100; target_init sdb 100; ata_capture() { :; }; lc_report() { echo REPORT; }; ')


def _beat(tmp_path, answer: str, extra: str = "") -> subprocess.CompletedProcess:
    (tmp_path / "answer.json").write_text(answer, encoding="utf-8")
    return run(tmp_path, COPYING + POWER.replace("attended_stop; kill $_gui_pid; ", "") + extra
               + 'build_hello() { echo \'{"schema":2}\'; }; SERVER=http://x; '
               f'http_post_json() {{ cp "$2" "$RUN_DIR/sent.json"; cat {posix(tmp_path / "answer.json")!r}; }}; '
               'attended_hello "q"; echo "after rc=$?"; '
               'for t in sda sdb; do echo "$t=$(cat "$RUN_DIR/targets/$t/state")|$(cat "$RUN_DIR/targets/$t/error" 2>/dev/null)"; done')


def test_a_confirmed_power_off_during_the_copy_marks_the_targets_reports_and_powers_off(tmp_path):
    out = _beat(tmp_path, '{"known":true,"power_action":"poweroff","power_confirmed":true}', 'arm_wol() { return 0; }; ')
    # ‏#1378 (170b9bec): כיבוי עובר ב-safe_poweroff — WoL נדרך ואז poweroff
    # (‏IMAGESRV_TEST: ‏TEST-POWEROFF וחזרה, ולכן "after rc=" כן מודפס כאן)
    assert "TEST-POWEROFF" in out.stdout, out.stdout + out.stderr
    assert out.stdout.index("REPORT") < out.stdout.index("TEST-POWEROFF")   # the journal is told first
    assert "the console confirmed 'poweroff' in the middle of the copy" in out.stdout
    assert "powering off with wake-on-LAN armed" in out.stdout
    state = (tmp_path / "run" / "targets" / "sda" / "state").read_text(encoding="utf-8").strip()
    error = (tmp_path / "run" / "targets" / "sdb" / "error").read_text(encoding="utf-8")
    assert state == "failed" and "באמצע שיכפול מקומי" in error


def test_an_unconfirmed_request_during_the_copy_is_left_for_the_screen_to_drop(tmp_path):
    out = _beat(tmp_path, '{"known":true,"power_action":"poweroff"}', 'arm_wol() { return 0; }; ')
    assert "POWER" not in out.stdout and "after rc=0" in out.stdout, out.stdout + out.stderr
    assert "sda=writing" in out.stdout or "sda=waiting" in out.stdout, out.stdout     # not marked
    assert (tmp_path / "run" / "power.resp").exists()                 # lc_session drops it, aloud


def test_a_confirmed_power_off_without_wake_on_lan_does_nothing_aloud(tmp_path):
    out = _beat(tmp_path, '{"known":true,"power_action":"poweroff","power_confirmed":true}', 'arm_wol() { return 1; }; ')
    assert "POWER" not in out.stdout and "REPORT" not in out.stdout, out.stdout
    assert "wake-on-LAN was not armed -- not powering off, the copy goes on" in out.stdout
    assert "failed" not in out.stdout.split("after rc=")[1]


def test_a_confirmed_request_when_no_copy_runs_is_the_screens_to_carry(tmp_path):
    """Not in the middle of a copy: the beat only keeps it -- the screen's loop
    acts on it (lc_power_check), as on any other request."""
    out = _beat(tmp_path, '{"known":true,"power_action":"poweroff","power_confirmed":true}',
                'arm_wol() { return 0; }; echo idle > "$LC/phase"; ')
    assert "POWER" not in out.stdout and (tmp_path / "run" / "power.resp").exists(), out.stdout


@pytest.mark.parametrize("phase,said", [("copying", True), ("verifying", True), ("idle", False), ("done", False)])
def test_the_hello_says_a_local_copy_is_writing(tmp_path, phase, said):
    out = _beat(tmp_path, '{"known":true}', f'echo {phase} > "$LC/phase"; ')
    assert "after rc=0" in out.stdout, out.stdout + out.stderr
    sent = (tmp_path / "run" / "sent.json").read_text(encoding="utf-8")
    assert ('"local_clone":"copying"' in sent) == said, sent
