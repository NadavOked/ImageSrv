"""#2042 (נדב 23/10, v1): דיסק יעד **כתום** נכתב **בלי שאלה** — בסבב מהשרת, בשחזור, במשיכה
ובהפצה ישירה (כולם עוברים ב-`smart_gate`, smartgate.sh). בסוף הוא מקבל הערה כתומה עם הסיבה.
**אדום** (SMART נכשל / F1) — ללא שינוי: אסור כיעד (#1732). **ירוק** — ללא שינוי.

למה: בבדיקת קנה המידה (23/10, ‏room_8c6bf26c) שאלת ה-SMART על WD כתום (‏187=1682) עצרה את
כל הגל 113 שניות עד תשובה מהקונסולה (‏`send_held_smart`, ‏sender._hold_for_asks). השולח
עוצר רק על שאלה פתוחה (‏smart_ask.waiting_in_wave) — שער שאינו שואל אינו עוצר גל.

בקרה שלילית (מעבדה, משורש העץ, אחרי commit):
  git checkout 4f6068e8 -- agent/lib/smart/smartgate.sh agent/imagesrv-agent server/static/console.js \
      server/static/index.html server/static/monitor.html
  python3 -m pytest tests/test_orange_no_ask_2042.py -q   # כל מה שמסומן RED נופל
  git checkout HEAD -- agent/ server/
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from native import requires_native
from test_final_report import (  # noqa: F401 -- `reports` is a fixture
    IMAGE, SESSION, native_tools, reports, run_path, url_of,
)
from test_smart import (BASH, GUI_STUB, HEALTH_FAIL, HEALTH_OK, PRELUDE, asked, attrs, env, event, full,
                        make_smartctl, posix, preflight, sh)

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
bash_only = requires_native(("bash", BASH))

#: the WD of 23/10 (82f686272ac5): PASSED, 187 Reported_Uncorrect = 1682 -- orange from the knowledge table
WD_187 = HEALTH_OK + "\n\n" + attrs() + (
    "187 Reported_Uncorrect      0x0032   100   100   000    Old_age   Always       -       1682\n")
NOTE_187 = "SMART: שגיאות שלא תוקנו (187)"


def note(run: Path, dev: str = "sda") -> str | None:
    f = run / "targets" / dev / "smart_note"
    return f.read_text(encoding="utf-8") if f.exists() else None


# --- the gate: orange -> no question, written, a note ---------------------------------


@bash_only
def test_the_wd_with_187_is_written_without_a_question(tmp_path):
    """RED: on the old gate the disk was asked (`asked == ["sda"]`) and the screen's "skip" won."""
    written, run, err = preflight(tmp_path, WD_187, gui=["skip"])
    assert (run / "smart/sda.v").read_text().split()[:2] == ["warn", "reported_uncorrectable"], err
    assert asked(run) == [], err                                     # no question at all
    assert (run / "gui_answers").read_text().split() == ["skip"]    # the screen was never read
    assert written == "sda", err
    assert not (run / "targets/sda/state").exists(), "not skipped, not replacing"
    assert note(run) == NOTE_187
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["smart"]["reason"], ev["write_state"], ev["decision"]) == \
        ("warn", "reported_uncorrectable", "pending", None), ev       # nobody decided: null, not rescue


@bash_only
def test_a_disk_that_failed_the_previous_clone_is_written_without_a_question(tmp_path):
    """RED: failed_last (orange, #1580) was asked and the screen's "replace" powered the machine off."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), gui=["replace"], failed=("sda",))
    assert asked(run) == [], err
    assert written == "sda", err
    assert not (run / "smart/awaiting_replace").exists()
    assert note(run) == "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["write_state"], ev["decision"]) == ("failed_last", "pending", None), ev


@bash_only
def test_red_is_unchanged_skipped_never_asked_and_no_orange_note(tmp_path):
    """Guard (passes in both states): red never becomes a target, and gets no orange note."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL), gui=["rescue"])
    assert written == "" and asked(run) == [], err
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert note(run) is None
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["write_state"], ev["decision"]) == ("fail", "skipped", "skip"), ev


