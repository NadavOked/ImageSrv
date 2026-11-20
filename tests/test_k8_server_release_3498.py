"""#3498: K8 stayed on a cut (`chunk_down`) that one wave gave it, for ever, because the waves after it taught nothing.

LabRoot, 15/11 (`brain_decisions`): the warm-up ended with a cut 892m -> 669m (23 chunks completed over HTTP). Every wave
after it: `WAVE_START KEEP 669m(chunk_down) -> 669m: fresh_matching` and `WAVE_RATE_LEARN UNLEARNED server_disk`. The
engine sent ~312Mb/s -- far under the 669m ask: the limit was the read of the library disk (~35MB/s), not the rate.
`adjust` takes the rate from `last_sample`, the newest wave that TAUGHT; a `server_disk` wave teaches nothing, so it
never replaced the cut, and the cut never expired.

The fix (`chunk_rate.server_release`): the cut is dropped when the newest `RELEASE_WAVES` waves after it ALL show both
(1) the measured limit is `server_disk` AND the sender did not sleep for its own pacing for more than `100 - CUT_PERCENT`
percent of the first pass (`paced_ms`; metal-1115-compare showed `server_disk` on waves where the disk was idle 64% of the
time and the sender asleep 74% of it -- the label alone is not trusted) and (2) the rate actually sent is far from the ask
(under `CUT_PERCENT` of it).
Then the next wave goes one step up (`STEP_MBPS`) from the rate the newest wave went out at, wave after wave, never above
the cap (`basis: "chunk_release"`), and the decision and its evidence are in `wave_rates.decision.start_check.release`
and in K8's `WAVE_START` row. Anything else keeps the cut as it was:

- real loss: a wave that teaches (the network's loss) cuts from itself; `chunk_net_gap` or a stream near the ask keep it;
- not measured (the rate sent, the limit): `UNDECIDED` / no `server_disk` -- today's behaviour, principle 5.

**Negative control:** `server/rounds/chunk_rate.py` back to the base -- the scenario of the metal and the gradual climb
fail (the rate stays on the cut); the tests that say "the cut is kept" pass on both sides, by design: they guard the
other half of the rule.
No test here sleeps; the clock of `start` is the real one and the wave is fresh.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.rounds import chunk_rate, drive_rates

MB = 10 ** 6
MAC, OTHER = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22"
MODEL = "SAMSUNG MZ7LN256"
CHUNK = 4 * 1024 * 1024
CHUNKS = 5000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives():
    return [{"serial": "D1", "model": MODEL, "mac": MAC, "port": 1},
            {"serial": "D2", "model": MODEL, "mac": OTHER, "port": 1}]


def _pressure(conn, sid, held: bool):
    """The wave's `server_io` row: the library disk `held` the stream back (`server_disk`), or it was fine (`ok`)."""
    basis = "server_disk" if held else "ok"
    result = {"util": 0.4 if held else 0.01, "await_ms": 20.0 if held else 0.5, "psi_some": 0.1, "psi_full": 0.01,
              "basis": basis}
    conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, 'x', '{}', 'x', ?, ?)", (sid, json.dumps(result), basis))
    conn.commit()


def _wave(conn, sid, *, held=None, lost=0, share=None, away=0, asleep=0.05) -> int:
    """One chunk wave, decided by `adjust` and recorded: `held` = the library disk held it (None = `server_io` did not
    measure), `lost` = chunks missing from the first pass, `share` = the part of the ask the engine really sent (None =
    not measured), `away` = a machine was lost in the first pass, `asleep` = the part of the first pass the sender slept for
    its own pacing (`paced`: `--kbps` and `--write-kbps`; None = not measured). Returns the rate it went out at."""
    rate = drive_rates.parse_bitrate(drive_rates.decide(
        conn, sid, _drives, None, adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives)))
    if held is not None:
        _pressure(conn, sid, held)
    size = CHUNKS * CHUNK
    outcome = {"chunks": CHUNKS, "bytes": size, "first_pass_s": size * 8 / rate, "lost_chunks": lost, "away": away,
               "cloners": 2, "repair_rounds": 0, "ended": "send_done"}
    if asleep is not None:
        outcome["paced_ms"] = {"wire": int(outcome["first_pass_s"] * 1000 * asleep), "disk": 0}
    if share is not None:
        outcome["stream_zbytes"] = int(size * share)       # sent = zbytes * 8 / seconds = share * rate
    chunk_rate.record(conn, sid, outcome)
    return rate


