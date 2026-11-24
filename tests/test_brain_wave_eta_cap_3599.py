"""#3599 (K3, `server/brain/waveeta.py`): the prediction of the first pass knows the ceiling of the engine's own feedback.

J1 (metal 17/11, `lab/metal-1117-night/runs/J1`): the write check of the slowest counted drive said 125MB/s, `--write-kbps` was
896000 (x 0.9, the drive floor was 74MB/s) and `--rate-max` 1,792,000 kbit/s of UNCOMPRESSED data (`chunksend.feedback_bounds`, 2 x the
start). The image is 18,441 chunks of 4MiB (77.35GB) and 18,545,444,861 compressed bytes, so the wire carries 24% of that rate: ~430Mb/s.
The sender sat on that ceiling 94.5% of the ticks; K3 predicted the first pass at 647Mb/s (what the previous wave achieved) = 229 s and it
took 378 s (`t1_actual_s`). The numbers below are those of that wave; the prediction is what changes, not the stream (the ceiling policy
of the feedback is a separate decision of Nadav, #3599).

**Negative control:** `server/brain/waveeta.py` and `server/rounds/chunksend.py` back at the base (`253e5159e`): every test marked (neg) fails
on behaviour -- `t1_s=166` instead of 345 and no `rate_cap_mbps` / `t1_cap_bound` in the evidence. The tests marked (guard) pass in both states.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory
from server.brain import waveeta as W
from server.rounds import chunksend
from server.rounds.chunksend import ChunkEngine, feedback_bounds

BIG_FILE = 100 << 20     # #4003: a test file bigger than this is a real write on Windows
CHUNKS, ZBYTES = 18_441, 18_545_444_861             # J1 `wave_rates.stream.chunk` (chunks, stream_zbytes)
WHOLE = CHUNKS * W.CHUNK_BYTES                       # 77,347,160,064 -- the same figure as `stream.chunk.bytes`
FLOOR = 74                                           # MB/s, `writecheck_min_mbps` of J1 (agentlog-cloner1.txt)
PROBES = {("a0:48", "naa.5002538d00a68927"): 209, ("a0:48", "naa.50025388a07a5842"): 125}
ASKED = 892_000_000                                  # J1 `--kbps 892000`
LEARNED = 1_000_000                                  # kbit/s, under 2 x the start: K8 knows the drives' maximum, so the ceiling cannot grow (#3616)


class Lib:
    def get(self, image_id):
        return {"total_compressed_bytes": ZBYTES, "partitions": [{"index": 3, "file": "p3", "readback_chunks": CHUNKS}]}


class Chunk:
    no_file = None

    def __init__(self, floor=lambda: FLOOR, learned=None):
        if floor is not None:
            self.write_floor = floor
        if learned is not None:                       # K8's learned maximum for the drives (`ChunkEngine.write_ceiling`)
            self.write_ceiling = lambda serials: learned

    def rate_outcome(self, session_id):
        return {}


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at) VALUES ('W-1', 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('W-1', 'a', 'writing', 'now')")
    c.commit()
    return c


def _evidence(conn, chunk, bits=ASKED, probes=PROBES):
    eta = W.WaveEta(conn, library=Lib(), chunk=chunk, mode=lambda: "shadow")
    assert eta.started("W-1", bits, dict(probes)) == bits                      # the rate itself is never changed
    rows = [d for d in memory.decisions(conn, rule="K3", limit=20) if d["action"] == "WAVE_ETA"]
    assert len(rows) == 1 and rows[0]["verdict"] == "PREDICT", rows
    return {e.split("=", 1)[0]: e.split("=", 1)[1] for e in rows[0]["evidence"] if "=" in e}, rows[0]


# --- the one place that reads the write check -----------------------------------------------------------------------------


def test_the_write_check_start_is_the_slowest_probe_times_the_margin_never_under_the_floor():
    assert chunksend.probe_write_kbps([209, 125], FLOOR) == 112 * 8000            # J1: 125 x 0.9 = 112MB/s = --write-kbps 896000
    assert chunksend.probe_write_kbps([60], FLOOR) == FLOOR * 8000                # under the floor: the floor
    assert chunksend.probe_write_kbps([209, None], FLOOR) == FLOOR * 8000         # a probe that was not measured: the floor, not the other
    assert chunksend.probe_write_kbps([], FLOOR) == FLOOR * 8000


def test_k3_and_the_command_read_one_rate_max(conn):
    """The ceiling K3 predicts with is the `--rate-max` the engine puts on the command line for the same probes."""
    write_kbps = chunksend.probe_write_kbps(list(PROBES.values()), FLOOR)
    engine = SimpleNamespace(port=7713, interface=None, source=None, feedback=True, write_floor=lambda: FLOOR)   # פורט-הפצה-מכוון
    cmd = ChunkEngine._command(engine, SimpleNamespace(key="0123456789abcdef", round=1), {"index": 3}, Path("image.zst"),
                               892_000, write_kbps, None, None)
    rate_max = int(cmd[cmd.index("--rate-max") + 1])
    assert rate_max == feedback_bounds(892_000, write_kbps, FLOOR)[1] == 1_792_000
    eta = W.WaveEta(conn, library=Lib(), chunk=Chunk(learned=LEARNED), mode=lambda: "shadow")
    assert eta._rate_cap(PROBES, ASKED, CHUNKS, ZBYTES) == (int(rate_max * 1000 * ZBYTES / WHOLE), "ceiling")


def test_k3_reads_the_rate_cap_the_command_carries_when_the_ceiling_can_grow(conn, tmp_path, monkeypatch):
    """(neg) #3616: with no learned maximum the sender may grow `--rate-max` up to `--rate-cap` (the wire's capacity as a rate of data). K3 takes
    the cap from the same `feedback_grow_cap`: the number on the command line, as wire bps -- which is the asked rate, so it binds nothing."""
    path = tmp_path / "p3.zst"
    monkeypatch.setattr(chunksend, "_size_of", lambda p: ZBYTES)   # #4003: truncate(18GB) is a real 18GB write on Windows
    write_kbps = chunksend.probe_write_kbps(list(PROBES.values()), FLOOR)
    engine = SimpleNamespace(port=7713, interface=None, source=None, feedback=True, write_floor=lambda: FLOOR)   # פורט-הפצה-מכוון
    wave = SimpleNamespace(key="0123456789abcdef", round=1, part_chunks={3: CHUNKS})
    cmd = ChunkEngine._command(engine, wave, {"index": 3}, path, ASKED // 1000, write_kbps, None, None)
    rate_cap = int(cmd[cmd.index("--rate-cap") + 1])
    # #4003 guard: no test file of this size may exist (a real write on Windows)
    assert not [q for q in tmp_path.rglob("*") if q.is_file() and q.stat().st_size > BIG_FILE]
    eta = W.WaveEta(conn, library=Lib(), chunk=Chunk(), mode=lambda: "shadow")
    assert eta._rate_cap(PROBES, ASKED, CHUNKS, ZBYTES) == (int(rate_cap * 1000 * ZBYTES / WHOLE), "grow")


# --- the prediction -------------------------------------------------------------------------------------------------------


def test_the_first_pass_is_predicted_at_the_feedback_ceiling_when_it_cannot_grow(conn):
    """J1: asked 892Mb/s, ceiling 224MB/s of data = 430Mb/s on the wire. T1 = 77.35GB / 224MB/s = 345 s (it was 378), not 166. With a learned
    maximum for the drives the ceiling cannot grow (#3616): `--rate-max` is the most."""
    ev, row = _evidence(conn, Chunk(learned=LEARNED))
    assert ev.get("rate_cap_basis") == "ceiling", ev
    assert ev["t1_s"] == "345" and ev["stream_mbps"] == "430" and ev["asked_mbps"] == "892", ev
    assert ev.get("rate_cap_mbps") == "430" and ev.get("t1_cap_bound") == "1", ev
    assert "תקרת המשוב" in row["reason"] and "430Mb/s" in row["reason"], row["reason"]


def test_a_stream_asked_below_the_ceiling_is_not_bound_by_it(conn):
    """(neg: the evidence only) 300Mb/s asked is under the 430Mb/s ceiling: T1 stays the asked one -- the cap never raises a prediction."""
    ev, row = _evidence(conn, Chunk(learned=LEARNED), bits=300_000_000)
    assert ev["t1_s"] == "495" and ev["stream_mbps"] == "300", ev                  # 18.55GB x 8 / 300Mb/s, in both states
    assert ev.get("t1_cap_bound") == "0" and ev.get("rate_cap_mbps") == "430", ev
    assert "תקרת המשוב" not in row["reason"]


def test_the_measurement_lock_is_the_start_the_cap_is_read_from(conn, monkeypatch):
    """(neg) `IMAGESRV_MEASURE_WRITE_KBPS` replaces the write check in the engine (`_write_base`); the cap follows it, not the probes."""
    monkeypatch.setenv(chunksend.MEASURE_WRITE_KBPS_ENV, "1000000")             # --write-kbps 1,000,000 -> --rate-max 2,000,000
    ev, _row = _evidence(conn, Chunk(learned=LEARNED))
    assert ev["t1_s"] == "309", ev                                                 # 77.35GB / 250MB/s
    assert ev.get("rate_cap_mbps") == "480" and ev.get("t1_cap_bound") == "1", ev


def test_a_ceiling_that_can_grow_binds_nothing_and_the_prediction_is_the_asked_rate(conn):
    """(neg) #3616 (the coordinator, 17/11): no learned maximum -> the sender widens `--rate-max` 10% at a time up to the wire. K3 no longer
    says the stream sits at 2 x the start: T1 is the asked rate's (166 s), `t1_cap_bound=0`, and the basis says why."""
    ev, row = _evidence(conn, Chunk())
    assert ev["t1_s"] == "166" and ev["stream_mbps"] == "892", ev
    assert ev.get("rate_cap_basis") == "grow" and ev.get("t1_cap_bound") == "0", ev
    assert "תקרת המשוב" not in row["reason"], row["reason"]


@pytest.mark.parametrize("chunk", [Chunk(floor=None), Chunk(floor=lambda: 1 / 0)])
def test_a_cap_that_cannot_be_read_is_unmeasured_and_the_prediction_goes_on_as_before(conn, chunk):
    """(guard on t1) A double without a drive floor, or a floor that fails: the cap is not invented, and the prediction is still recorded."""
    ev, _row = _evidence(conn, chunk)
    assert ev["t1_s"] == "166" and ev["stream_mbps"] == "892", ev                  # 18.55GB x 8 / 892Mb/s: the asked rate, as before
