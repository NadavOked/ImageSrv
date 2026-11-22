"""#3296: the drive memory of a chunk wave is learned from the multicast stream only -- not from the HTTP completion.

`drive_rates.record_wave` learned a drive's rate as `bytes_written / (first_moved_at -> moved_at)`. In a chunk wave
that window holds more than the drive's own writing. On 14/11 (LabRoot, ses_43eebdbc, 150m, two drawers):

- the Samsung (sdb) took 18254 of 18254 chunks of partition 3 in the first pass, which ended 20:15:10;
- its neighbour (sda) skipped 5978 of them ("the drive was behind") and completed them by HTTP until 20:26:30;
- partition 4 went out only after that, and the Samsung wrote it at 20:26-20:27 -- its `moved_at` is 20:27:27;
- so both were learned at 18,545,476,822 bytes / 1998 s = 9.28MB/s, held back under 150m (18.75MB/s). The engine's
  first passes ran 1252 s (`first_pass_s`). Two such samples and the drive memory sends a wave to the floor.

Since #3296 a chunk wave (`decision.engine = "chunk"`) records, for each drive, what it had durable when each first
pass ended (`ChunkEngine.rate_outcome.drives`, compressed bytes from the index's frame lengths) over `first_pass_s`,
against what those passes offered (`stream_zbytes` over the same seconds). A drive with no such number -- the first
passes not measured, the drive not in them, or most of it by HTTP -- is not learned, and says why (principle 5).
(#3173: udpcast is gone, and the guard that its waves did not move went with it.)

**Negative control:** `server/rounds/drive_rates.py`, `server/rounds/chunk_rate.py`, `server/rounds/chunksend.py` and
`server/console/journal_he.py` back to the base: the Samsung is learned at 9.28MB/s and the next wave drops to
`learned`; the 70% drive is learned over 1998 s; the drives with no stream measurement are learned anyway; the engine
has no `drives` in its outcome.
Nothing here sleeps.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
MAC = "78:ac:c0:9b:11:c2"
SAMSUNG, APS = "naa.5002538d42a58a2c", "t10.ATA APS-SL3N-512 SICSD40302WL"
WRITTEN = 18_545_476_822                 # bytes_written of each drawer of ses_43eebdbc (both done)
FIRST_PASS_S = 1252.4231001089938        # `first_pass_s` of ses_43eebdbc
CHUNKS = 18441


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drive(serial: str, model: str, port: int) -> dict:
    return {"serial": serial, "model": model, "mac": MAC, "port": port}


DRIVES = [_drive(APS, "APS-SL3N-512", 1), _drive(SAMSUNG, "SAMSUNG MZ7LN256", 2)]


def _chunk_wave(conn, sid: str) -> int:
    """A chunk wave goes out as the server sends one (`decide(adjust=chunk_rate.adjust)`), under a 150m ceiling."""
    return drive_rates.parse_bitrate(drive_rates.decide(
        conn, sid, lambda: DRIVES, "150m", adjust=lambda chosen, wave: chunk_rate.adjust(conn, chosen, wave)))


def _send_done(conn, sid: str, took: dict | None, stream_zbytes: int | None = WRITTEN):
    """What the engine says at `send_done` (`ChunkEngine.rate_outcome`), kept by `chunk_rate.record`."""
    chunk_rate.record(conn, sid, {"chunks": CHUNKS, "bytes": CHUNKS * 4 * 1024 * 1024, "first_pass_s": FIRST_PASS_S,
                                  "lost_chunks": 0, "away": 0, "cloners": 1, "repair_rounds": 1, "ended": "send_done",
                                  "stream_zbytes": stream_zbytes, "drives": took})


def _target(dev: str, serial: str) -> dict:
    # the counters of ses_43eebdbc: the window holds the HTTP completion of sda and the wait for it
    return {"dev": dev, "state": "done", "serial": serial, "bytes_written": WRITTEN,
            "first_moved_at": "2026-11-14T19:54:09+00:00", "moved_at": "2026-11-14T20:27:27+00:00"}


def _close(conn, sid: str) -> int:
    return drive_rates.record_wave(conn, sid, [{"mac": MAC, "targets_json": json.dumps(
        [_target("sda", APS), _target("sdb", SAMSUNG)])}])


def _rates(conn, sid: str) -> dict:
    return {r["serial"]: r for r in conn.execute("SELECT * FROM drive_rates WHERE session_id = ?", (sid,))}


def _stream(conn, sid: str) -> dict:
    return json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (sid,)).fetchone()["stream"])


def test_the_samsung_of_14_11_is_learned_from_the_stream_and_the_next_wave_does_not_drop(conn):
    """Two waves as ses_43eebdbc: the Samsung took everything in the first pass. Before #3296 it was held back twice
    (9.28MB/s < 80% of 18.75MB/s) and the next wave was `learned` at 66m."""
    for sid in ("ses_w1", "ses_w2"):
        assert _chunk_wave(conn, sid) == 150 * MB
        _send_done(conn, sid, {MAC: {SAMSUNG: WRITTEN, APS: int(WRITTEN * 0.7)}})
        _close(conn, sid)
        row = _rates(conn, sid)[SAMSUNG]
        assert row["rate_bps"] == int(WRITTEN / FIRST_PASS_S) and row["seconds"] == int(FIRST_PASS_S)
        assert row["rate_bps"] >= drive_rates.KEEP_UP * row["cap_bps"]           # it kept up with what was offered
    nxt = drive_rates.decide(conn, "ses_next", lambda: [DRIVES[1]], "150m")
    decision = drive_rates.decision(conn, "ses_next")
    assert decision["basis"] != "learned", decision
    assert drive_rates.parse_bitrate(nxt) == 150 * MB
    assert drive_rates.decision(conn, "ses_w2")["engine"] == "chunk"            # what told `record_wave` it was one


def test_a_drive_that_took_70_percent_from_the_stream_is_learned_at_its_stream_rate_not_the_average(conn):
    """The Issue's definition of done: 70% from the stream at X, 30% by HTTP after -- the sample is X, over the
    first passes' seconds, not the lower average over the whole window."""
    _chunk_wave(conn, "ses_w1")
    took = int(WRITTEN * 0.7)
    _send_done(conn, "ses_w1", {MAC: {SAMSUNG: WRITTEN, APS: took}})
    _close(conn, "ses_w1")
    row = _rates(conn, "ses_w1")[APS]
    assert row["rate_bps"] == int(took / FIRST_PASS_S) and row["bytes"] == took
    assert row["seconds"] == int(FIRST_PASS_S) and row["cap_bps"] == int(WRITTEN / FIRST_PASS_S)
    assert row["rate_bps"] > WRITTEN / 1998                                      # the window's average: 9.28MB/s


