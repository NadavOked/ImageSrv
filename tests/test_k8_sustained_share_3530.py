"""#3530: a drive's "proven ceiling" (`chunk_rate.sustained`) is learned only from a wave in which it REALLY fell behind.

Until now any `behind` entry taught it: what the drive made durable over the seconds the receiver skipped chunks for it, as MB/s. On the metal
of 16/11 (`lab/metal-1116-r12`, LabRoot, one drive, six chunk waves) that was b1: 3 chunks skipped of ~18,250 (0.16 permille) and the bytes
in `behind` are nearly the whole offer -- not what the drive writes. When the sender offers less than `--write-kbps` that number is the
sender's rate, it becomes the ceiling, and the next wave is cut to it: a slowness lock (as #3520, #3525). It did not hurt in r12 only
because 201MB/s was above the rate asked.

`sustained` now reads each `behind` wave like the cut of #3526 does (`_behind_share`, `BEHIND_PERMILLE`):
  - under the threshold: the drive KEPT UP in that wave (it counts toward `REPROBE_WAVES`, like a wave with no `behind`);
  - over it, but the sender was held by the wire for more than `server_io.SENDER_ASLEEP_MAX` of the first pass: no proof, and not
    "kept up" either -- the wave says nothing about the drive;
  - over it and the sender free: the proof;
  - not measured -- no `chunk_pass_counters` row that counted the skipped chunks (or the read failed), or no `paced_ms` -- is NO proof and is not
    counted as the drive keeping up either (principle 5): "we could not check" must not become a ceiling that slows the next waves. The wave is named
    in the evidence of the decision that read it (`ceiling_unmeasured`).

The numbers are the real ones of the metal (`runs/b1-chunk/db-extra.json`, `runs/a1-chunk/db-extra.json`): the engine's outcome, the paced
milliseconds and `chunk_pass_counters` of ses_d8135989 (b1) and ses_cbd80730 (a1).

**Negative control:** `server/rounds/chunk_rate.py` back at the base -- no `_lag_proof`, `sustained` takes every `behind` wave: every test
here fails on behaviour except the ones marked (guard).
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.learn import server_io
from server.brain import writerate
from server.rounds import chunk_rate, drive_rates

MAC = "78:ac:c0:9b:11:c2"
SERIAL = "naa.5002538d42a58a2c"
CHUNK = 4 * 1024 * 1024
CHECK = 1386000                  # a1 of the metal: 173.25MB/s asked of the drive
HELD_B1 = 1257795                # b1: 157.2MB/s
MARGIN = writerate.MARGIN

#: the engine's outcome of the two waves, as the metal reported it (ses_d8135989 = b1, ses_cbd80730 = a1)
B1 = {"chunks": 18441, "lost_chunks": 7, "bytes": 77_347_160_064, "first_pass_s": 399.51611464002053, "stream_zbytes": 18_545_444_861,
      "paced_ms": {"wire": 107_352, "disk": 142_827}, "write_kbps": HELD_B1, "behind": {SERIAL: {"bytes": 76_520_882_176, "seconds": 379.5048588690115}}}
A1 = {"chunks": 18441, "lost_chunks": 4, "bytes": 77_347_160_064, "first_pass_s": 399.0894286609837, "stream_zbytes": 18_545_444_861,
      "paced_ms": {"wire": 107_776, "disk": 142_731}, "write_kbps": CHECK, "behind": {SERIAL: {"bytes": 76_441_190_400, "seconds": 379.12987620101194}}}
#: the pass of a drive that REALLY fell behind: 13,250 chunks durable and 5,000 skipped (27%), at the pace of a1
LAG = {"bytes": 13_250 * CHUNK, "seconds": 379.1}
LAG_MBPS = int(LAG["bytes"] / LAG["seconds"] / 10 ** 6)          # 146


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _counters(skipped: int) -> dict:
    """The rows of the metal's wave: it skipped `skipped` chunks in partition 3's first pass, none elsewhere."""
    return {(1, 1): 0, (2, 1): 0, (3, 1): skipped, (4, 1): 0}


def _wave(conn, sid, base, *, skipped=None, wire=None, behind=None, kbps=None, drop=(), **over):
    """A chunk wave that ended (`decide`, then `record` of `base`, the outcome of the metal, with `over`) and the rows `chunkpass.reported` wrote
    for it. `skipped`: the chunks skipped for the drive (None = no `chunk_pass_counters` rows), `wire`: ms the sender slept for `--kbps`
    (None = as the metal), `behind`: replaces the metal's, `drop`: keys the engine did not report."""
    drive_rates.decide(conn, sid, lambda: [{"serial": SERIAL, "model": "M", "mac": MAC, "port": 1}], None,
                       adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives))
    outcome = {**base, "away": 0, "cloners": 1, "repair_rounds": 1, "ended": "send_done", "write_serials": [SERIAL], **over}
    if wire is not None:
        outcome["paced_ms"] = {**base["paced_ms"], "wire": wire}
    if behind is not None:
        outcome["behind"] = behind
    if kbps is not None:
        outcome["write_kbps"] = kbps
    for key in drop:
        outcome.pop(key, None)
    chunk_rate.record(conn, sid, outcome)
    if skipped is not None:
        for (part, rnd), n in _counters(skipped).items():
            conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                         " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')", (sid, part, rnd, MAC, "receive", n, json.dumps({SERIAL: n})))
    conn.commit()


