"""#2024 — a cloner does not power off by itself when a disk ended with a problem.

**Nadav's decision (22/10).** At the end of a cloner's task — a round from the
server, "restore from the server" (lcpull, the same ``do_restore_drawers``),
a local clone and the USB stick (both ``lc_end_count``) — a target that ended
red or orange, or whose result cannot be read (principle 5), keeps the results
on the screen with **no count and no ceiling** until "כבה". All green: as
before, ``END_POWEROFF_S`` (20 s) and off. It replaces the 300 s ceiling of
#1560 (``END_HOLD_S``), and the 5-minute idle power-off never runs during it
(the hold loop is not ``poll_sleep``).

What is checked, behaviourally (the real ``idle.sh`` / ``lcwait.sh`` /
``attended.sh`` / ``guistate.sh`` in sh; only the power-off primitive, the
clock and the beat's process are stubbed):

* a clean end counts and powers off; every kind of problem holds past 600 s
  (both old ceilings) and powers off on ``end.now`` only;
* the reasons the screen draws (``end.problems`` -> ``end_problem=``), red or
  orange per target;
* the console's power-off still reaches a held machine that has a beat (the
  local clone's); the local result screen's own buttons stay the person's;
* the screen (cc + pango, the Testrunner): the sentence, the button, each
  reason, and the problem rows tinted.

**Negative control** (in the PR): with the production files of the base
(0a47aaf4) the "holds" tests fail — the machine powers off after 20 s.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_end_of_task_1560 import bash, q, trace_of
from test_final_report import (  # noqa: F401 — `reports` is a fixture
    IMAGE,
    SESSION,
    native_tools,
    reports,
    run_path,
    url_of,
)

needs_bash = requires_native(("bash", BASH))
needs_jq = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

HOLD_SENTENCE = "המחשב נשאר דלוק כי יש דיסק עם בעיה — לחצו 'כבה' כשסיימתם"
#: #2081: over a screen with its own "כבה את המחשב" (both of these) the bar is words only
OWN_SENTENCE = 'המחשב נשאר דלוק כי יש דיסק עם בעיה. כשתסיימו: "כבה את המחשב".'

#: A deterministic second: the loop's `sleep 1`. PRESS_AT = the tick "כבה" is
#: pressed (end.now). 601 > END_HOLD_S (300, the old ceiling) and > the idle
#: power-off (IDLE_POWEROFF_S, 300). safe_poweroff is the one road to S5.
HARNESS = r'''
tick=0
finish_and_stop() { echo "FINISH $tick" >> "$RUN_DIR/trace"; return 0; }
safe_poweroff() { echo "SAFE-POWEROFF $tick $1" >> "$RUN_DIR/trace"; return 1; }
attended_start() { echo "BEAT-START $1" >> "$RUN_DIR/trace"; ATTENDED_PID=4242; }
attended_stop() { echo "BEAT-STOP" >> "$RUN_DIR/trace"; ATTENDED_PID=; }
watchdog_beat() { echo "$tick" > "$RUN_DIR/watchdog"; }
sleep() {
    tick=$((tick + 1))
    if [ "$tick" = 2 ]; then
        cp "$RUN_DIR/end.poweroff" "$RUN_DIR/seen"
        cp "$RUN_DIR/end.problems" "$RUN_DIR/seen.problems" 2>/dev/null || :
    fi
    [ "$tick" != "${CONSOLE_AT:-x}" ] || printf '{"power_action":"poweroff"}' > "$RUN_DIR/power.resp"
    [ "$tick" != "${REQ_AT:-x}" ] || echo "${REQ:-again}" > "$LC/end.req"
    [ "$tick" != "${PRESS_AT:-601}" ] || : > "$RUN_DIR/end.now"
    [ "$tick" -lt 2000 ] || { echo "RUNAWAY" >> "$RUN_DIR/trace"; exit 0; }
}
'''

CLEAN = {"state": "done", "readback": "sampled"}


def world(tmp_path: Path, targets: dict[str, dict[str, str]]) -> Path:
    """RUN_DIR with targets/<disk>/<file> = its text."""
    run = tmp_path / "run"
    (run / "lc" / "run").mkdir(parents=True, exist_ok=True)
    for disk, files in targets.items():
        d = run / "targets" / disk
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text + "\n", encoding="utf-8", newline="\n")
    return run


def end(tmp_path: Path, targets: dict, call: str, env: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    run = world(tmp_path, targets)
    script = (
        f"export RUN_DIR={q(run)} LC={q(run / 'lc')} LOG_FILE={q(run / 'agent.log')} MAC=aa:bb:cc:dd:ee:ff "
        f"SERVER=http://127.0.0.1:9 IMAGESRV_TEST=0 {env}; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/core/attended.sh; "
        f". {posix(AGENT)}/lib/core/idle.sh; . {posix(AGENT)}/lib/localclone/lcwait.sh; "
        + HARNESS + call + '; echo "rc=$?"'
    )
    return bash(script, timeout=120), run


def held_until_press(out: subprocess.CompletedProcess, run: Path, press: int = 601) -> None:
    trace = trace_of(run)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace[-1] == f"FINISH {press}", trace                  # off only on "כבה", at its tick
    assert not [t for t in trace if t.startswith("SAFE-POWEROFF")], trace   # nothing else powered it off
    at, _kind, _done, _total, held = (run / "seen").read_text().strip().split("|")
    assert (at, held) == ("0", "1"), "held: epoch 0, no time limit"
    assert (run / "watchdog").read_text().strip() == str(press - 1), "the watchdog is fed all along"
    for f in ("end.poweroff", "end.problems", "end.now"):
        assert not (run / f).exists(), f


def problems(run: Path) -> dict[str, tuple[str, ...]]:
    rows = (run / "seen.problems").read_text(encoding="utf-8").splitlines()
    return {r.split("|")[0]: tuple(r.split("|", 2)[1:]) for r in rows}


# --- all green: as before ---------------------------------------------------------------


@needs_jq
@pytest.mark.parametrize("extra", [
    {},
    {"readback": "verified"},
    {"write_check_state": "ok", "drivers.json": '{"state":"staged","packages":["x"],"boot":{"state":"enabled"}}'},
    {"drivers.json": '{"state":"skipped","reason":"no_model"}'},
], ids=["sampled", "verified", "drivers-staged", "drivers-no-model"])
def test_every_target_clean_counts_20_seconds_and_powers_off(tmp_path, extra):
    out, run = end(tmp_path, {"sdb": CLEAN, "sdc": {**CLEAN, **extra}},
                   'end_countdown drawers "drawers complete (2 of 2)" 2 2 sdb sdc')
    trace = trace_of(run)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace == ["FINISH 20"], trace                          # no beat, no hold
    at, kind, done, total, held = (run / "seen").read_text().strip().split("|")
    assert (kind, done, total, held) == ("drawers", "2", "2", "0") and int(at) > 0
    assert (run / "seen.problems").read_text() == ""


# --- a problem: no count, no ceiling --------------------------------------------------

PROBLEMS = {
    # name: (sdc's files, tone, a piece of the reason)
    "failed": ({"state": "failed", "error": "partclone: write error"}, "r", "partclone: write error"),
    "skipped-red-smart": ({"state": "skipped", "error": "SMART אדום"}, "r", "SMART אדום"),
    "cancelled": ({"state": "cancelled", "error": "בוטל במחשב"}, "r", "בוטל במחשב"),
    "still-writing": ({"state": "writing"}, "r", "(writing)"),
    "unknown-word": ({"state": "bogus"}, "r", "(bogus)"),
    "empty-state": ({"state": ""}, "r", "מצב הדיסק לא נקרא"),
    "no-state": ({"readback": "sampled"}, "r", "מצב הדיסק לא נקרא"),
    "done-with-warning": ({**CLEAN, "error": "הושלם, אך ההרחבה נדחתה"}, "o", "ההרחבה נדחתה"),
    "readback-unverified": ({"state": "done", "readback": "unverified", "readback_reason": "אין רשימה לאימות"},
                            "o", "אין רשימה לאימות"),
    "readback-missing": ({"state": "done"}, "o", "האימות אחרי הכתיבה לא נרשם"),
    "readback-garbage": ({"state": "done", "readback": "maybe"}, "o", "האימות אחרי הכתיבה לא נרשם"),
    "write-check-slow": ({**CLEAN, "write_check_state": "slow", "write_check_reason": "12 MB/s"}, "r", "12 MB/s"),
    "boot-driver-failed": ({**CLEAN, "drivers.json": '{"state":"staged","boot":{"state":"failed","error":"x"}}'},
                           "r", "דרייבר האחסון לא הוגדר"),
    "drivers-not-placed": ({**CLEAN, "drivers.json": '{"state":"failed","error":"no space"}'}, "o", "דרייברים לא הונחו"),
    "linux-hostonly": ({**CLEAN, "drivers.json": '{"state":"skipped","linux":{"state":"hostonly"}}'}, "o", "לינוקס"),
    "drivers-unreadable": ({**CLEAN, "drivers.json": "{not json"}, "o", "תוצאת הדרייברים לא נקראה"),
    "pipe-in-reason": ({"state": "failed", "error": "a|b"}, "r", "a/b"),
}


@needs_jq
@pytest.mark.parametrize("name", list(PROBLEMS))
def test_a_problem_holds_past_both_old_ceilings_until_the_button(tmp_path, name):
    files, tone, why = PROBLEMS[name]
    out, run = end(tmp_path, {"sdb": CLEAN, "sdc": files},
                   'end_countdown drawers "drawers complete" 2 2 sdb sdc')
    held_until_press(out, run)
    got = problems(run)
    assert list(got) == ["sdc"], got                              # the clean drawer is not named
    assert got["sdc"][0] == tone and why in got["sdc"][1], got
    # no hello of its own: a "seen" cloner would stop the server's re-wake of a new wave (#1721)
    assert not [t for t in trace_of(run) if t.startswith("BEAT")], trace_of(run)


@needs_bash
def test_an_error_file_that_cannot_be_read_is_not_no_warning(tmp_path):
    run = world(tmp_path, {"sdb": CLEAN})
    (run / "targets" / "sdb" / "error").mkdir()                    # cat fails: unreadable, not empty
    out, run = end(tmp_path, {}, 'end_countdown localclone "local clone ended" 1 1 sdb')
    held_until_press(out, run)
    assert problems(run)["sdb"] == ("o", "תוצאת הדיסק לא נקראה")


@needs_bash
def test_no_target_named_is_not_a_clean_end(tmp_path):
    out, run = end(tmp_path, {}, 'end_countdown drawers "drawers complete" "" ""')
    held_until_press(out, run)
    assert problems(run)[""] == ("r", "לא נמצאו יעדים לבדיקה — לא ידוע מה נכתב")


@needs_bash
@pytest.mark.parametrize("kind", ["partial", "failed"])
def test_the_callers_own_failed_count_holds_even_if_the_files_read_clean(tmp_path, kind):
    out, run = end(tmp_path, {"sdb": CLEAN}, f'end_countdown {kind} "x" 0 1 sdb')
    held_until_press(out, run)


@needs_jq
def test_the_console_power_off_reaches_a_held_machine(tmp_path):
    """A running beat (the local clone's) keeps the console's power_action
    (attended_keep); the hold carries it out (power_check -> safe_poweroff).
    Refused (WoL) -- the results stay, and "כבה" still works."""
    out, run = end(tmp_path, {"sdb": {"state": "failed", "error": "x"}},
                   'end_countdown failed "x" 0 1 sdb', env="CONSOLE_AT=5 PRESS_AT=9")
    trace = trace_of(run)
    assert "SAFE-POWEROFF 5 power: the console asked to power off" in trace, trace
    assert trace[-1] == "FINISH 9", trace


# --- the callers -------------------------------------------------------------------------


@needs_jq
def test_a_local_clone_with_a_problem_holds_and_leaves_its_beat_alone(tmp_path):
    """lc_flow runs the attended beat (attended_start "Local clone at the
    machine"): the hold neither replaces nor stops it."""
    targets = {"sdb": CLEAN, "sdc": {"state": "done"}}
    run = world(tmp_path, targets)
    (run / "lc" / "run" / "targets").write_text("sdb\nsdc\n", newline="\n")
    out, run = end(tmp_path, targets, "ATTENDED_PID=77; lc_end_count")
    held_until_press(out, run)
    assert not [t for t in trace_of(run) if t.startswith("BEAT")], trace_of(run)
    assert list(problems(run)) == ["sdc"]
    assert (run / "seen").read_text().split("|")[1] == "localclone"


@needs_bash
def test_a_clean_local_clone_still_counts(tmp_path):
    run = world(tmp_path, {"sdb": CLEAN})
    (run / "lc" / "run" / "targets").write_text("sdb\n", newline="\n")
    out, run = end(tmp_path, {}, "lc_end_count")
    assert trace_of(run) == ["FINISH 20"], trace_of(run)


@needs_bash
def test_a_local_clone_whose_target_list_is_gone_holds(tmp_path):
    out, run = end(tmp_path, {"sdb": CLEAN}, "lc_end_count")      # no $LC/run/targets
    held_until_press(out, run)
    assert "" in problems(run)


@needs_bash
@pytest.mark.parametrize("req", ["again", "back", "poweroff"])
def test_the_local_result_screens_own_buttons_stay_the_persons(tmp_path, req):
    """A person at the machine who pressed "שכפול נוסף" / back / "כבה את
    המחשב" on the result has seen it: the hold gives way (rc 2) and the
    caller (lc_flow) acts on end.req -- as the count did (#1560)."""
    run = world(tmp_path, {"sdb": {"state": "failed", "error": "x"}})
    (run / "lc" / "run" / "targets").write_text("sdb\n", newline="\n")
    out, run = end(tmp_path, {}, "lc_end_count", env=f"REQ_AT=450 REQ={req}")
    assert out.stdout.strip().endswith("rc=2"), out.stdout + out.stderr
    assert not [t for t in trace_of(run) if t.startswith(("FINISH", "SAFE"))], trace_of(run)
    assert (run / "lc" / "end.req").read_text().strip() == req   # left for lc_flow
    assert not (run / "end.poweroff").exists() and not (run / "end.problems").exists()


@needs_bash
def test_a_failed_round_holds_with_every_drawer_and_its_reason(tmp_path):
    """end_failed_round: the drawers the SMART gate skipped (_all) are named
    too, each with its reason; "כבה" powers off, no failure screen after."""
    stubs = ('report_final() { return 0; }; agent_log_send() { :; }; '
             'ui_error_hold() { echo "UI-HOLD" >> "$RUN_DIR/trace"; }; '
             '_all=" sdb sdc"; _disks=" sdc"; ')
    out, run = end(tmp_path, {"sdb": {"state": "skipped", "error": "SMART אדום — דולג"},
                              "sdc": {"state": "failed", "error": "הזרם נקטע"}},
                   stubs + 'end_failed_round "no drawer completed" ses_1 0; [ "$?" = 1 ]')
    trace = trace_of(run)
    assert trace[-1] == "FINISH 601" and "UI-HOLD" not in trace, trace
    assert (run / "seen").read_text().strip() == "0|failed|0|2|1"
    assert problems(run) == {"sdb": ("r", "SMART אדום — דולג"), "sdc": ("r", "הזרם נקטע")}


@native_tools
def test_the_round_hands_every_drawer_to_the_end(tmp_path, reports):
    """do_restore_drawers (a round, and lcpull's restore from the server) passes
    its drawers, the skipped ones too, so their results decide."""
    partial = ('run_restore_drawers() { target_set sdb done; target_set sdc failed; '
               'echo done > "$RUN_DIR/state"; return 0; }; '
               'end_countdown() { echo "END $1|$3|$4|$(echo $5 $6)" >> "$RUN_DIR/trace"; exit 0; }; ')
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                      stubs=partial, env="IMAGESRV_TEST=0 D_ROLE=cloner")
    assert "END partial|1|2|sdb sdc" in result["trace"], result["trace"]


# --- the screen's records ------------------------------------------------------------------


@needs_bash
def test_every_screen_carries_the_hold_and_its_problem_disks(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "end.problems").write_text("sdc|r|הזרם נקטע\n", encoding="utf-8", newline="\n")
    pre = f"export RUN_DIR={q(run)}; . {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/gui/guistate.sh; "
    # a left-over list with no end on the screen draws nothing
    assert "end_problem" not in bash(pre + "gui_hello_state").stdout
    (run / "end.poweroff").write_text("0|partial|1|2|1\n", newline="\n")
    lines = bash(pre + "gui_hello_state").stdout.splitlines()
    assert "end_poweroff=0|partial|1|2|1" in lines and "end_problem=sdc|r|הזרם נקטע" in lines, lines


# --- the screen (cc + pango: the Testrunner) ---------------------------------------------

from test_cloner_gui import _build_gui, _png_rgb  # noqa: E402
from test_lc_progress_1437_1440 import DISK, DISK2, driver, needs_cc, screens  # noqa: E402,F401 — driver is a fixture
from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402

CLONER = ("cloner=Win11\ndrawer_count=3\n"
          "drawer=1|sda|done|100|100||-1|-1|1|SATA 0||\n"
          "drawer=2|sdb|failed|10|100|הזרם נקטע|-1|-1|2|SATA 1||\n"
          "drawer=3|sdc|done|100|100||-1|-1|3|SATA 2||\n")
HELD = ("end_poweroff=0|partial|2|3|1\n"
        "end_problem=sdb|r|הזרם נקטע באמצע מחיצה 2\n"
        "end_problem=sdc|o|אין רשימה לאימות — מה שנכתב לא נבדק מול הדיסק\n")


@needs_cc
def test_the_held_end_says_why_and_names_each_problem_drawer(driver, tmp_path):
    held, timed = screens(driver, tmp_path, CLONER + HELD,
                          CLONER + "end_poweroff=2000000000|drawers|3|3|0\n")
    assert OWN_SENTENCE in held and "כבה את המחשב" in held, held   # #2081
    assert "הזרם נקטע באמצע מחיצה 2" in held                    # each with its own reason
    assert "אין רשימה לאימות — מה שנכתב לא נבדק מול הדיסק" in held
    assert OWN_SENTENCE not in timed and "כבה עכשיו" in timed, timed


@needs_cc
def test_the_local_clone_result_names_its_problem_target(driver, tmp_path):
    lc = ("lc_screen=local\nlc_inv=done\nlc_phase=done\n" + DISK + DISK2 +
          "lc_run=sdc|206000000000|206000000000|900|0|sent|2||\n"
          "lc_part=1|EFI|vfat|3030000000|3030000000|done|0\n"
          "lc_tgt=sdb|done|206000000000|2|2||||206000000000\n")
    held, plain = screens(driver, tmp_path, lc + "end_poweroff=0|localclone|1|1|1\n"
                                              "end_problem=sdb|o|האימות אחרי הכתיבה לא נרשם\n", lc)
    assert OWN_SENTENCE in held and "האימות אחרי הכתיבה לא נרשם" in held, held   # #2081
    assert any(s.endswith("· הושלם, עם בעיה") for s in held), held   # not "identical" over its reason
    assert not any(s.endswith("· הושלם, עם בעיה") for s in plain), plain


@needs_cc
def test_an_unlimited_end_that_is_not_held_is_refused(driver, tmp_path):
    """Epoch 0 means "no limit" only with held=1; anything else is no record
    (never a count that says 0 seconds, never an unsaid hold)."""
    drawn, = screens(driver, tmp_path, CLONER + "end_poweroff=0|partial|1|2|0\n")
    assert HOLD_SENTENCE not in drawn and OWN_SENTENCE not in drawn and not any(s.startswith("המחשב ייכבה") for s in drawn), drawn


def _tint(px, ch, rgb, tol=4) -> int:
    r0, g0, b0 = rgb
    return sum(1 for i in range(0, len(px), ch * 3)
               if abs(px[i] - r0) <= tol and abs(px[i + 1] - g0) <= tol and abs(px[i + 2] - b0) <= tol)


@GUI_TOOLS
def test_the_problem_drawers_are_highlighted(tmp_path):
    """The cloner-end card: the red and the orange rows are tinted (danger-soft /
    warning-soft of the light theme); without end_problem records they are not."""
    binary = _build_gui(tmp_path)
    run = _render(binary, tmp_path / "held", BASE_STATE + CLONER + HELD)
    assert "malformed" not in run.stderr, run.stderr
    _render(binary, tmp_path / "plain", BASE_STATE + CLONER + "end_poweroff=0|partial|2|3|1\n")
    _w, _h, ch, held = _png_rgb(tmp_path / "held" / "card-cloner-end-light.png")
    _w, _h, ch2, plain = _png_rgb(tmp_path / "plain" / "card-cloner-end-light.png")
    danger_soft, warning_soft = (0xFC, 0xED, 0xEA), (0xFF, 0xF2, 0xE3)
    # #2050 (mockup 07): a failed drawer is framed and tinted red whether or not it holds the end
    assert _tint(held, ch, danger_soft) > 2000 and _tint(plain, ch2, danger_soft) > 2000
    assert _tint(held, ch, warning_soft) - _tint(plain, ch2, warning_soft) > 2000
    assert (tmp_path / "held" / "card-cloner-end-dark.png").exists()
