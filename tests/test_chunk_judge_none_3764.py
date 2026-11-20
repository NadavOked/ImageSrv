"""#3764: `POST /agent/chunk-pass` החזיר 500 (`'NoneType' object has no attribute 'items'` ב-`_execute`).

`_judge_drawers` החזיר `return` חשוף (None) כשאין הורדה של המחשב במחיצה, או שהגל ב-`failed`/`stopped`, וכל שאר המסלולים
מחזירים `{}`. `report` העביר את ה-None ל-`_execute`, שקרא `verdicts.items()`. הדיווח נכשל, והדיווח הבא קיבל 200.

**בקרה שלילית:** עם `server/rounds/chunksend.py` של הענף שלפני התיקון, שלושת הטסטים המסומנים (neg) נופלים בחריגה.
"""

from __future__ import annotations

import logging
import time

import pytest

pytest.importorskip("fastapi")

from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_chunksend_2533 import _one_machine_left_to_fetch
from test_chunk_drawer_hold_2023 import _fetching_c

WAIT = {"ok": True, "action": "wait"}


def _engine_with_hold_on(rig):
    engine = _one_machine_left_to_fetch(rig)
    engine.drawer_hold = lambda **kw: {}              # the setting is on: `report` goes to `_judge_drawers`
    assert wait_for(lambda: (3, "c") in engine._wave.fetching)
    return engine


def test_a_fetching_report_with_no_download_of_the_machine_in_the_partition_is_answered_not_a_500(rig):
    """(neg) `track is None`: the machine's download is not in the wave (it ended, or never started here)."""
    engine = _engine_with_hold_on(rig)
    with engine._lock:
        engine._wave.fetching.pop((3, "c"))
    assert engine._judge_drawers(SID, "c", {"part": 3, "pass": 2, "state": "fetching"}) == {}
    assert engine._execute(SID, "c", 3, engine._judge_drawers(SID, "c", {"part": 3, "pass": 2, "state": "fetching"}), WAIT) == WAIT


@pytest.mark.parametrize("state", ["failed", "stopped"])
def test_a_fetching_report_of_a_wave_that_is_failed_or_stopped_is_answered_not_a_500(rig, state):
    """(neg) The wave is over (`failed` / `stopped`) while the agent still reports `fetching`: nothing to judge, and no exception."""
    engine = _engine_with_hold_on(rig)
    with engine._lock:
        engine._wave.state = state
    assert engine._judge_drawers(SID, "c", {"part": 3, "pass": 2, "state": "fetching"}) == {}
    got = _fetching_c(rig)                              # through the real `report`
    assert isinstance(got, dict) and got.get("ok") in (True, False), got


def test_execute_given_verdicts_that_are_not_a_dict_says_so_in_the_log_and_leaves_the_answer_as_it_was(rig, caplog):
    """(neg) Not swallowed in silence (principle 5): a WARNING names the function, and the answer to the agent is unchanged."""
    engine = _engine_with_hold_on(rig)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        assert engine._execute(SID, "c", 3, None, WAIT) == WAIT
    assert any("_execute" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records), caplog.text


def test_a_judged_fetch_is_still_answered_as_before(rig):
    """The guard (passes in both states): a live download, a brain that says nothing -- the plain `wait`."""
    engine = _engine_with_hold_on(rig)
    time.sleep(0.1)
    assert _fetching_c(rig) == WAIT
    assert engine._judge_drawers(SID, "c", {"part": 3, "pass": 2, "state": "fetching"}) == {}