def _ask(conn, sid, base=CHECK):
    return chunk_rate.write_rate(conn, sid, 3, base, [SERIAL])


def _taught(conn):
    """What the drive's memory says now: (MB/s, the wave) or None, and the ceiling of `--write-kbps` made of it."""
    return chunk_rate.write_history(conn, SERIAL), chunk_rate.write_ceiling(conn, [SERIAL])


# --- the metal -------------------------------------------------------------------------------------------------


def test_b1_of_the_metal_three_chunks_of_18247_teach_no_ceiling(conn):
    """b1 as the metal recorded it: 3 skipped (0.164 permille). Before: `write_history` = (201, ses_d8135989), a ceiling of 1,447,200."""
    _wave(conn, "ses_d8135989", B1, skipped=3)
    assert _taught(conn) == (None, None)


def test_the_share_alone_does_it_and_the_sender_alone_would_not_have(conn):
    """The metal's b1 has the sender asleep for the wire 26.9% of the pass, over `SENDER_ASLEEP_MAX`. With the wire asleep for nothing the 3 chunks
    teach nothing either: it is the share of the skipped chunks that decides, not the sender."""
    _wave(conn, "b1", B1, skipped=3, wire=0)
    assert _taught(conn) == (None, None)


def test_a1_of_the_metal_with_a_free_sender_is_a_lag_over_the_threshold_and_teaches(conn):
    """a1: 25 of 18,250 (1.37 permille, over): by the rule of #3526 a real lag, so by this one a proof (what it made durable, 201MB/s)."""
    _wave(conn, "a1", A1, skipped=25, wire=0)
    assert chunk_rate.write_history(conn, SERIAL) == (201, "a1")
    assert chunk_rate.write_ceiling(conn, [SERIAL]) == int(201 * 8000 * MARGIN)


def test_a_wave_in_which_the_sender_offered_less_than_held_does_not_lock_the_rate_to_what_it_offered(conn):
    """The lock itself. b1's wave, but the sender offered 100MB/s of the 157MB/s held (`behind` says what was durable: 100MB/s for 3 skipped
    chunks). Before: the ceiling 720,000 (100MB/s x 0.9) cuts the next wave from 1,257,795 -- `CUT behind_negligible+sustained`."""
    slow = {"bytes": 100 * 379 * 10 ** 6, "seconds": 379.0}
    _wave(conn, "b1", B1, skipped=3, wire=0, behind={SERIAL: slow})
    assert _ask(conn, "b2", base=HELD_B1) == (HELD_B1, "HOLD behind_negligible")


# --- over the threshold: a real lag teaches --------------------------------------------------------------------


def test_a_real_lag_teaches_what_the_drive_made_durable(conn):
    _wave(conn, "w1", A1, skipped=5_000, wire=0, behind={SERIAL: LAG})
    assert chunk_rate.write_history(conn, SERIAL) == (LAG_MBPS, "w1")
    assert chunk_rate.write_ceiling(conn, [SERIAL]) == int(LAG_MBPS * 8000 * MARGIN) == 1_051_200


@pytest.mark.parametrize("skipped, taught", [
    (3, False),                  # b1
    (18, False),                 # 0.99 permille: under
    (19, True),                  # 1.04 permille: over
    (25, True),                  # a1
    (5_000, True),
])
def test_the_threshold_is_the_one_of_the_cut_a_share_of_the_chunks_of_the_pass(conn, skipped, taught):
    """18,244 chunks durable, as in b1 (`BEHIND_PERMILLE`, #3526): the same line that decides whether a wave cuts decides whether it teaches."""
    _wave(conn, "w1", A1, skipped=skipped, wire=0, behind={SERIAL: {"bytes": 18_244 * CHUNK, "seconds": 379.0}})
    assert (chunk_rate.write_history(conn, SERIAL) is not None) is taught


