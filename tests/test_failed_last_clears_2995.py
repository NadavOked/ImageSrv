"""#2995 (Nadav 12/11, v0.92.0, cloner 2) -- "failed the previous clone" stays after a good write.

A round wrote 3 of 3 drawers. Drawer 1 had failed the round before (cause=cable), so the
SMART gate marked it orange (failed_last, smartgate.sh). The write succeeded and was
checked, and still: the card was orange, "נכשל בשיכפול הקודם — סיבה: כבל/חריץ", and
"המחשב נשאר דלוק כי יש דיסק עם בעיה". The root: smart_note_apply put the gate's note in
the `error` of a target that ended done, and idle.sh::end_problem counts any done target
with an `error` as orange. The server did clear its disk_failures row -- 4 s after the
agent's final report, at the round's close.

The gate is shared by both engines (udpcast and chunks): imagesrv-agent calls smart_gate
before the branch, and smart_note_apply after it.

The fix: a write that ends done answers "failed the previous clone". What stays on a done
target is SMART's own warning (a disk that is both warn and failed_last), or nothing.

Negative control: agent/lib/smart/smartgate.sh back to the base -- see the PR.
"""

from __future__ import annotations

from pathlib import Path

from test_orange_no_ask_2042 import APPLY, NOTE_187, WD_187, _error, bash_only
from test_smart import HEALTH_OK, full, posix, preflight, sh

FAILED_LAST = "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"


def _gate_then_end(tmp_path: Path, body: str, state: str, error: str | None = None) -> Path:
    """The gate as a round meets it (sda failed the previous clone, cause=cable), then the
    write ends in `state`, then smart_note_apply -- imagesrv-agent's order."""
    written, run, err = preflight(tmp_path, body, failed=("sda",))
    assert written == "sda", err
    t = run / "targets" / "sda"
    (t / "state").write_text(state + "\n", newline="\n")
    if error is not None:
        (t / "error").write_text(error + "\n", encoding="utf-8", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r}; ' + APPLY + 'smart_note_apply sda; echo "rc=$?"')
    assert out.stdout.split() == ["rc=0"], (out.stdout, out.stderr)
    return run


@bash_only
def test_a_disk_that_failed_last_time_and_is_written_now_ends_without_a_warning(tmp_path):
    """RED: `error` = "נכשל בשיכפול הקודם — סיבה: כבל/חריץ" on a done target -- orange,
    and the machine stays on."""
    run = _gate_then_end(tmp_path, full(HEALTH_OK), "done")
    assert _error(run, "sda") is None
    assert (run / "targets/sda/state").read_text().strip() == "done"


@bash_only
def test_smarts_own_warning_stays_on_a_disk_that_also_failed_last_time(tmp_path):
    """RED: the failed_last note hid SMART's own warning, and rode on the done target instead
    of it. A disk that is warn (187) and failed_last ends "done with a warning" -- SMART's."""
    run = _gate_then_end(tmp_path, WD_187, "done")
    assert _error(run, "sda") == NOTE_187


@bash_only
def test_a_disk_that_failed_again_keeps_its_own_reason(tmp_path):
    """Guard (passes in both states): not done = its own reason stands, no note added."""
    run = _gate_then_end(tmp_path, full(HEALTH_OK), "failed", error="dd: error writing")
    assert _error(run, "sda") == "dd: error writing"


@bash_only
def test_the_gate_still_says_failed_last_before_the_write(tmp_path):
    """Guard (passes in both states): the gate's verdict and its note are unchanged -- the
    disk is still orange until it is written (#1580, #2042)."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), failed=("sda",))
    assert written == "sda", err
    assert (run / "targets/sda/smart_note").read_text(encoding="utf-8") == FAILED_LAST


@bash_only
def test_a_warn_only_disk_still_ends_done_with_its_warning(tmp_path):
    """Guard (passes in both states): no failure record -- #2042 unchanged."""
    written, run, err = preflight(tmp_path, WD_187)
    assert written == "sda", err
    (run / "targets/sda/state").write_text("done\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r}; ' + APPLY + 'smart_note_apply sda; echo "rc=$?"')
    assert out.stdout.split() == ["rc=0"], (out.stdout, out.stderr)
    assert _error(run, "sda") == NOTE_187