def test_a_drive_that_got_most_of_its_data_by_http_teaches_no_drop_and_says_why(conn):
    _chunk_wave(conn, "ses_w1")
    _send_done(conn, "ses_w1", {MAC: {SAMSUNG: WRITTEN, APS: int(WRITTEN * 0.3)}})
    assert _close(conn, "ses_w1") == 1                                            # the Samsung only
    assert set(_rates(conn, "ses_w1")) == {SAMSUNG}
    assert _stream(conn, "ses_w1")["drives_unmeasured"] == {APS: "http_majority"}
    (line,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'drive_rates_unmeasured'")]
    assert APS in line and "http_majority" in line
    (row,) = conn.execute("SELECT * FROM brain_decisions WHERE point = '#3296'").fetchall()
    assert row["verdict"] == "UNLEARNED" and row["action"] == "DRIVE_MEMORY" and row["reason"] == "http_majority"


def test_a_chunk_wave_closed_before_the_engine_said_how_it_ended_learns_nothing(conn):
    """No first-pass measurement (the room closed before `send_done`): "not measured" is not "slow"."""
    _chunk_wave(conn, "ses_w1")
    assert _close(conn, "ses_w1") == 0
    assert not _rates(conn, "ses_w1")
    assert _stream(conn, "ses_w1")["drives_unmeasured"] == {APS: "no_first_pass", SAMSUNG: "no_first_pass"}


def test_a_drive_missing_from_the_first_passes_or_sizes_not_known_is_not_learned(conn):
    _chunk_wave(conn, "ses_w1")
    _send_done(conn, "ses_w1", {MAC: {SAMSUNG: WRITTEN, APS: None}})            # APS: not in one partition's pass
    _close(conn, "ses_w1")
    assert set(_rates(conn, "ses_w1")) == {SAMSUNG}
    assert _stream(conn, "ses_w1")["drives_unmeasured"] == {APS: "not_in_first_pass"}
    _chunk_wave(conn, "ses_w2")
    _send_done(conn, "ses_w2", None, stream_zbytes=None)                          # the engine knew no sizes
    _close(conn, "ses_w2")
    assert not _rates(conn, "ses_w2")
    assert set(_stream(conn, "ses_w2")["drives_unmeasured"].values()) == {"no_first_pass"}