def test_a_small_image_one_chunk_is_over_the_threshold_a_big_one_190_are_not(conn):
    """A share, not a count (as #3526): 1 of 501 chunks is 2 permille, 190 of 199,190 is 0.95."""
    _wave(conn, "small", A1, skipped=1, wire=0, behind={SERIAL: {"bytes": 500 * CHUNK, "seconds": 90.0}})
    assert chunk_rate.write_history(conn, SERIAL) == (23, "small")
    _wave(conn, "big", A1, skipped=190, wire=0, behind={SERIAL: {"bytes": 199_000 * CHUNK, "seconds": 4_000.0}})
    assert chunk_rate.write_history(conn, SERIAL) == (23, "small")        # the newer wave taught nothing: the older proof stands


# --- the sender held by the wire -------------------------------------------------------------------------------


def test_a_lag_with_the_sender_held_by_the_wire_is_no_proof(conn):
    """The a1 of the metal (the sender asleep for the wire 27%) with 5,000 chunks skipped: a real share, but the bytes are the sender's."""
    _wave(conn, "w1", A1, skipped=5_000, behind={SERIAL: LAG})
    assert _taught(conn) == (None, None)


@pytest.mark.parametrize("wire, taught", [
    (0, True),
    (100_000, True),             # exactly SENDER_ASLEEP_MAX (25%) of 400s: not over
    (100_001, False),
    (399_000, False),
])
def test_the_sender_is_held_when_it_slept_for_the_wire_over_sender_asleep_max_of_the_first_pass(conn, wire, taught):
    assert server_io.SENDER_ASLEEP_MAX == 0.25
    _wave(conn, "w1", A1, skipped=5_000, wire=wire, behind={SERIAL: LAG}, first_pass_s=400.0)
    assert (chunk_rate.write_history(conn, SERIAL) is not None) is taught


def test_sleeping_for_write_kbps_is_the_cap_not_a_limit(conn):
    """LabRoot's waves hold the sender with `--write-kbps` (74% of the first pass asleep for the disk, #3498): that is the cap whose ceiling this
    is, and the sender asleep for it offered the held rate."""
    _wave(conn, "w1", A1, skipped=5_000, behind={SERIAL: LAG}, paced_ms={"wire": 0, "disk": 296_000}, first_pass_s=400.0)
    assert chunk_rate.write_history(conn, SERIAL) == (LAG_MBPS, "w1")


# --- the count toward the end of a proof (#3520) ---------------------------------------------------------------


K = chunk_rate.REPROBE_WAVES
WAS = {"bytes": 18_300_000_000, "seconds": 300.0}      # the drive wrote 61MB/s while behind
CEILING = int(61 * 8000 * MARGIN)


def _proof(conn):
    _wave(conn, "w0", A1, wire=0, skipped=637, behind={SERIAL: WAS}, kbps=CEILING)         # 637 of 5,000 chunks skipped
    assert chunk_rate.write_history(conn, SERIAL) == (61, "w0")


def test_waves_with_a_negligible_skip_at_the_ceiling_are_waves_the_drive_kept_up_in_and_spend_the_proof(conn):
    """K waves like b1 (3 skipped) at the ceiling: the drive kept up there, the loop may climb again. Before: the newest of them was the proof
    (201MB/s, "the drive wrote 201") and nothing was ever spent."""
    _proof(conn)
    for n in range(1, K):
        _wave(conn, f"w{n}", B1, skipped=3, wire=0, kbps=CEILING)
        assert chunk_rate.write_history(conn, SERIAL) == (61, "w0"), n
    _wave(conn, f"w{K}", B1, skipped=3, wire=0, kbps=CEILING)
    assert _taught(conn) == (None, None)


def test_waves_in_which_the_sender_was_held_by_the_wire_do_not_count_as_kept_up(conn):
    """The sender offered what the wire allowed, maybe less than the ceiling: the wave says nothing about the drive, so it neither replaces the
    proof nor spends it."""
    _proof(conn)
    for n in range(1, K + 2):
        _wave(conn, f"w{n}", A1, skipped=5_000, behind={SERIAL: LAG}, kbps=CEILING)
    assert chunk_rate.write_history(conn, SERIAL) == (61, "w0")


# --- guards ----------------------------------------------------------------------------------------------------


