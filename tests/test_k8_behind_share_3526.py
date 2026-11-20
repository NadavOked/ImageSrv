"""#3526: the closed loop of `--write-kbps` cuts for the SHARE of chunks a drive skipped, not for the fact that it skipped some.

Metal 16/11 (`lab/metal-1116-r12`, LabRoot, one drive, six chunk waves): the loop swung 173, 130, 143, 157, 118, 130 MB/s. Both cuts came
from a handful of skipped chunks -- b1 skipped 3 of ~18,250 chunks of partition 3 (0.016%), a1 skipped 25 -- and each cut the next wave by a
flat 25%: `behind.bytes` is nearly the whole pass (76.5GB in 379s = 201MB/s, over the 157MB/s asked), so the number the loop derived from it
was above the asked rate, was thrown away, and the code fell back to `CUT_PERCENT`.

`chunk_rate.write_next` now reads how many chunks were skipped for the drive (`chunk_pass_counters`, pass 1) next to the chunks it made
durable (`behind.bytes`): under `BEHIND_PERMILLE` of the passes' chunks it is `HOLD behind_negligible`; from it, a cut graded by the share
(`held x (1 - share) x margin`); and when the counters are not there, the old rate rule if it gives a number, else ONE step down
(`drive_behind_unmeasured`) -- never a quarter of the rate made up from a number nobody measured.

**Negative control:** `server/rounds/chunk_rate.py` back at the base -- no `BEHIND_PERMILLE`, no `_lag`, and the 3-of-18,247 wave is a `CUT` of
25%; every test but the guards (marked) fails on behaviour.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, chunksend, drive_rates

MAC = "aa:bb:cc:00:00:31"
SERIAL = "naa.5002538d42a58a2c"
CHUNK = 4 * 1024 * 1024
CHECK = 1386000                  # a1 of the metal: 173.25MB/s asked of the drive
HELD_B1 = 1257795                # b1 of the metal: 157.2MB/s
B1_BEHIND = {"bytes": 76_520_882_176, "seconds": 379.5}      # ses_d8135989, partition 3: 18,244 chunks durable, 3 skipped
A1_BEHIND = {"bytes": 76_441_190_400, "seconds": 379.1}      # ses_cbd80730, partition 3: 18,225 chunks durable, 25 skipped
MARGIN = writerate.MARGIN


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _wave(conn, sid, *, kbps=CHECK, behind=None, counters=None, lost=0, serials=(SERIAL,), seconds=600.0):
    """A chunk wave that ended (`decide`, then `record` of the engine's outcome) and the rows `chunkpass.reported` wrote for it:
    `counters` = {(part, pass): {serial: skipped or None}}, the shape of the metal's `chunk_pass_counters`."""
    drive_rates.decide(conn, sid, lambda: [{"serial": SERIAL, "model": "M", "mac": MAC, "port": 1}], None,
                       adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives))
    outcome = {"chunks": 18_430, "bytes": 18_430 * CHUNK, "first_pass_s": seconds, "lost_chunks": lost, "away": 0, "cloners": 1,
               "repair_rounds": 1, "ended": "send_done", "paced_ms": {"wire": 0, "disk": int(seconds * 1000 * 0.74)},
               "write_kbps": kbps, "write_serials": list(serials)}
    if behind is not None:
        outcome["behind"] = behind
    chunk_rate.record(conn, sid, outcome)
    for (part, rnd), skipped in (counters or {}).items():
        conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                     " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')",
                     (sid, part, rnd, MAC, "receive" if rnd == 1 else "fetch",
                      None if skipped is None else sum(n for n in skipped.values() if n), None if skipped is None else json.dumps(skipped)))
    conn.commit()


def _pass(durable: int, mbps: int = 250) -> dict:
    """`rate_outcome.behind` of a drive that made `durable` chunks durable in passes it was behind in, at `mbps` over them. 250MB/s is over every
    `held` here: the proven ceiling (`sustained`, #3295/#3520 -- not what this file is about) must not be the thing that moves the answer."""
    size = durable * CHUNK
    return {"bytes": size, "seconds": size / (mbps * 10 ** 6)}


def _ask(conn, sid, base=CHECK, serials=(SERIAL,)):
    return chunk_rate.write_rate(conn, sid, 3, base, list(serials))


def _rows(conn):
    return conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_WRITE_RATE' ORDER BY id").fetchall()


