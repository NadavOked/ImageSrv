"""#3549 (K16, `learn/eta.py`) and #3550 (K3, `waveeta`): the forecasts against the real waves of the metal runs.

Calibration of 16/11 (`lab/calibration-1116.md` section 3): ONE cloner, ONE Samsung MZ7LN256 drive, ONE image (18,545,476,822
compressed bytes, 18441 chunks), 669Mb/s asked in every wave. Two series: r14 (file order; every few waves a different engine
setting, so the rate moves between 327 and 442Mb/s) and r16 (mixed). The numbers below are copied from `db-extra.json` of
every run (`wave_rates.stream`, `delivered`, and the `K3/WAVE_ETA_ACTUAL` and `K16/TIME_FORECAST` rows), in the order the
runs went; tr does not see the lab folder, so they live here as constants.

What is NOT proved by this file: one machine, one drive, one image. The 6-cloner run has not finished. And the history of
the first wave of a series is only what the series itself gave (the server on metal also had the earlier series' waves).

**Negative control (bakara-shlilit):** with `server/learn/eta.py` and `server/brain/waveeta.py` both back at the base, the
MAPE tests fail on behaviour -- r14/r16 K3 predicts 222s in every wave (MAPE 36-46%), and K16 keeps no chunk wave in its
history, so r14's MAPE is 11.7% and the first chunk waves of both series have no estimate at all.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory
from server.brain import waveeta as W
from server.learn import eta as learn_eta

MAC = "78:ac:c0:9b:11:c2"
SERIAL = "naa.5002538d00a68927"    # #3640: K3 takes the rate of an earlier wave only from a wave on the same drives
IMAGE = 18_545_476_822            # `total_compressed_bytes` = `written_bytes` of every wave below
CHUNKS = 18_441
ASKED = 669_000_000
MAPE_LIMIT = 10.0                 # percent: the acceptance of #3549 / #3550, per series

# (name, engine, session seconds `K16.actual`, first_pass_s / wall_s, achieved_bps, K3 actual_s)
# engine "chunk": wall = `stream.chunk.first_pass_s`; "udp": wall = `stream.wall_s`. Sorted by the time the wave opened.
R14 = [
    ("B0-1", "chunk", 453, 392.4933827640489, 377_941_207, 414),
    ("B0-2", "chunk", 457, 392.46075932195527, 377_870_124, 413),
    ("O0-1", "udp", 417, 374.2, None, None),
    ("O0-2", "udp", 414, 374.6, None, None),
    ("A4-1", "chunk", 483, 418.9137104579713, 354_124_165, 451),
    ("A4-2", "chunk", 471, 419.43417302303715, 353_454_568, 437),
    ("A16-1", "chunk", 542, 453.7453725350206, 326_922_172, 471),
    ("A16-2", "chunk", 512, 454.11528597411234, 326_638_151, 475),
    ("MX-1", "chunk", 386, 335.81602769804886, 441_752_197, 351),
    ("MX-2", "chunk", 378, 335.67101193493, 441_895_106, 346),
    ("WB-1", "chunk", 454, 399.50382329995045, 371_208_452, 419),
    ("WB-2", "chunk", 442, 384.024408598023, 386_129_377, 404),
    ("B1-1", "chunk", 449, 389.805400947982, 380_464_819, 414),
    ("B1-2", "chunk", 455, 390.9801439330331, 379_321_671, 417),
]
R16 = [
    ("M0-1", "chunk", 380, 334.2560730279997, 443_813_830, 345),
    ("M0-2", "chunk", 384, 332.9227245430002, 445_567_129, 349),
    ("RT-1", "chunk", 373, 321.47776908799915, 461_354_721, 335),
    ("RT-2", "chunk", 376, 323.2486707620001, 458_876_991, 336),
    ("SM-1", "chunk", 428, 368.32628241300154, 402_695_488, 389),
    ("SM-2", "chunk", 400, 354.5123000099993, 418_455_066, 364),
    ("G35-1", "chunk", 412, 332.8941166730001, 445_460_414, 345),
    ("O0-1", "udp", 413, 374.6, None, None),
    ("RS-1", "chunk", 399, 353.5895461039963, 419_592_605, 364),
    ("RS-2", "chunk", 385, 340.9823318109993, 435_106_294, 349),
    ("M1-1", "chunk", 384, 331.44430281300083, 447_457_507, 348),
    ("G35-2", "chunk", 419, 333.34066392299974, 444_960_211, 346),
    ("G35-3", "chunk", 411, 333.0759936150007, 445_337_941, 343),
]
# the wave before the first of each series, as the first wave's own `decision.previous` named it (`achieved_bps` of a chunk wave)
BEFORE = {"r14": ("ses_6e680a33", 368_077_168), "r16": ("ses_a2581fb9", 379_321_671)}
SERIES = {"r14": R14, "r16": R16}

T0 = datetime(2026, 11, 16, 8, 0, 0, tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _stream(engine: str, wall: float, achieved: int | None, *, ended: str = "send_done") -> dict:
    """`wave_rates.stream` in the shape the server writes: a chunk wave keeps everything under `chunk` (`chunk_rate.sample_of`),
    a udpcast wave has `wall_s` and `ended` at the top."""
    if engine == "udp":
        return {"wall_s": wall, "ended": ended, "sent_bytes": IMAGE, "loss_blocks": 0}
    return {"chunk": {"asked_bps": ASKED, "sent_bps": achieved, "achieved_bps": achieved, "first_pass_s": wall,
                      "ended": ended, "skip": None, "stream_zbytes": IMAGE, "drives": {MAC: {SERIAL: IMAGE}}}}


def _wave(conn, sid: str, stream: dict, *, at: datetime, seconds: float, state: str = "closed") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
        " created_at, last_join_at, started_at, closed_at) VALUES (?, 'grp_CLONERS', 'img_x', 'ROOM', 1, 300, ?, 't', ?, 0, ?, ?)",
        (sid, state, _iso(at), _iso(at), _iso(at + timedelta(seconds=seconds))))
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream, delivered) VALUES (?,?,?,?,?)",
                 (sid, json.dumps({"max_bitrate": "669m"}), _iso(at), json.dumps(stream),
                  json.dumps({MAC: {"drawers": 1, "done": 1, "written_bytes": IMAGE}})))
    conn.commit()


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _mape(pairs: list[tuple[float, float]]) -> float:
    return 100 * sum(abs(p - a) / a for p, a in pairs) / len(pairs)


# --- #3549: K16 (learn/eta.py) ---------------------------------------------------------------------------------------------


def test_a_chunk_wave_with_no_wall_s_enters_the_history(conn):
    """The negative control of the Issue: a wave whose `stream` has only `chunk` (no `wall_s`, no top `ended`) is history. Two
    waves of 392s and 400s by the engine's clock, 453s and 460s from open to close."""
    _wave(conn, "ses_a", _stream("chunk", 392.5, 377_941_207), at=T0, seconds=453)
    _wave(conn, "ses_b", _stream("chunk", 400.0, 370_000_000), at=T0 + timedelta(hours=1), seconds=460)
    got = learn_eta.wave(conn, [{"mac": MAC}], IMAGE, None, "ses_new")
    assert got["why"] is None and got["machines"][0]["waves"] == 2, got
    assert got["machines"][0]["sessions"] == ["ses_b", "ses_a"]
    # goodput of the median of the two (IMAGE / 392.5 and IMAGE / 400), fixed stages of the median (60.5 and 60): the same image
    # gives back the wave itself -- the median of 453 and 460
    assert got["seconds"] == pytest.approx(456.5, abs=1.5)


