"""#3520: a `--write-kbps` ceiling is a measurement, not a verdict for 50 waves.

LabRoot, round 11 (16/11, `lab/metal-1116-r11`): run 1 `CUT 188 -> 157.5MB/s`, runs 2-4 `HOLD at_proven_ceiling`, never a `RAISE`. The
ceiling came from one wave, `ses_7516fee9` -- an experiment that sent without a limit and lost 32% -- where "the drive wrote 175MB/s"
(x 0.9 = 157.5) was the durable bytes of a drive that was missing chunks, not a drive that was slow. `chunk_rate.sustained` now:

- takes a proof only from a wave whose loss was MEASURED and not above `LOSS_PERMILLE` (the loss that cuts the wave rate);
- spends the proof after `REPROBE_WAVES` clean waves newer than it, in which the drive took part and kept up at the ceiling or above:
  the loop climbs again (`RAISE`), and a drive that falls behind there is a new proof.

**Negative control:** `server/rounds/chunk_rate.py` back at the base (and `server/rounds/chunksend.py`, whose `feedback_bounds` the
sibling file `test_feedback_learned_max_3512.py` pins) -- `sustained` takes the polluted wave and never lets go: every test here fails on
behaviour, except the one marked (guard).
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, drive_rates

MAC = "aa:bb:cc:00:00:31"
CHUNK = 4 * 1024 * 1024
CHUNKS = 5000
CHECK = 1504000                      # kbit/s: 0.9 x the 209MB/s write check of the metal
WAS = {"bytes": 18_300_000_000, "seconds": 300.0}      # the drive wrote 61MB/s (a number for the test) while behind
CEILING = int(61 * 8000 * writerate.MARGIN)
K = getattr(chunk_rate, "REPROBE_WAVES", 3)       # getattr: against the base the tests fail on behaviour, not on this name
#: the most chunks a wave of CHUNKS can lose without passing `LOSS_PERMILLE`
LOSS_OK = CHUNKS * chunk_rate.LOSS_PERMILLE // 1000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, sid, *, kbps=CEILING, lost=0, behind=None, ended="send_done", serials=("S1",), drives=None):
    """A chunk wave that ended, by the server's own path (`decide`, then `record` of the engine's outcome). `kbps`: what `--write-kbps`
    was (None = the engine did not say), `lost`: None = not measured, `behind`: `rate_outcome.behind`."""
    drive_rates.decide(conn, sid, lambda: [{"serial": "S1", "model": "M", "mac": MAC, "port": 1}], None,
                       adjust=lambda chosen, drives_: chunk_rate.adjust(conn, chosen, drives_))
    outcome = {"chunks": CHUNKS, "bytes": CHUNKS * CHUNK, "first_pass_s": 600.0, "lost_chunks": lost, "away": 0, "cloners": 1,
               "repair_rounds": 0, "ended": ended, "paced_ms": {"wire": 0, "disk": 444_000}}
    if kbps is not None:
        outcome.update(write_kbps=kbps, write_serials=list(serials))
    if behind is not None:
        outcome["behind"] = behind
    if drives is not None:
        outcome["drives"] = drives
    chunk_rate.record(conn, sid, outcome)
    for serial, mine in (behind or {}).items():
        # #3530: what a real wave writes next to `behind` -- `chunk_pass_counters`, pass 1: the chunks of the wave the drive did not make durable
        skipped = max(1, CHUNKS - mine["bytes"] // CHUNK)
        conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                     " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')", (sid, 3, 1, MAC, "receive", skipped, json.dumps({serial: skipped})))
    conn.commit()


def _ask(conn, sid):
    return chunk_rate.write_rate(conn, sid, 3, CHECK, ["S1"])


# --- 1. the metal: a wave that lost chunks is not proof of a ceiling ------------------------------------------------


def test_the_scenario_of_the_metal_a_wave_with_32_percent_lost_teaches_no_ceiling(conn):
    """ses_7516fee9: an unbounded sender, 32% lost, the drive "wrote 175MB/s" -- the next waves are not cut to 157.5."""
    _wave(conn, "ses_7516fee9", kbps=None, lost=CHUNKS * 32 // 100, behind={"S1": {"bytes": 175 * 300 * 10 ** 6, "seconds": 300.0}})
    assert chunk_rate.write_history(conn, "S1") is None
    assert chunk_rate.write_ceiling(conn, ["S1"]) is None
    assert _ask(conn, "w1") == (CHECK, None)                  # the check is the start: no `CUT ...+sustained`


@pytest.mark.parametrize("lost, proof", [(0, True), (LOSS_OK, True), (LOSS_OK + 1, False), (None, False)])
def test_the_loss_of_the_wave_decides_whether_what_the_drive_wrote_is_proof(conn, lost, proof):
    """At `LOSS_PERMILLE` it still teaches; over it, or not measured (not "none lost", principle 5), it does not."""
    assert LOSS_OK == 5
    _wave(conn, "w0", kbps=None, lost=lost, behind={"S1": WAS})
    assert (chunk_rate.write_history(conn, "S1") == (61, "w0")) is proof
    assert (chunk_rate.write_ceiling(conn, ["S1"]) == CEILING) is proof


def test_a_clean_older_proof_stands_behind_a_polluted_newer_one(conn):
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    _wave(conn, "w1", kbps=None, lost=CHUNKS // 3, behind={"S1": {"bytes": 175 * 300 * 10 ** 6, "seconds": 300.0}})
    assert chunk_rate.write_history(conn, "S1") == (61, "w0")


# --- 2. a proof is spent after K clean waves at the ceiling: the probe --------------------------------------------


def test_after_k_waves_at_the_ceiling_without_lag_the_loop_raises_once(conn):
    """The metal: run 1 CUT, runs 2-4 HOLD at_proven_ceiling. Now the K-th wave at the ceiling without lag is followed by a RAISE."""
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    for n in range(1, K):
        _wave(conn, f"w{n}")
        got, label = _ask(conn, f"w{n + 1}")
        assert (got, label.split()[:2]) == (CEILING, ["HOLD", "at_proven_ceiling"]), n
    _wave(conn, f"w{K}")
    got, label = _ask(conn, f"w{K + 1}")
    assert got == CEILING * (100 + chunk_rate.WRITE_STEP_PERCENT) // 100 and got > CEILING
    assert label == "RAISE kept_up_and_held_by_cap"               # not "...+sustained": the old proof is spent
    assert chunk_rate.write_history(conn, "S1") is None and chunk_rate.write_ceiling(conn, ["S1"]) is None


def test_after_the_probe_the_loop_goes_on_climbing_until_the_drive_falls_behind(conn):
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    for n in range(1, K + 1):
        _wave(conn, f"w{n}")
    rate = _ask(conn, "w9")[0]
    _wave(conn, "w9", kbps=rate)
    got, label = _ask(conn, "w10")
    assert got == rate * 110 // 100 and label == "RAISE kept_up_and_held_by_cap"


def test_a_drive_that_falls_behind_in_the_probe_is_the_new_proof_and_the_count_starts_again(conn):
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    for n in range(1, K + 1):
        _wave(conn, f"w{n}")
    probe = _ask(conn, "w9")[0]
    again = {"bytes": 64 * 300 * 10 ** 6, "seconds": 300.0}       # it wrote 64MB/s: the ceiling moves up, from the wave it fell behind in
    _wave(conn, "w9", kbps=probe, behind={"S1": again})
    assert chunk_rate.write_history(conn, "S1") == (64, "w9")
    new = int(64 * 8000 * writerate.MARGIN)
    share = (CHUNKS - again["bytes"] // CHUNK) / CHUNKS          # #3530: the wave carries its counters, so the cut is the graded one of #3526
    assert _ask(conn, "w10") == (int(probe * (1 - share) * writerate.MARGIN), "CUT drive_behind")
    for n in range(10, 10 + K - 1):                                # K - 1 waves at the new ceiling: still held there
        _wave(conn, f"w{n}", kbps=new)
        got, label = _ask(conn, f"w{n + 1}")
        assert got == new and label.startswith("HOLD at_proven_ceiling"), n
    assert chunk_rate.write_ceiling(conn, ["S1"]) == new


@pytest.mark.parametrize("what, wave", [
    ("under the ceiling", {"kbps": CEILING - 1}),                  # it kept up, but not AT the ceiling: proves nothing about it
    ("polluted by loss", {"lost": LOSS_OK + 1}),
    ("loss not measured", {"lost": None}),
    ("the engine did not say what it asked", {"kbps": None}),
    ("it did not end", {"ended": "send_failed"}),
    ("the drive was not in it", {"serials": ("S2",)}),
])
def test_a_wave_that_does_not_show_the_drive_keeping_up_at_the_ceiling_is_not_counted(conn, what, wave):
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    for n in range(1, K + 1):
        _wave(conn, f"w{n}", **wave)
    assert chunk_rate.write_history(conn, "S1") == (61, "w0"), what


def test_a_drive_a_machine_reported_durable_counts_as_in_the_wave_even_when_it_was_left_out_of_the_cap(conn):
    """The slow drive left out of `--write-kbps` (#3295): the fast ones set the pace, but it was in the stream and kept up."""
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    for n in range(1, K + 1):
        _wave(conn, f"w{n}", serials=("S9",), drives={MAC: {"S1": 10 ** 9}})
    assert chunk_rate.write_history(conn, "S1") is None


def test_one_wave_at_the_ceiling_does_not_spend_the_proof(conn):
    _wave(conn, "w0", kbps=None, behind={"S1": WAS})
    _wave(conn, "w1")
    assert chunk_rate.write_history(conn, "S1") == (61, "w0")


def test_garbage_in_the_history_never_raises(conn):
    """(guard) a row that cannot be read is skipped, as before."""
    drive_rates.decide(conn, "w0", lambda: [{"serial": "S1", "model": "M", "mac": MAC, "port": 1}], None)
    conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = 'w0'", ('{"chunk": [1, 2]}',))
    assert chunk_rate.write_history(conn, "S1") is None
    assert chunk_rate.write_ceiling(conn, ["S1"]) is None