def _metal(skipped: int):
    """The four partitions of the metal's wave: it skipped `skipped` chunks in partition 3, none elsewhere (`chunk_pass_counters`)."""
    return {(1, 1): {SERIAL: 0}, (2, 1): {SERIAL: 0}, (3, 1): {SERIAL: skipped}, (3, 2): None, (4, 1): {SERIAL: 0}}


# --- the metal --------------------------------------------------------------------------------------------------


def test_b1_of_the_metal_three_chunks_of_18247_do_not_cut_at_all(conn):
    """b1: 3 chunks skipped (0.164 permille). Before: `CUT drive_behind` to 75% (1257795 -> 943346), and b3 wrote 129.7MB/s."""
    _wave(conn, "b1", kbps=HELD_B1, behind={SERIAL: B1_BEHIND}, counters=_metal(3))
    got, label = _ask(conn, "b2", base=HELD_B1)
    assert (got, label) == (HELD_B1, "HOLD behind_negligible")
    [row] = _rows(conn)
    assert row["verdict"] == "HOLD" and row["reason"].endswith("behind_negligible")
    prev = json.loads(row["evidence_json"])[0]["previous"]
    assert prev["skipped"] == {SERIAL: 3} and prev["behind"] == {SERIAL: B1_BEHIND}      # the evidence the decision stood on


def test_a1_of_the_metal_25_chunks_is_a_step_not_a_quarter(conn):
    """a1: 25 of 18,250 (1.37 permille, over the threshold): a cut, graded by the share. Before: 1386000 -> 1039500 (75%)."""
    _wave(conn, "a1", behind={SERIAL: A1_BEHIND}, counters=_metal(25))
    got, label = _ask(conn, "a2")
    assert label == "CUT drive_behind"
    assert got == int(CHECK * (1 - 25 / 18_250) * MARGIN) == 1_245_691
    assert got > CHECK * chunk_rate.CUT_PERCENT // 100              # not the flat quarter
    assert got < CHECK * (100 - chunk_rate.WRITE_STEP_PERCENT + 1) // 100       # a real cut: about one step (10.1%)


def test_the_metals_two_cuts_were_over_a_negligible_and_a_marginal_lag_and_neither_is_the_old_25_percent(conn):
    cuts = {}
    for name, behind, skipped in (("a1", A1_BEHIND, 25), ("b1", B1_BEHIND, 3)):
        _wave(conn, name, kbps=HELD_B1, behind={SERIAL: behind}, counters=_metal(skipped))
        cuts[name] = _ask(conn, f"{name}-next", base=HELD_B1)[0]
    assert cuts["b1"] == HELD_B1 and cuts["a1"] < HELD_B1 and min(cuts.values()) > HELD_B1 * 75 // 100


# --- proportional to the wave ---------------------------------------------------------------------------------


@pytest.mark.parametrize("durable, skipped, verdict", [
    (18_244, 3, "HOLD"),             # b1
    (18_244, 18, "HOLD"),            # 0.99 permille: under
    (18_244, 19, "CUT"),             # 1.04 permille: over
    (4_995, 5, "CUT"),               # exactly 1 permille: the threshold itself cuts
    (4_996, 5, "HOLD"),              # one durable chunk more: 0.9998 permille
    (500, 1, "CUT"),                 # a small image: ONE chunk is 2 permille. A fixed count would have held it
    (199_000, 190, "HOLD"),          # a big image: 190 chunks is 0.95 permille. A fixed count would have cut it
])
def test_the_threshold_is_a_share_of_the_chunks_of_the_pass_not_a_count(conn, durable, skipped, verdict):
    _wave(conn, "w1", behind={SERIAL: _pass(durable)}, counters={(3, 1): {SERIAL: skipped}})
    assert _ask(conn, "w2")[1].split()[0] == verdict


def test_the_cut_deepens_with_the_share_and_never_cuts_more_than_the_drive_missed(conn):
    """held x (1 - share) x margin: the drive made `1 - share` of what was offered durable; the margin is the one the check gets."""
    lags = ((19, 18_244), (180, 18_244), (1_000, 18_000), (4_600, 18_400), (9_000, 9_000))
    got = []
    for n, (skipped, durable) in enumerate(lags):
        _wave(conn, f"w{n}", behind={SERIAL: _pass(durable)}, counters={(3, 1): {SERIAL: skipped}})
        got.append(_ask(conn, f"x{n}")[0])
    assert got == sorted(got, reverse=True) and len(set(got)) == len(got)
    for kbps, (skipped, durable) in zip(got, lags):
        assert kbps == int(CHECK * (1 - skipped / (durable + skipped)) * MARGIN)
    assert got[-1] == int(CHECK * 0.5 * MARGIN)            # half of the chunks skipped: half of the rate, under the margin