@pytest.mark.parametrize("stream", [
    {"chunk": {"first_pass_s": 392.5, "ended": "send_failed"}},                 # the stream did not finish
    {"chunk": {"first_pass_s": None, "ended": "send_done"}},                    # the first pass was not measured
    {"chunk": {"first_pass_s": 30.0, "ended": "send_done"}},                    # too short to measure (MIN_SECONDS)
    {"chunk": {"ended": "send_done"}},
], ids=["not_done", "unmeasured", "too_short", "missing"])
def test_a_chunk_wave_without_a_positive_measurement_is_not_history(conn, stream):
    """Principle 5: success by positive evidence. No `ended == send_done` or no first pass in seconds: not a sample."""
    _wave(conn, "ses_a", stream, at=T0, seconds=453)
    got = learn_eta.wave(conn, [{"mac": MAC}], IMAGE, None, "ses_new")
    assert got["seconds"] is None and got["why"] == "no_history", got


def test_an_old_udpcast_wave_is_not_history(conn):
    """#3173: udpcast is gone. Its row in an upgraded DB (`wall_s` at the top) stays in the table and is not read."""
    _wave(conn, "ses_u", _stream("udp", 374.6, None), at=T0, seconds=413)
    got = learn_eta.wave(conn, [{"mac": MAC}], IMAGE, None, "ses_new")
    assert got["why"] == "no_history" and got["seconds"] is None, got


