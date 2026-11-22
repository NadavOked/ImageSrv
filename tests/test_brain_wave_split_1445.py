"""#1445, #3064 (B48): one wave, or waves by the cloners' capacity -- the brain decides by itself, in shadow.

Nadav 15/11 on #1445: "why doesn't the brain understand by itself what is connected, and go by that?" No switch and no
question to the operator. A split is chosen only when the prediction shows a shorter round; cloners of one capacity
(the college: all 1Gb/s) stay one wave. The numbers in the pure tests are numbers for the test, not measurements: a
20GB image of 10,000 chunks, 1Gb/s cards at 900m (`LINK_CEILING_PER_MBPS`) and 10Gb/s cards at 9000m.

**Negative control:** with `server/app.py` back at #3353 (`0222c6353`) and `server/brain/wavesplit.py` removed, the test
marked (neg) fails on behaviour: the server writes no `B48/WAVE_SPLIT` row. The pure tests fail there on the import --
that is NOT a negative control, and they are not counted as one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory

from conftest import MANIFEST_256
from test_chunksend_2533 import CLONER1, _index_the_image, _room_wave, wait_for
from test_server_room import room_server  # noqa: F401 -- shared fixture

G1 = 900 * 10 ** 6
G10 = 10 * G1                                  # a round number this size reads as a distribution port to test_sender_ports (#156)
CHUNKS, ZBYTES = 10_000, 20 * 10 ** 9


def _rows(conn):
    return [d for d in memory.decisions(conn, rule="B48", limit=100) if d["action"] == "WAVE_SPLIT"]


def test_the_server_decides_one_wave_for_cloners_of_one_capacity_and_says_it_is_shadow(room_server):
    """(neg) The college case: one cloner, two drawers, its card not reported (1Gb/s assumed). One capacity -> SAME."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _index_the_image(ctx, chunks=10)
    wave, _hello = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    told = anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": "listening", "targets": [
            {"serial": "S1", "write_mbps": 400}, {"serial": "S2", "write_mbps": 400}]})
    assert told.status_code == 200 and told.json()["action"] == "receive", told.text
    assert wait_for(lambda: _rows(ctx.conn), 10)
    rows = _rows(ctx.conn)
    assert len(rows) == 1, rows                                         # once a wave
    row = rows[0]
    assert row["verdict"] == "SAME" and row["round_id"] == wave and row["mode"] == "shadow", row
    assert "remaining=0" in row["evidence"] and "waves_one=1" in row["evidence"], row["evidence"]
    assert any(e.startswith(f"cloner={CLONER1}:900Mb/s:assumed:2") for e in row["evidence"]), row["evidence"]
    assert "גל אחד" in row["outcome"] and "בצל" in row["reason"], row
    ctx.chunk.stop(wave)


# --- the pure decision -------------------------------------------------------------------------------------------------


def _fleet(fast: int, slow: int, slots: int = 3, slow_source: str = "reported"):
    from server.brain import wavesplit as W
    return ([W.Cloner(f"f{i}", G10, slots) for i in range(fast)]
            + [W.Cloner(f"s{i}", G1, slots, slow_source) for i in range(slow)])


def test_cloners_of_one_capacity_stay_one_wave():
    from server.brain import wavesplit as W
    p = W.plan(CHUNKS, ZBYTES, G10, _fleet(0, 6), remaining=20)
    assert p.verdict == W.SAME and p.split_s is None
    assert p.waves_one == 1 + 2                                         # 20 more disks in 18 drawers: two more waves
    assert p.one_s == pytest.approx(3 * ZBYTES * 8 / G1)


def test_a_server_that_is_the_ceiling_of_everyone_leaves_nothing_to_split():
    """A 10Gb/s cloner behind a 1Gb/s server is a 1Gb/s cloner: the threshold comes from the prediction, not a number."""
    from server.brain import wavesplit as W
    assert W.plan(CHUNKS, ZBYTES, G1, _fleet(2, 2), remaining=30).verdict == W.SAME


def test_a_round_of_one_wave_is_never_shorter_split():
    """The engine streams one wave at a time: the fast wave and then the slow one is longer than both together."""
    from server.brain import wavesplit as W
    p = W.plan(CHUNKS, ZBYTES, G10, _fleet(2, 1), remaining=0)
    assert p.verdict == W.ONE and p.split_s > p.one_s and p.waves_split == 2


def test_a_production_line_with_10g_beside_1g_is_shorter_split():
    """Six 10Gb/s cloners and one 1Gb/s cloner, 3 drawers each, 19 disks left after this wave: one wave is two waves at
    1Gb/s; the split is a fast wave, a slow wave, and two more fast waves."""
    from server.brain import wavesplit as W
    p = W.plan(CHUNKS, ZBYTES, G10, _fleet(6, 1), remaining=19)
    t1, t10 = ZBYTES * 8 / G1, ZBYTES * 8 / G10
    assert p.one_s == pytest.approx(2 * t1) and p.waves_one == 2
    assert p.split_s == pytest.approx(t10 + t1 + 2 * t10) and p.waves_split == 4
    assert p.verdict == W.SPLIT and p.fast == tuple(f"f{i}" for i in range(6))


def test_a_split_that_would_rest_on_an_assumed_speed_is_undecided_with_both_numbers():
    from server.brain import wavesplit as W
    p = W.plan(CHUNKS, ZBYTES, G10, _fleet(6, 1, slow_source="assumed"), remaining=19)
    assert p.verdict == W.UNDECIDED and p.split_s is not None and p.split_s < p.one_s


def test_a_slow_drive_completing_counts_in_its_wave():
    """The ETA of each wave is `waveeta.predict`, with the completion of a drive that was behind in a past wave."""
    from server.brain import waveeta, wavesplit as W
    slow = W.Cloner("s0", G1, 3, slow=(waveeta.Slow("APS", 20, waveeta.HTTP, "s0"),))
    p = W.plan(CHUNKS, ZBYTES, G10, [slow], remaining=0)
    assert p.one_s == pytest.approx(waveeta.predict(CHUNKS, ZBYTES, G1, list(slow.slow)).eta_s)
    assert p.one_s > ZBYTES * 8 / G1


def test_off_decides_nothing_and_a_failure_never_reaches_the_wave():
    from server.brain import wavesplit as W
    touched = []
    off = W.WaveSplit(None, image=lambda sid: touched.append(sid), mode=lambda: W.OFF)
    assert off.started("ses_1", 700, {}) == 700 and touched == []

    def boom(sid):
        raise RuntimeError("the image is gone")
    broken = W.WaveSplit(None, image=boom, mode=lambda: "shadow")
    assert broken.started("ses_1", 700, {}) == 700