@bash_only
def test_green_is_unchanged_written_without_a_note(tmp_path):
    """Guard (passes in both states)."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK))
    assert written == "sda" and asked(run) == [], err
    assert note(run) is None
    ev = event(run)
    assert ev["smart"]["verdict"] in ("ok", "unchecked"), ev     # unchecked (ro_unread, no sysfs here) is green too
    assert (ev["write_state"], ev["decision"]) == ("pending", None), ev


@bash_only
def test_a_wave_with_one_orange_drawer_does_not_wait(tmp_path):
    """RED: one orange drawer among green ones. The question went through `attended` -- the hello
    beat that carries `ask` to the server, the one the sender holds the whole wave on. Here the
    screen never answers (a 30 s sleep stands for the person who is not there): the gate must not
    reach it. On the old gate it did -- `attended.calls` has the orange drawer, and the gate took
    the screen's whole wait."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    for d in ("sda", "sdb", "sdc"):
        (dev / d).write_bytes(b"\x00")
    (tmp_path / "wd").mkdir(); (tmp_path / "ok").mkdir()
    wd = make_smartctl(tmp_path / "wd", WD_187)
    ok = make_smartctl(tmp_path / "ok", full(HEALTH_OK))
    wrapper = tmp_path / "smartctl-by-dev"       # sdb = the WD, the others healthy
    wrapper.write_text(f'#!/bin/sh\ncase "$*" in *sdb*) exec {wd} "$@" ;; *) exec {ok} "$@" ;; esac\n',
                       newline="\n")
    stubs = ('attended() { echo "$1" >> "$RUN_DIR/attended.calls"; shift; "$@"; }; '
             'gui_smart_choice() { echo "$1" >> "$RUN_DIR/asked"; sleep 30; return 1; }; ')
    t0 = time.monotonic()
    out = sh(f'chmod +x {posix(wrapper)!r}; ' + env(run, dev, smartctl=posix(wrapper)) + PRELUDE + stubs
             + 'smart_gate sess1 sda sdb sdc; echo "rc=$?"')
    took = time.monotonic() - t0
    assert out.returncode == 0, out.stderr
    assert not (run / "attended.calls").exists(), (run / "attended.calls").read_text()
    assert not (run / "asked").exists()
    assert out.stdout.split() == ["sda", "sdb", "sdc", "rc=0"], (out.stdout, out.stderr)
    assert took < 25, f"the gate waited {took:.0f}s on the orange drawer"
    assert note(run, "sdb") == NOTE_187 and note(run, "sda") is None and note(run, "sdc") is None


# --- the end: the note rides on a done target -----------------------------------------

APPLY = (f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/progress.sh; '
         f'. {posix(AGENT)}/lib/smart/smart.sh; . {posix(AGENT)}/lib/smart/smartgate.sh; ')


def _target(run: Path, dev: str, state: str, error: str | None = None, smart_note: str | None = None) -> None:
    t = run / "targets" / dev
    t.mkdir(parents=True, exist_ok=True)
    (t / "state").write_text(state + "\n", newline="\n")
    if error is not None:
        (t / "error").write_text(error + "\n", encoding="utf-8", newline="\n")
    if smart_note is not None:
        (t / "smart_note").write_text(smart_note, encoding="utf-8", newline="\n")


def _error(run: Path, dev: str) -> str | None:
    f = run / "targets" / dev / "error"
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


@bash_only
def test_the_note_rides_on_a_done_target_and_only_there(tmp_path):
    """RED (smart_note_apply did not exist). A done orange target: "done with a warning".
    After an earlier warning: both, in order. Failed: its own reason stands. Green: nothing."""
    run = tmp_path / "run"
    _target(run, "sda", "done", smart_note=NOTE_187)
    _target(run, "sdb", "done", error="שם המחשב לא נכתב: x", smart_note=NOTE_187)
    _target(run, "sdc", "failed", error="dd: error writing", smart_note=NOTE_187)
    _target(run, "sdd", "done")
    out = sh(f'export RUN_DIR={posix(run)!r}; ' + APPLY + 'smart_note_apply sda sdb sdc sdd; echo "rc=$?"')
    assert out.stdout.split() == ["rc=0"], (out.stdout, out.stderr)
    assert _error(run, "sda") == NOTE_187
    assert _error(run, "sdb") == f"שם המחשב לא נכתב: x | {NOTE_187}"
    assert _error(run, "sdc") == "dd: error writing"
    assert _error(run, "sdd") is None
    assert [(run / "targets" / d / "state").read_text().strip() for d in ("sda", "sdb", "sdc", "sdd")] == \
        ["done", "done", "failed", "done"]


#: the gate as a round meets it: sdb orange (its note written, as smart_preflight does), sdc green
ORANGE_GATE = (f'. {posix(AGENT)}/lib/smart/smartgate.sh; '
               'smart_gate() { shift; mkdir -p "$RUN_DIR/targets/sdb"; '
               f'printf %s {NOTE_187!r} > "$RUN_DIR/targets/sdb/smart_note"; echo "$@"; }}; ')


@native_tools
def test_the_round_reports_the_orange_drawer_done_with_its_note(tmp_path, reports):
    """RED: do_restore_drawers did not put the note on the target -- the final report said
    sdb done, plain green, and the console and the cloner screen had nothing to show."""
    result = run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}", stubs=ORANGE_GATE)
    final = reports.received[-1]
    assert final["state"] == "done", result["out"]
    by_dev = {t["dev"]: t for t in final["targets"]}
    assert by_dev["sdb"]["state"] == "done" and by_dev["sdb"].get("error") == NOTE_187, by_dev
    assert by_dev["sdc"]["state"] == "done" and "error" not in by_dev["sdc"], by_dev


# --- the console: the note under the drawer on the room page --------------------------


def test_the_room_page_shows_a_done_drawers_warning():
    """RED: roundDskHtml drew a done drawer as a green 100% and dropped its `error` -- the
    orange note reached the server and no one saw it. Static: the line exists, on done only."""
    js = (REPO / "server/static/console.js").read_text(encoding="utf-8")
    body = js[js.index("function roundDskHtml("):]
    body = body[:body.index("\n}\n")]
    m = re.search(r'const (\w+) = live && d\.state === "done" && d\.error \? `<span class="dline warn">\$\{esc\(d\.error\)\}</span>` : "";', body)
    assert m, "no done-with-warning line in roundDskHtml"
    assert "${" + m.group(1) + "}" in body.split("return `", 1)[1], "the line is built and not drawn"