def test_a_behind_whose_bytes_are_the_whole_pass_is_not_what_the_drive_wrote(conn):
    """(d) `behind` of the metal is 201MB/s over a 173MB/s ask: not a write rate. With a real lag (10% skipped) the cut is the share's,
    NOT the 75% fallback the old code made of a number it could not use."""
    whole = {"bytes": 76_441_190_400, "seconds": 379.1}       # over `held`: `_behind_kbps` is None here, as on the metal
    assert chunk_rate._behind_kbps(whole, chunk_rate.config(None)) > CHECK
    _wave(conn, "w1", behind={SERIAL: whole}, counters={(3, 1): {SERIAL: 2_025}})        # 2,025 of 20,250: 10%
    got, label = _ask(conn, "w2")
    assert label == "CUT drive_behind" and got == int(CHECK * 0.9 * MARGIN) and got > CHECK * 75 // 100


def test_a_sender_slower_than_held_is_not_a_drive_that_wrote_less():
    """The old rule cut to `bytes / seconds` (60MB/s: well under `held`) for any skipped chunk. The sender did not offer more than
    that -- the drive made durable all but 2 of 4,293 chunks: negligible. (The pure function: no proven ceiling in it.)"""
    slow = {"bytes": 18_000_000_000, "seconds": 300.0}                 # 60MB/s, like `WAS` of the 3506 tests
    cfg = chunk_rate.config(None)
    assert chunk_rate._behind_kbps(slow, cfg) < CHECK
    prev = {"at": "2026-11-16T04:00:00+00:00", "write_kbps": CHECK, "write_serials": [SERIAL], "behind": {SERIAL: slow}, "skipped": {SERIAL: 2}}
    got = chunk_rate.write_next(prev, CHECK, [SERIAL], None, cfg, chunk_rate._epoch("2026-11-16T04:01:00+00:00"))
    assert (got["verdict"], got["why"], got["kbps"]) == ("HOLD", "behind_negligible", CHECK)
    got = chunk_rate.write_next({**prev, "skipped": {}}, CHECK, [SERIAL], None, cfg, chunk_rate._epoch("2026-11-16T04:01:00+00:00"))
    assert (got["verdict"], got["why"], got["kbps"]) == ("CUT", "drive_behind", chunk_rate._behind_kbps(slow, cfg))     # no count: the old rule


# --- not measured -----------------------------------------------------------------------------------------------


