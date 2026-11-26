"""A `bad_chunks` list with an entry outside the partition is discarded -- and said, never silently (principle 5)."""

import logging

from test_chunksend_2533 import SID, rig, wait_for  # noqa: F401  (rig is a fixture)


def _ended_with(rig, mac, bad):
    rig.engine.report(SID, mac, {"part": 3, "pass": 1, "state": "ended", "bad_chunks": bad, "targets": [
        {"serial": mac, "skipped": 0, "durable": [{"part": 3, "ranges": [[0, 4], [6, 9]]}]}]})


def _start(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    return engine


def test_out_of_range_entry_is_discarded_with_a_warning(rig, caplog):
    engine = _start(rig)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        _ended_with(rig, "a", [5, 10])                       # the partition has 10 chunks: 10 does not exist
    assert engine._wave.marks[3, "a"].bad_chunks is None     # untrusted: not used as evidence (same in both states)
    said = [r.getMessage() for r in caplog.records if "bad_chunks" in r.getMessage()]
    assert said and "a" in said[0] and "p3" in said[0], caplog.text


def test_a_valid_list_is_kept_and_not_warned_about(rig, caplog):
    engine = _start(rig)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        _ended_with(rig, "a", [5, 9])
    assert engine._wave.marks[3, "a"].bad_chunks == frozenset({5, 9})
    assert not [r for r in caplog.records if "bad_chunks" in r.getMessage()]