def _basis(conn, sid) -> str:
    return drive_rates.decision(conn, sid)["basis"]


def _cut_by_a_lossy_wave(conn) -> int:
    """The warm-up of the metal: a wave that teaches and lost chunks; returns the rate it asked."""
    asked = _wave(conn, "w1", held=False, lost=50, share=0.9)
    conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES ('w1','D1',?,?,60,'x')",
                 (MODEL, MAC))
    conn.commit()
    return asked


def test_the_scenario_of_the_metal_a_cut_that_the_server_disk_explains_is_dropped(conn):
    """`chunk_down`, then waves the library disk held back, with the stream at half of the ask: K8 tries to climb."""
    first = _cut_by_a_lossy_wave(conn)
    cut = _wave(conn, "w2", held=True, share=0.5)
    assert cut == int(first * chunk_rate.CUT_PERCENT / 100) and _basis(conn, "w2") == "chunk_down"
    assert _wave(conn, "w3", held=True, share=0.5) == cut and _basis(conn, "w3") == "chunk_down"   # one wave: a pulse
    assert chunk_rate.last_sample(conn)["session_id"] == "w1"                                       # the cut still stands
    third = _wave(conn, "w4", held=True, share=0.5)                                                  # two in a row
    assert third == cut + chunk_rate.STEP_MBPS * MB and _basis(conn, "w4") == "chunk_release"
    release = drive_rates.decision(conn, "w4")["previous"]["release"]
    assert release["verdict"] == "RELEASE" and [w["session_id"] for w in release["waves"]] == ["w3", "w2"]
    assert all(w["skip"] == "server_disk" and w["sent_bps"] < w["asked_bps"] / 2 + 1 for w in release["waves"])


def test_the_climb_is_gradual_and_stops_at_the_cap(conn):
    _cut_by_a_lossy_wave(conn)
    rates = [_wave(conn, f"w{n}", held=True, share=0.5) for n in range(2, 12)]
    cap = chunk_rate.cap_of(conn, drive_rates.decision(conn, "w11"))[0]
    assert rates[0] == rates[1] < rates[2] < rates[3] < rates[4]                  # the cut, then step by step
    assert rates[3] - rates[2] == chunk_rate.STEP_MBPS * MB
    assert max(rates) == cap and rates[-1] == cap                                  # and never above it
    assert all(r <= cap for r in rates)


def test_the_decision_is_recorded_with_its_evidence(conn):
    _cut_by_a_lossy_wave(conn)
    for n in (2, 3):
        _wave(conn, f"w{n}", held=True, share=0.5)
    rate = _wave(conn, "w4", held=True, share=0.5)
    assert chunk_rate.start(conn, "w4", rate, {(MAC, "D1"): 60, (OTHER, "D2"): 62}) == rate
    check = drive_rates.decision(conn, "w4")["start_check"]
    assert check["why"] == ["fresh_matching"] and check["release"]["verdict"] == "RELEASE"
    (row,) = [r for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_START'"
                                      " AND round_id = 'w4'")]
    assert "chunk_release" in row["reason"] and "release=RELEASE" in row["reason"] and row["mode"] == "enforce"
    assert json.loads(row["evidence_json"])[0]["release"]["waves"][0]["session_id"] == "w3"


def test_a_sender_that_slept_for_its_own_pacing_is_not_held_by_the_library_disk(conn):
    """The finding of 15/11 (metal-1115-compare): the new engine's waves were labelled `server_disk` (util 30-34%, await
    15.5ms over the whole wave) while the library disk was idle 64% of the stream and the sender slept 74% of it, held by
    `--write-kbps`. The label alone is not the evidence: the cut stays, and the decision says why."""
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    rates = [_wave(conn, f"w{n}", held=True, share=0.47, asleep=0.74) for n in range(2, 8)]
    assert rates == [cut] * 6 and _basis(conn, "w7") == "chunk_down"
    release = drive_rates.decision(conn, "w7")["previous"]["release"]
    assert release["verdict"] == "KEEP" and release["why"] == "sender_paced"


