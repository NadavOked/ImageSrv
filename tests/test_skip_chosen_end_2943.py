"""#2943 -- a drawer the operator chose to skip is not a problem at the end of the round.

On the cloner's end screen a drawer the operator skipped (the RAID question, #1808, or the split question,
#1898: "skip") was a red row, and the machine "stayed on because a disk has a problem": ``end_problem``
(idle.sh) called every ``skipped`` target red, and ``end_tally`` counted it as not written, so the end was
``partial`` -- held, no count. A skip is the person's choice: the row stays the grey "דולג", the count
runs, the machine powers off.

What is a *choice* is only an explicit answer. ``drawergate.sh`` marks ``targets/<d>/skip.chosen`` for the
answer ``skip`` and for nothing else:

* no answer (the question expired, the screen died) -> still skipped, no mark -> still red and held
  (principle 5: "nobody answered" is not "the operator decided");
* a drawer the SMART gate skipped (red disk) -> no mark -> still red and held (``skipped-red-smart``,
  test_end_hold_2024.py).

Behavioural, on the real ``idle.sh`` / ``drawergate.sh`` under bash (jq for ``end_problems``).
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH
from test_end_hold_2024 import CLEAN, end, held_until_press, needs_jq, problems
from test_end_of_task_1560 import trace_of
from test_raid_drawer_1808 import gate

pytestmark = requires_native(("bash", BASH))

#: what imagesrv-agent does at the end of a round (do_restore_drawers): tally, kind, countdown
ROUND_END = ('end_tally sdb sdc; _k=drawers; [ "$_et_ok" = "$_et_n" ] || _k=partial; '
             'end_countdown "$_k" "drawers complete ($_et_ok of $_et_n)" "$_et_ok" "$_et_n" sdb sdc')
SKIPPED = {"state": "skipped", "error": "לא נכתב: מידע RAID ישן על הדיסק (isw_raid_member), לא אושר"}


@needs_jq
def test_a_drawer_the_operator_skipped_does_not_hold_the_end(tmp_path):
    out, run = end(tmp_path, {"sdb": CLEAN, "sdc": {**SKIPPED, "skip.chosen": ""}}, ROUND_END)
    trace = trace_of(run)
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace == ["FINISH 20"], trace                         # the count ran, then it powered off: no hold
    at, kind, done, total, held = (run / "seen").read_text().strip().split("|")
    assert (kind, done, total, held) == ("drawers", "1", "1", "0") and int(at) > 0, (kind, done, total, held)
    assert (run / "seen.problems").read_text() == ""              # no red row: the skip is not a problem


@needs_jq
def test_the_same_skip_with_no_mark_is_still_a_red_held_problem(tmp_path):
    """Negative: no `skip.chosen` (a SMART skip, or a question nobody answered) -- red, held until "כבה"."""
    out, run = end(tmp_path, {"sdb": CLEAN, "sdc": SKIPPED}, ROUND_END)
    held_until_press(out, run)
    assert problems(run)["sdc"][0] == "r", problems(run)


@needs_jq
def test_a_skip_next_to_a_real_failure_still_holds_for_the_failure(tmp_path):
    out, run = end(tmp_path, {"sdb": {"state": "failed", "error": "partclone: write error"},
                              "sdc": {**SKIPPED, "skip.chosen": ""}}, ROUND_END)
    held_until_press(out, run)
    assert list(problems(run)) == ["sdb"], problems(run)         # only the failure is named; the skip is not


@needs_jq
def test_a_mark_on_a_drawer_that_is_not_skipped_changes_nothing(tmp_path):
    """The mark only means something on a `skipped` target: a stale one on a failed drawer does not clear it."""
    out, run = end(tmp_path, {"sdb": CLEAN, "sdc": {"state": "failed", "error": "x", "skip.chosen": ""}}, ROUND_END)
    held_until_press(out, run)
    assert problems(run)["sdc"][0] == "r", problems(run)


def test_the_gate_marks_the_drawer_only_for_an_explicit_skip(tmp_path):
    out, run = gate(tmp_path / "skip", "skip")
    assert (run / "targets/sda/state").read_text().strip() == "skipped", out.stdout + out.stderr
    assert (run / "targets/sda/skip.chosen").exists()


@pytest.mark.parametrize("name, alive", [("dead", False), ("silent", True)])
def test_no_answer_is_not_a_choice_and_leaves_no_mark(tmp_path, name, alive):
    out, run = gate(tmp_path / name, None, gui_alive=alive)
    assert (run / "targets/sda/state").read_text().strip() == "skipped", out.stdout + out.stderr
    assert not (run / "targets/sda/skip.chosen").exists()


def test_a_wipe_answer_writes_the_drawer_and_leaves_no_mark(tmp_path):
    out, run = gate(tmp_path, "wipe")
    assert (run / "out").read_text().split() == ["sda", "sdb"], out.stdout + out.stderr
    assert not (run / "targets/sda/skip.chosen").exists()
