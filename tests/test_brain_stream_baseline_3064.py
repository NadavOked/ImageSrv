"""#3064 (X10): the baseline of "the stream was hit" -- chunks abandoned in a clean pass -- and each chunk wave judged
against it, in the brain's record. Shadow: recorded only.

The measurement is #3164's (`server/learn/netloss.py`: abandoned chunks a GB, against the median of like waves); the
waves here are #3164's simulated ones (`test_chunk_netloss_3164._wave`), at a known rate. The starting value (716 a
machine, research-3064 1ד, metal 13/11) judges the first waves, before there is a learned baseline.

**Negative control:** with `server/learn/netloss.py` back at the base (`9933c5dc1`) and `server/brain/streamhit.py`
removed, the tests marked (neg) fail on behaviour: the wave is evaluated and no `X10` row is written. The test that
imports `streamhit` fails there on the import -- that is NOT a negative control.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import memory, veto
from server.learn import netloss as learn_netloss

from test_chunk_netloss_3164 import MAC1, MAC2, RECEIVED_GB, _base, _wave


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    yield db.connect(tmp_path / "imagesrv.db")
    veto.forget()


def _rows(conn, sid=None):
    return [d for d in memory.decisions(conn, rule="X10", limit=100)
            if d["action"] == "STREAM_HIT" and (sid is None or d["round_id"] == sid)]


def test_a_wave_above_the_learned_baseline_is_recorded_as_a_hit(conn):
    """(neg) Three calm waves (median 0.125/GB), then one at 2/GB (above #3164's floor of 1/GB): #3164 says jump, and
    the brain records HIT."""
    _base(conn)
    sid = _wave(conn, 2.0)
    learn_netloss.evaluate_chunk(conn, sid)
    (row,) = _rows(conn, sid)
    assert row["verdict"] == "HIT" and "source=learned" in row["evidence"], row
    assert "baseline_per_gb=0.125" in row["evidence"] and "state=jump" in row["evidence"]
    assert row["outcome"] and "צל" in row["outcome"]


def test_a_wave_within_the_learned_baseline_is_clean(conn):
    """(neg)"""
    _base(conn)
    sid = _wave(conn, 0.12)
    learn_netloss.evaluate_chunk(conn, sid)
    assert [r["verdict"] for r in _rows(conn, sid)] == ["CLEAN"]


def test_with_no_learned_baseline_yet_the_starting_value_judges(conn):
    """(neg) The first wave: 716 a machine is the starting value. 40 GB x 20/GB = 800 a machine -- a hit; 40 x 10 =
    400 -- clean."""
    hit = _wave(conn, 20.0)
    learn_netloss.evaluate_chunk(conn, hit)
    clean = _wave(conn, 10.0, machines=(MAC1, MAC2))
    learn_netloss.evaluate_chunk(conn, clean)
    (h,), (c,) = _rows(conn, hit), _rows(conn, clean)
    assert h["verdict"] == "HIT" and "source=start" in h["evidence"] and "start_chunks=716" in h["evidence"], h
    assert c["verdict"] == "CLEAN" and "machines=2" in c["evidence"], c
    assert 20 * RECEIVED_GB > 716 > 10 * RECEIVED_GB


def test_a_wave_with_a_disconnect_is_not_judged_on_the_stream(conn):
    """A net gap (#3141) is not the stream's fault: NOT_CLEAN, and no threshold."""
    _base(conn)
    sid = _wave(conn, 5.0, gap=True)
    learn_netloss.evaluate_chunk(conn, sid)
    (row,) = _rows(conn, sid)
    assert row["verdict"] == "NOT_CLEAN" and "source=none" in row["evidence"], row


def test_a_wave_that_was_not_measured_has_no_row_not_a_clean_one(conn):
    """Principle 5: an unread `abandoned` makes the wave no measurement -- no row, never CLEAN."""
    sid = _wave(conn, None)
    learn_netloss.evaluate_chunk(conn, sid)
    assert _rows(conn, sid) == []


def test_two_evaluations_of_one_wave_are_one_row(conn):
    """The engine's end and the room closing the wave both evaluate it."""
    sid = _wave(conn, 1.0)
    learn_netloss.evaluate_chunk(conn, sid)
    learn_netloss.evaluate_chunk(conn, sid)
    assert len(_rows(conn, sid)) == 1


def test_the_brain_switched_off_records_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    sid = _wave(conn, 20.0)
    learn_netloss.evaluate_chunk(conn, sid)
    assert _rows(conn) == []


def test_the_starting_value_is_the_measured_top_of_a_clean_pass():
    from server.brain import streamhit
    assert streamhit.START_CHUNKS == 716
