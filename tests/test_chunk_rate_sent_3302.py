"""#3302: the rate a chunk wave "sent" is what went on the wire -- the compressed frames -- not the chunks it covered.

`chunk_rate.sample_of` computed `sent_bps` from `rate_outcome["bytes"]`, which is `chunks * CHUNK_BYTES`: the chunks
before compression. `chunkcast send --kbps` paces the compressed frames (`agent/chunkcast.c`, `cc_pace` on the packets'
bytes), so `asked_bps` and that `sent_bps` were not the same unit, and `next_rate`'s "the engine kept its pace"
(`sent >= KEEP_UP * asked`) held for nearly every wave: ses_43eebdbc (LabRoot, 14/11) logged `sent=494Mb/s` at
`asked=150m`. The wire is `stream_zbytes` (#3296: the index's frame lengths), and without it `sent_bps` is None --
"not measured" is not "kept up" (principle 5).

The ratio used here (0.3) is a number for the test, NOT a measurement: the compression ratio of ses_43eebdbc's image
was not measured (#3302, "not verified").

**Negative control:** with `server/rounds/chunk_rate.py` back at the base, `sent_bps` is the uncompressed rate and all
four tests here fail on behaviour (a sent rate 3.3x the wire, `achieved_bps` where nothing was measured). The guards
that pass in both states are the AIMD tests of `test_chunk_rate_3163.py`, whose frames are as big as their chunks.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import chunk_rate, drive_rates

CHUNK = 4 * 1024 * 1024
CHUNKS = 5000                              # 21GB of chunks: long and big enough to teach (`MIN_SAMPLE_*`)
ASKED = 600 * 10 ** 6                      # `choose`'s start rate: the first wave with no history goes out at it
MACS = ("aa:bb:cc:00:33:01", "aa:bb:cc:00:33:02")


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives():
    return [{"serial": f"S{i}", "model": "EVO 870", "mac": mac, "port": i + 1} for i, mac in enumerate(MACS)]


def _outcome(*, wire_rate: float | None, zratio: float = 0.3) -> dict:
    """`rate_outcome` of a clean wave whose frames are `zratio` of its chunks, on the wire at `wire_rate` x ASKED.
    `wire_rate=None`: the engine knew no sizes (`stream_zbytes` None) -- the pass took as long as at the asked rate."""
    size = CHUNKS * CHUNK
    zbytes = int(size * zratio)
    seconds = zbytes * 8 / (ASKED * (wire_rate or 1.0))
    return {"chunks": CHUNKS, "bytes": size, "first_pass_s": seconds, "lost_chunks": 0, "away": 0,
            "cloners": len(MACS), "repair_rounds": 0, "ended": "send_done",
            "stream_zbytes": None if wire_rate is None else zbytes}


def _two_waves(conn, first: dict) -> tuple[str, dict]:
    """A wave that ends with `first`, then the next one as the server chooses it: (its rate, its decision)."""
    adjust = lambda chosen, wave: chunk_rate.adjust(conn, chosen, wave)       # noqa: E731
    assert drive_rates.decide(conn, "w1", _drives, None, adjust=adjust) == "600m"
    chunk_rate.record(conn, "w1", first)
    rate = drive_rates.decide(conn, "w2", _drives, None, adjust=adjust)
    return rate, drive_rates.decision(conn, "w2")


def _sample(conn, sid="w1") -> dict:
    return json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                                   (sid,)).fetchone()["stream"])["chunk"]


def test_a_wave_that_put_half_the_asked_rate_on_the_wire_holds_though_its_chunks_were_more(conn):
    """(neg) The frames went out at 50% of the asked rate; the chunks they cover are 1.67x it. Not kept up: hold."""
    rate, decision = _two_waves(conn, _outcome(wire_rate=0.5))
    sample = _sample(conn)
    assert sample["sent_bps"] == pytest.approx(ASKED * 0.5, rel=1e-3), sample
    assert sample["sent_bps"] < drive_rates.KEEP_UP * ASKED
    assert decision["basis"] == "chunk_hold" and rate == "600m", decision


def test_the_journal_says_the_rate_on_the_wire(conn):
    """(neg) `chunk_rate_recorded` prints the compressed rate (300Mb/s), not the chunks' (1000Mb/s)."""
    _two_waves(conn, _outcome(wire_rate=0.5))
    (line,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'chunk_rate_recorded'")]
    assert " sent=300Mb/s " in line, line


def test_sizes_not_known_is_not_measured_and_holds(conn):
    """(neg) `stream_zbytes` None: `sent_bps` and `achieved_bps` None, and the next wave holds -- no fallback to
    the uncompressed bytes."""
    rate, decision = _two_waves(conn, _outcome(wire_rate=None))
    sample = _sample(conn)
    assert sample["sent_bps"] is None and sample["achieved_bps"] is None, sample
    assert sample["bytes"] == CHUNKS * CHUNK                  # still kept: `too_short` and the journal read it
    assert decision["basis"] == "chunk_hold" and rate == "600m", decision


def test_a_wave_that_kept_up_on_the_wire_still_climbs(conn):
    """(neg) Against an over-broad fix: frames at 90% of the asked rate are kept up -- one step up, and the sample
    says 540Mb/s, not the chunks' 1800."""
    rate, decision = _two_waves(conn, _outcome(wire_rate=0.9))
    sample = _sample(conn)
    assert sample["sent_bps"] == pytest.approx(ASKED * 0.9, rel=1e-3)
    assert sample["achieved_bps"] == sample["sent_bps"]       # nothing lost
    assert decision["basis"] == "chunk_up" and rate == "700m", decision