def test_a_sender_that_did_not_report_its_sleep_is_undecided_not_the_disk(conn):
    """No `paced` line (an older sender, a log that was not read): "not asleep" cannot be said (principle 5)."""
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    rates = [_wave(conn, f"w{n}", held=True, share=0.5, asleep=None) for n in range(2, 7)]
    assert rates == [cut] * 5
    release = drive_rates.decision(conn, "w6")["previous"]["release"]
    assert release["verdict"] == "UNDECIDED" and release["why"] == "sender_not_measured"


def test_a_stream_near_the_ask_keeps_the_cut(conn):
    """The disk held the wave back but the engine still sent most of what it was asked: the rate may be the limit."""
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    rates = [_wave(conn, f"w{n}", held=True, share=0.9) for n in range(2, 7)]
    assert rates == [cut] * 5 and _basis(conn, "w6") == "chunk_down"
    assert drive_rates.decision(conn, "w6")["previous"]["release"]["why"] == "rate_near_ceiling"


def test_a_lost_machine_in_the_wave_is_the_networks_and_keeps_the_cut(conn):
    """`chunk_net_gap` (a disconnect): not `server_disk`, whatever the rate."""
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    rates = [_wave(conn, f"w{n}", held=True, share=0.5, away=1) for n in range(2, 7)]
    assert rates == [cut] * 5
    assert drive_rates.decision(conn, "w6")["previous"]["release"]["why"] == "not_server_disk"


def test_one_wave_that_is_not_the_servers_among_the_newest_keeps_the_cut(conn):
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    _wave(conn, "w2", held=True, share=0.5)
    _wave(conn, "w3", held=True, share=0.5, away=1)
    assert _wave(conn, "w4", held=True, share=0.5) == cut


def test_a_wave_that_taught_a_real_loss_cuts_from_itself_and_the_release_starts_over(conn):
    """Real loss on a healthy disk is what K8 learns from: it cuts again, and the old waves are not evidence any more."""
    _cut_by_a_lossy_wave(conn)
    for n in (2, 3):
        _wave(conn, f"w{n}", held=True, share=0.5)
    _wave(conn, "w4", held=True, share=0.5)
    assert _basis(conn, "w4") == "chunk_release"
    lossy = _wave(conn, "w5", held=False, lost=50, share=0.9)                   # the network loses chunks: teaches
    again = _wave(conn, "w6", held=True, share=0.5)
    assert again == lossy * chunk_rate.CUT_PERCENT // 100 // MB * MB             # cut from w5; the old waves are not evidence
    assert _basis(conn, "w6") == "chunk_down"
    assert drive_rates.decision(conn, "w6")["previous"]["release"]["why"] == "few_waves"


def test_a_rate_that_was_not_measured_is_undecided_and_changes_nothing(conn):
    """The engine did not know the sizes (`stream_zbytes` None): "not measured" is not "far from the ask"."""
    first = _cut_by_a_lossy_wave(conn)
    cut = int(first * chunk_rate.CUT_PERCENT / 100)
    rates = [_wave(conn, f"w{n}", held=True, share=None) for n in range(2, 7)]
    assert rates == [cut] * 5 and _basis(conn, "w6") == "chunk_down"
    assert drive_rates.decision(conn, "w6")["previous"]["release"]["verdict"] == "UNDECIDED"


def test_a_limit_that_was_not_measured_is_not_the_server(conn):
    """No `server_io` row (the library disk was not read): the waves teach, as before; nothing is released."""
    _cut_by_a_lossy_wave(conn)
    for n in (2, 3, 4):
        _wave(conn, f"w{n}", held=None, lost=50, share=0.5)
    assert _basis(conn, "w4") == "chunk_down" and chunk_rate.last_sample(conn)["session_id"] == "w4"


def test_a_cut_that_is_not_there_is_not_released(conn):
    """Waves that never cut have no release to decide: the basis of today (`chunk_hold` / `chunk_up`)."""
    _wave(conn, "w1", held=False, lost=0, share=0.9)
    _wave(conn, "w2", held=True, share=0.5)
    _wave(conn, "w3", held=True, share=0.5)
    _wave(conn, "w4", held=True, share=0.5)
    assert _basis(conn, "w4") in ("chunk_hold", "chunk_up")
    assert "release" not in (drive_rates.decision(conn, "w4")["previous"] or {})