def test_a_wave_whose_skipped_chunks_were_not_counted_is_no_proof_and_the_ceiling_does_not_fall(conn):
    """Principle 5 (#3530): no `chunk_pass_counters` row for the wave. Before the first version of this fix it taught `behind` as the ceiling."""
    _wave(conn, "w1", A1, wire=0, behind={SERIAL: LAG})
    notes: list = []
    assert chunk_rate.write_history(conn, SERIAL, notes) is None
    assert notes == [{"session_id": "w1", "serial": SERIAL, "why": "skipped_not_counted"}]
    assert chunk_rate.write_ceiling(conn, [SERIAL]) is None
    # the cut of `write_next` for a wave without counters is #3526's old rule (what it wrote x the margin), and it is NOT capped by a ceiling
    assert _ask(conn, "w2") == (chunk_rate._behind_kbps(LAG, chunk_rate.config(None)), "CUT drive_behind")
    [row] = conn.execute("SELECT evidence_json FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_WRITE_RATE'").fetchall()
    assert json.loads(row["evidence_json"])[0]["ceiling_unmeasured"] == notes


@pytest.mark.parametrize("what, counters", [
    ("the machine did not say what it skipped", {"skipped": None}),
    ("a count of 0 contradicts `behind`", {"skipped": 0}),
])
def test_a_count_that_is_not_a_measurement_is_no_proof(conn, what, counters):
    _wave(conn, "w1", A1, wire=0, behind={SERIAL: LAG}, **counters)
    if counters["skipped"] is None:                   # the row exists, with nothing in `skipped_json`
        conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                     " VALUES ('w1',3,1,?,'receive',NULL,NULL,'2026-11-16T04:00:00+00:00')", (MAC,))
        conn.commit()
    assert _taught(conn) == (None, None), what


def test_a_counter_table_that_cannot_be_read_is_no_proof(conn):
    """The read fails: not "free to teach". The wave is named as not measured, and no ceiling comes out of it."""
    _wave(conn, "w1", A1, wire=0, skipped=5_000, behind={SERIAL: LAG})
    assert chunk_rate.write_history(conn, SERIAL) == (LAG_MBPS, "w1")
    conn.execute("DROP TABLE chunk_pass_counters")
    notes: list = []
    assert chunk_rate.write_history(conn, SERIAL, notes) is None
    assert notes == [{"session_id": "w1", "serial": SERIAL, "why": "skipped_not_counted"}]
    assert chunk_rate.write_ceiling(conn, [SERIAL]) is None


def test_a_wave_without_the_sender_sleep_is_no_proof(conn):
    """The share is over the threshold, but `paced_ms` was not read: the sender may have offered less than held. Not measured is not "free"."""
    _wave(conn, "w1", A1, skipped=5_000, behind={SERIAL: LAG}, drop=("paced_ms",))
    notes: list = []
    assert chunk_rate.write_history(conn, SERIAL, notes) is None
    assert notes == [{"session_id": "w1", "serial": SERIAL, "why": "sender_not_measured"}]
    _wave(conn, "w2", A1, skipped=5_000, behind={SERIAL: LAG}, first_pass_s=0)
    assert chunk_rate.write_history(conn, SERIAL) is None


def test_waves_that_were_not_measured_are_not_counted_as_the_drive_keeping_up(conn):
    """K waves at the ceiling whose lag nobody counted must not spend the proof (they say neither "behind" nor "kept up"), and must not replace it."""
    _proof(conn)
    for n in range(1, K + 2):
        _wave(conn, f"w{n}", A1, wire=0, behind={SERIAL: LAG}, kbps=CEILING)
    assert chunk_rate.write_history(conn, SERIAL) == (61, "w0")


def test_guard_the_loss_of_the_wave_still_decides_first(conn):
    """(guard, #3520) a wave over `LOSS_PERMILLE` is no proof whatever its share of skipped chunks."""
    _wave(conn, "w1", A1, skipped=5_000, wire=0, behind={SERIAL: LAG}, lost_chunks=500)
    assert _taught(conn) == (None, None)


def test_guard_a_lag_of_another_drive_teaches_nothing_about_this_one(conn):
    _wave(conn, "w1", A1, skipped=5_000, wire=0, behind={"S9": LAG})
    assert chunk_rate.write_history(conn, SERIAL) is None


@pytest.mark.parametrize("paced, seconds, held", [
    ({"wire": 0, "disk": 10 ** 6}, 400.0, False),
    ({"wire": 100_000, "disk": 0}, 400.0, False),
    ({"wire": 100_001, "disk": 0}, 400.0, True),
    (None, 400.0, None),                   # not measured is neither "held" nor "free"
    ({"wire": 10 ** 6}, 400.0, None),      # no `disk`: a counter that is not whole is not read as a measurement
    ({"wire": 10 ** 6, "disk": 0}, None, None),
    ({"wire": 10 ** 6, "disk": 0}, 0, None),
])
def test_sender_limited_reads_only_what_was_measured(paced, seconds, held):
    got = {"paced_ms": paced, "first_pass_s": seconds}
    assert chunk_rate._sender_limited(got) is held