def test_without_the_counters_and_without_a_usable_rate_it_is_one_step_not_a_quarter(conn):
    _wave(conn, "w1", behind={SERIAL: {"bytes": B1_BEHIND["bytes"], "seconds": 30.0}})        # a pass too short to tell
    got, label = _ask(conn, "w2")
    assert (got, label) == (CHECK * (100 - chunk_rate.WRITE_STEP_PERCENT) // 100, "CUT drive_behind_unmeasured")
    assert got > CHECK * chunk_rate.CUT_PERCENT // 100


def test_without_the_counters_the_old_rate_rule_still_works_when_it_gives_a_number(conn):
    """Guard: a wave that has no `chunk_pass_counters` rows (an old wave) is cut to what the drive wrote x the margin, as before."""
    _wave(conn, "w1", kbps=1654400, behind={SERIAL: {"bytes": 18_000_000_000, "seconds": 300.0}})
    assert _ask(conn, "w2") == (432000, "CUT drive_behind")


@pytest.mark.parametrize("counters", [
    {(3, 1): None},                              # the machine did not say what it skipped
    {(3, 1): {SERIAL: None}},                    # it said it, but not for this drive
    {(3, 1): {SERIAL: 0}},                       # `behind` says it was behind: a count of 0 contradicts it
    {(3, 1): {SERIAL: 7}, (4, 1): {SERIAL: None}},     # one pass of the drive was not counted: the sum is not known
    {(3, 2): {SERIAL: 7}},                       # a count of a repair pass is not the first pass's
])
def test_a_count_that_is_not_the_first_passes_is_not_a_measurement(conn, counters):
    _wave(conn, "w1", behind={SERIAL: B1_BEHIND}, counters=counters)
    assert _ask(conn, "w2")[1] == "CUT drive_behind_unmeasured"


def test_the_skipped_chunks_of_all_the_partitions_add_up(conn):
    """Two passes the drive was behind in: 4 + 4 of ~18,244 durable in each -- the share is of both passes' chunks."""
    twice = {"bytes": 2 * 18_244 * CHUNK, "seconds": 758.0}
    _wave(conn, "w1", behind={SERIAL: twice}, counters={(3, 1): {SERIAL: 20}, (5, 1): {SERIAL: 20}})
    got, label = _ask(conn, "w2")
    assert label == "CUT drive_behind" and got == int(CHECK * (1 - 40 / (2 * 18_244 + 40)) * MARGIN)


# --- more than one drive ----------------------------------------------------------------------------------------


def test_the_deepest_lag_of_the_drives_decides_and_all_negligible_holds(conn):
    two = ("S1", "S2")
    behind = {"S1": _pass(18_244), "S2": _pass(15_000)}
    _wave(conn, "w1", behind=behind, serials=two, counters={(3, 1): {"S1": 3, "S2": 1_000}})
    got, label = _ask(conn, "w2", serials=two)
    assert label == "CUT drive_behind" and got == int(CHECK * (1 - 1_000 / 16_000) * MARGIN)
    _wave(conn, "w3", behind=behind, serials=two, counters={(3, 1): {"S1": 3, "S2": 2}})
    assert _ask(conn, "w4", serials=two) == (CHECK, "HOLD behind_negligible")


def test_a_lag_of_a_drive_that_is_not_in_this_wave_cuts_nothing(conn):
    _wave(conn, "w1", behind={"S9": B1_BEHIND}, counters={(3, 1): {"S9": 5_000}})
    assert _ask(conn, "w2")[0] == CHECK * 110 // 100


# --- guards, and the pieces -------------------------------------------------------------------------------------


def test_a_drive_that_kept_up_still_raises_and_a_failing_counter_read_is_unmeasured_not_a_failure(conn):
    """Guard: no `behind` -> the climb of the loop as before. And a table that cannot be read leaves the lag unmeasured (one step),
    never the check."""
    _wave(conn, "w1")
    assert _ask(conn, "w2") == (CHECK * 110 // 100, "RAISE kept_up_and_held_by_cap")
    _wave(conn, "w3", behind={SERIAL: B1_BEHIND})
    conn.execute("DROP TABLE chunk_pass_counters")
    assert _ask(conn, "w4") == (CHECK * 90 // 100, "CUT drive_behind_unmeasured")


def test_the_ceiling_of_a_proven_drive_still_caps_a_graded_cut_and_the_floor_of_one_is_one(conn):
    got = chunk_rate.write_next({"at": "2026-11-16T04:00:00+00:00", "write_kbps": CHECK, "write_serials": [SERIAL],
                                 "behind": {SERIAL: {"bytes": 4 * CHUNK, "seconds": 1.0}}, "skipped": {SERIAL: 10 ** 9}},
                                CHECK, [SERIAL], None, chunk_rate.config(None), chunk_rate._epoch("2026-11-16T04:01:00+00:00"))
    assert got["verdict"] == "CUT" and got["kbps"] >= 1
    lowered = chunk_rate.write_next({"at": "2026-11-16T04:00:00+00:00", "write_kbps": CHECK, "write_serials": [SERIAL],
                                     "behind": {SERIAL: B1_BEHIND}, "skipped": {SERIAL: 25}},
                                    CHECK, [SERIAL], 1_000_000, chunk_rate.config(None), chunk_rate._epoch("2026-11-16T04:01:00+00:00"))
    assert (lowered["verdict"], lowered["why"], lowered["kbps"]) == ("CUT", "drive_behind+sustained", 1_000_000)


def test_the_constants_are_pinned_to_what_they_are_copies_of():
    """(guard) `CHUNK_BYTES` is the engine's; the threshold is the share of the network loss that cuts the wave rate."""
    assert chunk_rate.CHUNK_BYTES == chunksend.CHUNK_BYTES
    assert chunk_rate.BEHIND_PERMILLE == chunk_rate.LOSS_PERMILLE == 1
    assert chunk_rate.config(None).behind_permille == chunk_rate.BEHIND_PERMILLE
