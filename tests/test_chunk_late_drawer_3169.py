"""#3169 -- a drive that joined a chunk pass late (at 10%, 50% or 90%) is completed, never failed as slow.

The Issue asked whether `repair.py`'s first-pass gate ("less than half -> 'לא עמד בקצב'") fails a drive
that only came late. It did, before #3141/#3170: the gate counted what was missing. Since then a drive
is slow only on what its receiver said it skipped (`slow_reason`), and lateness skips nothing -- the
chunks sent before it listened never reached it. A late drive is completed like any other gap: by
HTTP when it alone lacks them, by a multicast repair when two machines do, and failed by name only
after `MAX_REPAIRS`.
"""

from __future__ import annotations

import pytest

from server.brain import repairrule
from server.rounds import repair

CHUNKS = 1000


def _joined_at(percent: int) -> list[tuple[int, int]]:
    """the ledger of a drive that started to listen at `percent` of the first pass, and kept everything after"""
    return [(CHUNKS * percent // 100, CHUNKS - 1)]


@pytest.mark.parametrize("percent", [10, 50, 90])
def test_a_late_drive_alone_fetches_what_it_missed_and_is_not_failed(percent):
    plan = repair.plan(CHUNKS, {"early": {"E1": [(0, CHUNKS - 1)]}, "late": {"L1": _joined_at(percent)}},
                       repairs_done=0)
    assert plan.failed == {}, plan.failed
    assert plan.multicast == [] and plan.fetch == {"late": [(0, CHUNKS * percent // 100 - 1)]}


@pytest.mark.parametrize("percent", [10, 50, 90])
def test_two_machines_late_together_get_one_multicast_repair(percent):
    plan = repair.plan(CHUNKS, {"a": {"A1": _joined_at(percent)}, "b": {"B1": _joined_at(percent)}},
                       repairs_done=0)
    assert plan.failed == {}, plan.failed
    assert plan.multicast == [(0, CHUNKS * percent // 100 - 1)] and plan.receive == {"a", "b"}


@pytest.mark.parametrize("percent", [10, 50, 90])
def test_a_late_drive_that_never_completes_fails_by_the_repair_ceiling_not_as_slow(percent):
    plan = repair.plan(CHUNKS, {"late": {"L1": _joined_at(percent)}}, repairs_done=repairrule.MAX_ROUNDS)
    why = plan.failed[("late", "L1")]
    assert f"אחרי {repairrule.MAX_ROUNDS} ניסיונות" in why and "לא עמד בקצב" not in why, why
