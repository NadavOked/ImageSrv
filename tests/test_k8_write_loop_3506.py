"""#3506: `--write-kbps` is a loop between the waves, not the short write check alone.

LabRoot, 15/11 (`lab/metal-1115-compare`): the new engine went out with `--write-kbps 1504000` (188MB/s = 0.9 x the write check, 209MB/s) in
every run, and `chunkcast send` slept 74% of the time in `hrtimer_nanosleep` -- held by that cap -- while the library disk was
idle 64% of the stream. The old receiver wrote 216MB/s on average to the same drive. The cap was a guess from a short check.

`chunk_rate.write_rate` (K8, `enforce`, hooked into `ChunkEngine._write_kbps`) takes the check as the START, and then: the drives of
the wave kept up (every machine told what it skipped) AND the sender slept for `--write-kbps` for a quarter of the first pass
or more (`paced.disk`) -> the next wave asks one step more; a drive of the wave fell behind (the receiver skipped chunks for it) -> what it
wrote x the margin; anything else, or not measured, holds. A drive proven to write less (`write_history`) is a ceiling. Every
decision is a `brain_decisions` row (`WAVE_WRITE_RATE`) with the evidence.

**Negative control:** `server/rounds/chunk_rate.py`, `server/rounds/chunksend.py`, `server/rounds/chunkpass.py` and `server/app.py` back
at the base: `write_rate` does not exist, the engine neither asks for it nor reports `write_kbps`, and the app does not wire it -- every
test fails on behaviour, except the one marked (guard): a hook that fails or answers nonsense is the rate of the check, in both states.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, chunksend, drive_rates

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import room_server                   # noqa: F401 -- shared fixture

MAC = "aa:bb:cc:00:00:31"
MODEL = "SAMSUNG MZ7LN256"
CHUNK = 4 * 1024 * 1024
CHUNKS = 5000
CHECK = 1504000                    # kbit/s: 0.9 x the 209MB/s write check of the metal (188MB/s), what the first wave asks
ASLEEP = 0.74                      # the metal: the sender slept 74% of the time for its pacing
WAS = {"bytes": 18_000_000_000, "seconds": 300.0}      # a drive that wrote 60MB/s (a number for the test) while behind


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives():
    return [{"serial": "S1", "model": MODEL, "mac": MAC, "port": 1}]


def _wave(conn, sid, *, kbps=CHECK, serials=("S1",), asleep=ASLEEP, lost=0, behind=None, ended="send_done", seconds=600.0, skipped=None):
    """A chunk wave that ended, by the server's own path (`decide`, then `record` of the engine's outcome). `kbps`/`serials`:
    what `rate_outcome` says `--write-kbps` was and whose check set it (None = the engine did not say), `asleep`: the share of the
    first pass the sender slept for `--write-kbps` (None = not read), `lost`: None = a machine did not say what it skipped."""
    drive_rates.decide(conn, sid, _drives, None, adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives))
    outcome = {"chunks": CHUNKS, "bytes": CHUNKS * CHUNK, "first_pass_s": seconds, "lost_chunks": lost, "away": 0,
               "cloners": 1, "repair_rounds": 0, "ended": ended}
    if asleep is not None:
        outcome["paced_ms"] = {"wire": 0, "disk": int(seconds * 1000 * asleep)}
    if kbps is not None:
        outcome.update(write_kbps=kbps, write_serials=list(serials))
    if behind is not None:
        outcome["behind"] = behind
    chunk_rate.record(conn, sid, outcome)
    if skipped is not None:       # #3530: `chunk_pass_counters` of the wave (pass 1): the chunks skipped for S1
        conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                     " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')", (sid, 3, 1, MAC, "receive", skipped, json.dumps({"S1": skipped})))
        conn.commit()


def _ask(conn, sid, base=CHECK, serials=("S1",), now=None) -> tuple[int, str | None]:
    return chunk_rate.write_rate(conn, sid, 3, base, list(serials), now)


def _rows(conn) -> list:
    return conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_WRITE_RATE' ORDER BY id").fetchall()


def test_the_scenario_of_the_metal_the_cap_held_the_sender_and_the_drive_kept_up_so_the_next_wave_asks_more(conn):
    _wave(conn, "w1")
    got, label = _ask(conn, "w2")
    assert got == CHECK * (100 + chunk_rate.WRITE_STEP_PERCENT) // 100 == 1654400 and got > CHECK
    assert label == "RAISE kept_up_and_held_by_cap"
    [row] = _rows(conn)
    assert (row["mode"], row["verdict"], row["round_id"], row["outcome"]) == ("enforce", "RAISE", "w2", "applied")
    [evidence] = json.loads(row["evidence_json"])
    assert evidence["kbps"] == got and evidence["check_kbps"] == CHECK and evidence["serials"] == ["S1"]
    assert evidence["previous"]["session_id"] == "w1" and evidence["previous"]["write_kbps"] == CHECK
    assert evidence["previous"]["paced_ms"] == {"wire": 0, "disk": int(600 * 1000 * ASLEEP)}


def test_the_climb_goes_on_wave_after_wave_from_what_the_last_wave_went_out_at_not_from_the_check(conn):
    _wave(conn, "w1")
    rates = []
    for n in (2, 3, 4):
        got, _label = _ask(conn, f"w{n}")                 # the check says CHECK every time: only the loop moves
        rates.append(got)
        _wave(conn, f"w{n}", kbps=got)
    assert rates == [1654400, 1819840, 2001824] and all(a < b for a, b in zip(rates, rates[1:]))


def test_a_drive_that_fell_behind_pulls_the_rate_down_to_what_it_wrote(conn):
    _wave(conn, "w1", kbps=1654400, behind={"S1": WAS})
    got, label = _ask(conn, "w2")
    assert got == 432000 and label == "CUT drive_behind"             # 60MB/s x 8000 x 0.9
    [row] = _rows(conn)
    assert row["verdict"] == "CUT" and json.loads(row["evidence_json"])[0]["previous"]["behind"] == {"S1": WAS}


def test_a_drive_behind_in_a_pass_too_short_to_tell_cuts_one_step_not_a_quarter(conn):
    """#3526: no number (no count of the chunks skipped, no usable rate) is not made up as 25%: one step down, said as `drive_behind_unmeasured`."""
    _wave(conn, "w1", behind={"S1": {"bytes": WAS["bytes"], "seconds": 30.0}})
    assert _ask(conn, "w2") == (CHECK * (100 - chunk_rate.WRITE_STEP_PERCENT) // 100, "CUT drive_behind_unmeasured")


def test_a_drive_that_is_not_in_this_wave_does_not_cut_it(conn):
    """#3295: a drive left out of `--write-kbps` (the slow one) fell behind -- it does not pull the others down."""
    _wave(conn, "w1", behind={"S2": WAS})
    assert _ask(conn, "w2")[0] == CHECK * 110 // 100


def test_without_a_measurement_the_write_check_is_the_start_and_nothing_else(conn):
    assert _ask(conn, "w1") == (CHECK, None)
    [row] = _rows(conn)
    assert row["verdict"] == "PROBE" and json.loads(row["evidence_json"])[0]["previous"] is None


@pytest.mark.parametrize("wave, ask, verdict, why, expected", [
    ({"lost": None}, {}, "HOLD", "kept_up_not_measured", CHECK),                   # a machine did not say what it skipped
    ({"asleep": None}, {}, "HOLD", "sender_not_measured", CHECK),                   # the sender's sleep was not read
    ({"asleep": 0.10}, {}, "HOLD", "cap_did_not_hold_sender", CHECK),               # something else held the sender
    ({"asleep": 0.24}, {}, "HOLD", "cap_did_not_hold_sender", CHECK),               # just under the share
    ({"asleep": 0.25}, {}, "RAISE", "kept_up_and_held_by_cap", CHECK * 110 // 100),     # exactly the share
    ({"serials": ("S1", "S2")}, {}, "PROBE", "other_drives", CHECK),                # the evidence is about other drives
    ({"kbps": None}, {}, "PROBE", "other_drives", CHECK),                           # the engine did not say what it asked
    ({"ended": "send_failed"}, {}, "PROBE", "no_previous_wave", CHECK),             # a wave that failed proves nothing
    ({}, {"base": CHECK * 40 // 100}, "PROBE", "check_far_below", CHECK * 40 // 100),    # today's check is not this drive's
    ({}, {"now_after_hours": 13}, "PROBE", "stale", CHECK),
])
def test_what_is_not_measured_or_not_about_these_drives_does_not_move_the_rate(conn, wave, ask, verdict, why, expected):
    import time
    _wave(conn, "w1", **wave)
    now = None if "now_after_hours" not in ask else time.time() + ask["now_after_hours"] * 3600
    got, label = _ask(conn, "w2", base=ask.get("base", CHECK), now=now)
    assert (got, [r["verdict"] for r in _rows(conn)]) == (expected, [verdict]), json.loads(_rows(conn)[0]["evidence_json"])
    assert _rows(conn)[0]["reason"].endswith(why)


def test_a_drive_proven_to_write_less_than_its_check_is_a_ceiling(conn):
    """`write_history` (#3295): the drive wrote 61MB/s in a wave it fell behind in -- the check's 188MB/s is not for it."""
    _wave(conn, "w0", kbps=None, behind={"S1": {"bytes": 18_300_000_000, "seconds": 300.0}}, skipped=637)     # #3530: 637 of the 5,000 chunks skipped
    assert chunk_rate.write_history(conn, "S1") == (61, "w0")
    ceiling = int(61 * 8000 * writerate.MARGIN)
    assert _ask(conn, "w1") == (ceiling, "CUT other_drives+sustained")       # no loop evidence yet: the check, under the ceiling
    _wave(conn, "w1", kbps=ceiling - 20000)
    assert _ask(conn, "w2") == (ceiling, "RAISE kept_up_and_held_by_cap+sustained")     # one step, but not past what it wrote
    _wave(conn, "w2", kbps=ceiling)
    got, label = _ask(conn, "w3")
    assert got == ceiling and label.startswith("HOLD at_proven_ceiling")


def test_with_the_automatic_rate_switched_off_nothing_is_decided(conn):
    """`learned_rate_enabled` off -- the check, and no row."""
    _wave(conn, "w1")
    db.set_setting(conn, drive_rates.SETTING, "false")
    assert _ask(conn, "w2") == (CHECK, None) and _rows(conn) == []


def test_a_failure_in_the_loop_is_the_rate_of_the_check():
    """The learning never stops a wave: a connection that fails is the check."""
    assert chunk_rate.write_rate(None, "w1", 3, CHECK, ["S1"]) == (CHECK, None)


def test_sample_of_keeps_what_the_engine_said_and_drops_a_malformed_answer():
    base = {"chunks": 10, "lost_chunks": 0, "first_pass_s": 100, "bytes": 10, "stream_zbytes": 5, "ended": "send_done"}
    got = chunk_rate.sample_of(1000, {**base, "write_kbps": CHECK, "write_serials": ["B", "A"]})
    assert (got["write_kbps"], got["write_serials"]) == (CHECK, ["A", "B"])
    for bad in ({"write_kbps": 0}, {"write_kbps": True}, {"write_kbps": "9"}, {"write_serials": []}, {"write_serials": [1]},
                {"write_serials": "A"}, {}):
        got = chunk_rate.sample_of(1000, {**base, **bad})
        assert (got["write_kbps"], got["write_serials"]) == (None, None), bad


def test_the_margin_of_the_loop_is_the_margin_of_the_check_and_the_held_share_is_the_one_of_3498():
    """The loop has no number of its own that drifts from the rule it sits next to."""
    assert chunk_rate.config(None).write_margin == writerate.MARGIN
    assert chunk_rate.WRITE_HELD_PERCENT == 100 - chunk_rate.CUT_PERCENT


# --- the engine --------------------------------------------------------------------------------------------------


def _listen(rig, mbps=120):
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=mbps)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    return int(cmd[cmd.index("--write-kbps") + 1])


def _finish(rig, engine):
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": ALL}, skipped={"C1": 0})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_the_engine_asks_the_loop_with_the_check_and_the_drives_that_set_it_and_goes_out_at_its_answer(rig):
    asked = []
    engine = rig.make()
    engine.write_rate = lambda *a: (asked.append(a), (2 * a[2], "RAISE test"))[1]
    got = _listen(rig)
    check = int(120 * writerate.MARGIN) * 8000
    assert asked == [(SID, 3, check, ["A1", "B1", "C1"])] and got == 2 * check
    [said] = rig.named("chunk_write_rate")
    assert "RAISE test" in said and f"--write-kbps {2 * check}" in said
    _finish(rig, engine)
    outcome = engine.rate_outcome(SID)
    assert outcome["write_kbps"] == 2 * check and outcome["write_serials"] == ["A1", "B1", "C1"]


def test_the_engine_reports_what_the_write_rate_was_even_when_nothing_moved_it(rig):
    engine = rig.make()
    assert _listen(rig) == int(120 * writerate.MARGIN) * 8000
    _finish(rig, engine)
    outcome = engine.rate_outcome(SID)
    assert outcome["write_kbps"] == int(120 * writerate.MARGIN) * 8000 and outcome["write_serials"] == ["A1", "B1", "C1"]


def test_the_loop_never_takes_the_rate_under_the_floor(rig):
    engine = rig.make()
    engine.write_rate = lambda *a: (1000, "CUT test")
    assert _listen(rig) == 50 * 8000                       # the rig's floor, 50MB/s


@pytest.mark.parametrize("answer", [lambda *a: 1 / 0, lambda *a: 5, lambda *a: (0, "x"), lambda *a: (9, 1), lambda *a: (True, None)])
def test_a_failing_or_nonsense_loop_is_the_rate_of_the_check(rig, answer):
    """(guard) The learning never stops a wave: a failing or nonsense hook is the rate of the check, with or without the loop."""
    engine = rig.make()
    engine.write_rate = answer
    assert _listen(rig) == int(120 * writerate.MARGIN) * 8000
    engine.stop()


def test_a_rate_no_drive_check_set_is_not_asked_of_the_loop_and_is_not_evidence(rig):
    """The floor (a drive that was not measured) is not a measurement: no loop, and `rate_outcome` says nothing about it."""
    asked = []
    engine = rig.make()
    engine.write_rate = lambda *a: (asked.append(a), (1, "x"))[1]
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=None)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert asked == []
    _finish(rig, engine)
    assert engine.rate_outcome(SID)["write_kbps"] is None and engine.rate_outcome(SID)["write_serials"] is None


def test_the_server_wires_the_write_loop_into_its_chunk_engine(room_server):
    """(neg) `server.app` gives the engine `chunk_rate.write_rate`; with no past wave it is the check."""
    ctx = room_server["ctx"]
    hook = getattr(ctx.chunk, "write_rate", None)
    assert hook is not None
    assert hook("ses_none", 3, CHECK, ["S1"]) == (CHECK, None)
    assert [r["verdict"] for r in _rows(ctx.conn)] == ["PROBE"]