def _replay_k16(conn, series: str) -> tuple[list[tuple[float, float]], int]:
    """Go through the series in order: before each chunk wave ask the forecast (`learn_eta.wave`, what K16 records), then add the
    wave to the history. Returns the (forecast, session seconds) pairs of the waves that HAD an estimate, and how many chunk
    waves there were after the first."""
    pairs, chunk_waves = [], 0
    at = T0
    for i, (name, engine, seconds, wall, achieved, _k3) in enumerate(SERIES[series]):
        sid = f"ses_{series}_{i:02d}"
        if engine == "chunk":
            chunk_waves += 1
            got = learn_eta.wave(conn, [{"mac": MAC}], IMAGE, None, sid)
            if got["seconds"] is not None:
                pairs.append((got["seconds"], seconds))
        _wave(conn, sid, _stream(engine, wall, achieved), at=at, seconds=seconds)
        at += timedelta(hours=1)
    return pairs, chunk_waves - 1


@pytest.mark.parametrize("series", ["r14", "r16"])
def test_k16_forecast_of_the_chunk_waves_is_within_ten_percent_per_series(conn, series):
    """r14 and r16 each on its own: every chunk wave after the first has an estimate (the history holds the chunk waves), and the
    MAPE against the wave's time from open to close is under 10%. Before: r14 predicted 413/449s in every wave (udpcast only)."""
    pairs, expected = _replay_k16(conn, series)
    assert len(pairs) == expected, f"{series}: {len(pairs)} of {expected} waves had an estimate"
    assert _mape(pairs) < MAPE_LIMIT, (series, _mape(pairs), pairs)


# --- #3550: K3 (waveeta) -----------------------------------------------------------------------------------------------------


class Lib:
    def get(self, image_id):
        return {"total_compressed_bytes": IMAGE, "partitions": [{"index": 3, "file": "p3.pcl.zst", "readback_chunks": CHUNKS}]}


class Chunk:
    no_file = None

    def rate_outcome(self, session_id):
        return {}


def _eta(conn) -> W.WaveEta:
    return W.WaveEta(conn, library=Lib(), chunk=Chunk(), mode=lambda: "shadow", clock=lambda: 0.0)


def _predict(conn, sid: str, bits: int = ASKED) -> tuple[int, list[str]]:
    """What K3 records when the wave `sid` starts (`WaveEta.started`): (`eta_s`, the evidence)."""
    conn.execute("INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES (?, 'grp_CLONERS', 'img_x', 'ROOM', 1, 1, 'running', 'x', 'now', 0)",
                 (sid,))
    conn.commit()
    _eta(conn).started(sid, bits, {(MAC, SERIAL): 300})
    row = [d for d in memory.decisions(conn, rule="K3", limit=100) if d["action"] == "WAVE_ETA" and d["round_id"] == sid][0]
    ev = row["evidence"]
    return int(next(e for e in ev if e.startswith("eta_s=")).split("=")[1]), ev


def _replay_k3(conn, series: str, *, seed: bool = True) -> list[tuple[float, float]]:
    """Before each chunk wave, K3's forecast from the DB as the server has it then; after it, the wave's row (as the server writes
    it at the wave's end). The pairs are (forecast, `actual_s` -- what K3 itself measured and reports as `actual_s`)."""
    at = T0
    if seed:
        before_sid, before_bps = BEFORE[series]
        _wave(conn, before_sid, _stream("chunk", 330.0, before_bps), at=at - timedelta(hours=1), seconds=400)
    pairs = []
    for i, (name, engine, seconds, wall, achieved, actual) in enumerate(SERIES[series]):
        sid = f"ses_{series}_{i:02d}"
        if engine == "chunk":
            eta_s, _ev = _predict(conn, sid)
            pairs.append((eta_s, actual))
        _wave(conn, sid, _stream(engine, wall, achieved), at=at, seconds=seconds)
        at += timedelta(hours=1)
    return pairs


