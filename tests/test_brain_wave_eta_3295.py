"""#3295 (option ב'), #3064 (K3): the ETA of a chunk wave whose slow drive misses chunks and completes them -- and the
actual time, as numbers, so the match can be measured (#3321).

Nadav 15/11: the stream goes at the pace of the fast drives; a drive that was behind in a past wave misses chunks and
completes them afterwards, from the drawer next to it (neighborfill) or over HTTP. What it sustained
(`chunk_rate.write_history`) is for the prediction only. The numbers in the pure tests: the APS of #3034 (wrote ~61MB/s,
`test_write_rate_3295.py`), `fill.CEILING_BPS` (100MiB/s, M1/M2 on metal 14/11) and KT4 (42 chunks/s); the image sizes
are numbers for the test, not measurements.

**Negative control:** with `server/app.py` back at #3316 (`c359a21bb`) and `server/brain/waveeta.py` removed, the test
marked (neg) fails on behaviour: the server writes no `WAVE_ETA` row (it does not import the new module). The tests
that import `waveeta` fail there on the import -- that is NOT a negative control, and they are not counted as one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory

from conftest import MANIFEST_256
from test_chunksend_2533 import CLONER1, _index_the_image, _room_wave, wait_for
from test_server_room import room_server  # noqa: F401 -- shared fixture
from test_write_rate_3295 import _earlier

MB = 10 ** 6
CHUNK = 4 * 1024 * 1024


def _rows(conn, action):
    return [d for d in memory.decisions(conn, rule="K3", limit=100) if d["action"] == action]


def test_the_server_predicts_the_wave_with_its_slow_drive_completing_from_the_drawer_next_to_it(room_server):
    """(neg) S2 was behind in a past wave (61MB/s); S1, on the same cloner, was not. The wave's prediction says so."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _earlier(ctx.conn, "ses_earlier", {"S2": {"bytes": 4503 * CHUNK, "seconds": 306.0}})
    _index_the_image(ctx, chunks=10)
    wave, _hello = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    told = anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": "listening", "targets": [
            {"serial": "S1", "write_mbps": 400}, {"serial": "S2", "write_mbps": 209}]})
    assert told.status_code == 200 and told.json()["action"] == "receive", told.text
    assert wait_for(lambda: _rows(ctx.conn, "WAVE_ETA"), 10)
    row = _rows(ctx.conn, "WAVE_ETA")[0]
    assert row["verdict"] == "PREDICT" and row["round_id"] == wave, row
    ev = row["evidence"]
    assert "behind=1" in ev and "drives=2" in ev, ev
    assert any(e.startswith("drive=S2:61MB/s:neighbor:") for e in ev), ev
    assert any(e.startswith("eta_s=") for e in ev) and any(e.startswith("t1_s=") for e in ev)
    ctx.chunk.stop(wave)
    assert wait_for(lambda: (_rows(ctx.conn, "WAVE_ETA")[0]["outcome"] or "").find("לא נמדד") >= 0)
    assert _rows(ctx.conn, "WAVE_ETA_ACTUAL") == []                     # a stopped wave has no actual ETA


# --- the pure prediction -----------------------------------------------------------------------------------------------


def test_a_wave_with_no_slow_drive_is_its_first_pass_at_the_stream_rate():
    from server.brain import waveeta as W
    f = W.predict(10_000, 20 * 10 ** 9, 700 * MB, [])
    assert f.eta_s == pytest.approx(20e9 * 8 / 700e6) and f.t1_s == f.eta_s and f.drives == ()


def test_a_slow_drive_writes_the_whole_image_at_its_own_pace_when_the_source_is_faster():
    """The APS (61MB/s) behind 100MiB/s from the drawer next to it, or behind HTTP at 42 x 4MiB/s: the drive is the
    bottleneck either way -- the image's 10,000 chunks at 61MB/s."""
    from server.brain import waveeta as W
    for source in (W.NEIGHBOR, W.HTTP):
        f = W.predict(10_000, 20 * 10 ** 9, 700 * MB, [W.Slow("APS", 61, source)])
        assert f.eta_s == pytest.approx(10_000 * CHUNK / 61e6), source
        assert f.eta_s > f.t1_s


def test_a_drive_faster_than_its_source_is_held_by_the_source():
    """150MB/s from the drawer next to it (100MiB/s): the first pass at its own pace, the rest at the source's."""
    from server.brain import waveeta as W
    t1 = 20e9 * 8 / 700e6
    f = W.predict(10_000, 20 * 10 ** 9, 700 * MB, [W.Slow("D", 150, W.NEIGHBOR)])
    want = t1 + (10_000 * CHUNK - 150e6 * t1) / (100 * 1024 * 1024)
    assert f.drives[0][3] == pytest.approx(want) and f.eta_s == pytest.approx(want)