@pytest.mark.parametrize("series", ["r14", "r16"])
def test_k3_forecast_is_within_ten_percent_per_series(conn, series):
    """The forecast is made at the wave's start from the rate the last chunk wave achieved, and lands within 10% of `actual_s`.
    Before: 222s in every wave (669Mb/s asked) against 335-475s (MAPE 46% on r14, 37% on r16 -- calibration-1116.md section 3)."""
    pairs = _replay_k3(conn, series)
    assert len(pairs) == sum(1 for w in SERIES[series] if w[1] == "chunk")
    assert _mape(pairs) < MAPE_LIMIT, (series, _mape(pairs), pairs)
    assert all(p != 222 for p, _a in pairs), pairs


def test_k3_names_what_it_took_the_rate_from(conn):
    _wave(conn, "ses_prev", _stream("chunk", 334.0, 443_813_830), at=T0, seconds=380)
    eta_s, ev = _predict(conn, "ses_now")
    assert eta_s == round(IMAGE * 8 / 443_813_830) and "stream_mbps=444" in ev and "asked_mbps=669" in ev, ev
    assert "achieved_before_mbps=444" in ev and "achieved_before_wave=ses_prev" in ev, ev


def test_no_earlier_wave_stays_with_the_asked_rate(conn):
    """The first wave of a server: nothing achieved is known. The old number, and the evidence says it was not measured."""
    eta_s, ev = _predict(conn, "ses_now")
    assert eta_s == round(IMAGE * 8 / ASKED) == 222, ev
    assert "achieved_before_mbps=unmeasured" in ev and not any(e.startswith("achieved_before_wave=") for e in ev), ev


@pytest.mark.parametrize("stream, why", [
    (_stream("chunk", 334.0, 443_813_830, ended="send_failed"), "the last chunk wave did not end in send_done"),
    (_stream("chunk", 334.0, 443_813_830, ended="send_stopped"), "the last chunk wave was stopped"),
    ({"chunk": {"asked_bps": ASKED, "achieved_bps": None, "ended": "send_done"}}, "achieved_bps was not measured"),
    ({"chunk": {"asked_bps": ASKED, "achieved_bps": 0, "ended": "send_done"}}, "achieved_bps is not positive"),
    (_stream("udp", 374.6, None), "only a udpcast wave before"),
])
def test_an_earlier_wave_that_gives_no_rate_leaves_the_asked_rate(conn, stream, why):
    _wave(conn, "ses_prev", stream, at=T0, seconds=380)
    eta_s, ev = _predict(conn, "ses_now")
    assert eta_s == 222 and "achieved_before_mbps=unmeasured" in ev, (why, ev)


def test_a_failed_last_chunk_wave_does_not_borrow_the_rate_of_an_older_one(conn):
    """Which wave to trust is not guessed: the LAST chunk wave counts; if it gave no rate, the asked rate stays."""
    _wave(conn, "ses_old", _stream("chunk", 334.0, 443_813_830), at=T0, seconds=380)
    _wave(conn, "ses_last", _stream("chunk", 100.0, 300_000_000, ended="send_failed"), at=T0 + timedelta(hours=1), seconds=380)
    assert _predict(conn, "ses_now")[0] == 222


def test_a_udpcast_wave_in_between_is_skipped_not_a_blocker(conn):
    """r16: the O0 udpcast wave sits between G35-1 and RS-1; the rate comes from the last CHUNK wave."""
    _wave(conn, "ses_chunk", _stream("chunk", 334.0, 443_813_830), at=T0, seconds=380)
    _wave(conn, "ses_udp", _stream("udp", 374.6, None), at=T0 + timedelta(hours=1), seconds=413)
    eta_s, ev = _predict(conn, "ses_now")
    assert eta_s == round(IMAGE * 8 / 443_813_830) and "achieved_before_wave=ses_chunk" in ev, ev


def test_the_rate_is_the_lower_of_asked_and_achieved(conn):
    """An earlier wave that managed more than this wave asks does not speed the forecast beyond what is asked."""
    _wave(conn, "ses_prev", _stream("chunk", 200.0, 800_000_000), at=T0, seconds=300)
    eta_s, ev = _predict(conn, "ses_now", bits=500_000_000)
    assert eta_s == round(IMAGE * 8 / 500_000_000) and "stream_mbps=500" in ev, ev


def test_the_wave_itself_is_not_its_own_earlier_wave(conn):
    """A row of the wave being predicted (a restart) is not read as the wave before it."""
    _wave(conn, "ses_now", _stream("chunk", 334.0, 443_813_830), at=T0, seconds=380)
    conn.execute("DELETE FROM sessions WHERE id = 'ses_now'")
    conn.commit()
    assert _predict(conn, "ses_now")[0] == 222