def test_a_drive_that_keeps_up_in_the_first_pass_adds_nothing():
    from server.brain import waveeta as W
    f = W.predict(1_000, 20 * 10 ** 9, 700 * MB, [W.Slow("D", 300, W.HTTP)])
    assert f.eta_s == pytest.approx(f.t1_s)


# --- the record: prediction and actual, as numbers ---------------------------------------------------------------


class FakeChunk:
    no_file = None

    def __init__(self, first_pass_s=300.0):
        self.first_pass_s = first_pass_s

    def rate_outcome(self, session_id):
        return {"first_pass_s": self.first_pass_s}


class Lib:
    def __init__(self, manifest):
        self.manifest = manifest

    def get(self, image_id):
        return self.manifest


@pytest.fixture()
def conn(tmp_path):
    from server import db
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at) VALUES ('W-1', 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('W-1', 'a', 'writing', 'now')")
    c.commit()
    return c


# 10,000 chunks in all: the small partition is a tenth, the big one the rest. Written as a difference, because a
# round number of that size in a test file reads as a distribution port to `test_sender_ports` (#156).
ALL_CHUNKS, SMALL_CHUNKS = 10_000, 1_000
MANIFEST = {"total_compressed_bytes": 20 * 10 ** 9,
            "partitions": [{"index": 1, "file": "p1", "readback_chunks": SMALL_CHUNKS},
                           {"index": 3, "file": "p3", "readback_chunks": ALL_CHUNKS - SMALL_CHUNKS}]}


def _eta(conn, clock, chunk=None, manifest=MANIFEST):
    from server.brain import waveeta as W
    return W.WaveEta(conn, library=Lib(manifest), chunk=chunk or FakeChunk(), mode=lambda: "shadow",
                     clock=lambda: clock[0])


def test_the_end_of_the_wave_writes_the_actual_next_to_the_prediction(conn):
    clock = [100.0]
    eta = _eta(conn, clock)
    eta.on_event("chunk_write_behind", 'W-1 "APS" check=209MB/s wrote=61MB/s in=ses_x left_out', "W-1")
    probes = {("a", "FAST"): 400, ("a", "APS"): 209}     # before #3327 the slow drive is in the write checks too
    assert eta.started("W-1", 700 * MB, probes) == 700 * MB                     # the rate is never changed
    predicted = _rows(conn, "WAVE_ETA")[0]
    assert "drive=APS:61MB/s:neighbor:688s" in predicted["evidence"], predicted["evidence"]
    clock[0] = 100.0 + 900
    eta.on_event("send_done", "W-1 {}", "W-1")
    actual = _rows(conn, "WAVE_ETA_ACTUAL")[0]
    assert {"predicted_s=688", "actual_s=900", "ratio=1.31", "t1_predicted_s=229", "t1_actual_s=300"} <= set(
        actual["evidence"]), actual["evidence"]
    assert "בפועל 900s" in _rows(conn, "WAVE_ETA")[0]["outcome"]


def test_a_slow_drive_alone_on_its_cloner_completes_over_http(conn):
    eta = _eta(conn, [0.0])
    eta.on_event("chunk_write_behind", 'W-1 "APS" check=209MB/s wrote=61MB/s in=ses_x left_out', "W-1")
    eta.started("W-1", 700 * MB, {})                                           # no fast drawer on any cloner
    assert any(e.startswith("drive=APS:61MB/s:http:") for e in _rows(conn, "WAVE_ETA")[0]["evidence"])


@pytest.mark.parametrize("manifest, bits, why", [
    ({"total_compressed_bytes": 10, "partitions": [{"file": "p", "readback_chunks": None}]}, 700 * MB, "מספר החתיכות"),
    ({"partitions": [{"file": "p", "readback_chunks": 5}]}, 700 * MB, "הגודל הדחוס"),
    (MANIFEST, 0, "קצב הזרם"),
])
def test_what_was_not_measured_is_no_prediction_not_a_zero(conn, manifest, bits, why):
    eta = _eta(conn, [0.0], manifest=manifest)
    eta.started("W-1", bits, {})
    row = _rows(conn, "WAVE_ETA")[0]
    assert row["verdict"] == "UNPREDICTED" and why in row["reason"], row
    eta.on_event("send_done", "", "W-1")
    assert _rows(conn, "WAVE_ETA_ACTUAL") == []


def test_off_predicts_nothing_and_nothing_raises(conn):
    from server.brain import waveeta as W
    eta = W.WaveEta(conn, library=Lib(MANIFEST), chunk=FakeChunk(), mode=lambda: "off")
    assert eta.started("W-1", 700 * MB, {}) == 700 * MB and _rows(conn, "WAVE_ETA") == []
    broken = W.WaveEta(conn, library=None, chunk=None, mode=lambda: 1 / 0)
    assert broken.started("W-1", 5, {}) == 5
    broken.on_event("send_done", None, "W-1")
