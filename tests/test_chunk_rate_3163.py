"""#3163, #3174: the network rate of a chunk wave is learned from the waves before it.

udpcast learned its rate from what `udp-sender` counted; the chunk engine has no such counter. At the end of
every chunk wave `chunk_rate.record` keeps what the engine itself measured (the rate asked, the rate pushed,
the chunks that did not arrive in the first pass, the cloners), and before the next one `chunk_rate.adjust`
moves the rate by AIMD: a step up after a clean wave, a cut after a lossy one, the same when the wave proved
nothing -- never above the network budget, never from a wave that taught nothing (a disconnect, a wave that did
not end, the library disk that held the stream back).

**Negative control:** removing `chunk_rate.py` and the `adjust=` hook of `drive_rates.decide` makes every test here
fail on behaviour (the tests that call `decide(adjust=...)` get a `TypeError`, which does NOT count -- the ones
that count are the behavioural ones named in the PR: the rate that comes out of the wave after).
No test here sleeps: the engine tests wait on conditions (`wait_for`), and the clock is a counter the fake
`chunkcast` advances.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import budget
from server.db import set_setting
from server.rounds import chunk_rate, drive_rates

CHUNK = 4 * 1024 * 1024
MAC_A, MAC_B, MAC_C = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22", "aa:bb:cc:00:00:23"
CHUNKS = 5000                          # 21GB: long and big enough to teach (`MIN_SAMPLE_S`, `MIN_SAMPLE_BYTES`)


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives(macs):
    return [{"serial": f"S{i}-{mac}", "model": "EVO 870", "mac": mac, "port": i + 1} for i, mac in enumerate(macs)]


def outcome(asked, *, cloners=2, lost=0, pace=1.0, away=0, chunks=CHUNKS, ended="send_done", rounds=0):
    """What `ChunkEngine.rate_outcome` says of a wave. `pace` < 1: the engine pushed slower than it was asked to.
    #3302: the rate on the wire is `stream_zbytes` over the seconds; here the frames are as big as the chunks."""
    size = chunks * CHUNK
    return {"chunks": chunks, "bytes": size, "first_pass_s": size * 8 / (asked * pace), "lost_chunks": lost,
            "away": away, "cloners": cloners, "repair_rounds": rounds, "ended": ended, "stream_zbytes": size}


def wave(conn, sid, macs=(MAC_A, MAC_B), ceiling=None, link=None, **kw) -> str:
    """One whole wave the way the server runs it: the rate is chosen (`decide`, with the learning in it), then the
    wave ends and says what it saw. Returns the rate the wave went out at."""
    rate = drive_rates.decide(conn, sid, lambda: _drives(macs), ceiling, link=link,
                              adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives))
    kw.setdefault("cloners", len(macs))
    chunk_rate.record(conn, sid, outcome(drive_rates.parse_bitrate(rate), **kw))
    return rate


def basis_of(conn, sid) -> str:
    return drive_rates.decision(conn, sid)["basis"]


def taught(conn, sid) -> bool:
    return json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                                   (sid,)).fetchone()["stream"])["chunk"]["teaches"]


# --- 1. what is recorded ------------------------------------------------------------------------------------------


def test_every_wave_leaves_the_rate_it_asked_the_rate_it_pushed_the_loss_and_the_cloners(conn):
    rate = wave(conn, "w1", lost=5, pace=0.9, cloners=2, rounds=1)
    got = json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = 'w1'").fetchone()["stream"])["chunk"]
    asked = drive_rates.parse_bitrate(rate)
    assert got["asked_bps"] == asked and got["cloners"] == 2 and got["repair_rounds"] == 1
    assert got["sent_bps"] == pytest.approx(asked * 0.9, rel=1e-3)
    assert got["loss_permille"] == pytest.approx(1.0) and got["lost_chunks"] == 5 and got["chunks"] == CHUNKS
    assert got["achieved_bps"] == pytest.approx(got["sent_bps"] * 0.999, rel=1e-3)     # pushed x the share that arrived
    assert got["teaches"] is True and got["skip"] is None
    (line,) = [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'chunk_rate_recorded'")]
    assert line.startswith("w1 asked=600m") and "cloners=2" in line and line.endswith("teaches")


def test_a_wave_whose_loss_was_not_measured_records_none_not_zero(conn):
    wave(conn, "w1", lost=None)
    got = json.loads(conn.execute("SELECT stream FROM wave_rates").fetchone()["stream"])["chunk"]
    assert got["loss_permille"] is None and got["lost_chunks"] is None and got["achieved_bps"] is None


# --- 2. no history: today's rate -----------------------------------------------------------------------------------


def test_with_no_history_the_rate_is_the_one_choose_gives_today(conn):
    chosen = drive_rates.choose(conn, _drives([MAC_A]), None, "w1")
    assert chunk_rate.adjust(conn, chosen, _drives([MAC_A])) == chosen
    assert wave(conn, "w1") == "600m" and basis_of(conn, "w1") == "start"


# --- 3. up and down --------------------------------------------------------------------------------------------------


def test_the_rate_climbs_after_clean_waves_is_cut_after_a_lossy_one_and_climbs_again(conn):
    assert wave(conn, "w1") == "600m"
    assert wave(conn, "w2") == "700m" and basis_of(conn, "w2") == "chunk_up"
    assert wave(conn, "w3", lost=10) == "800m"                     # 800m asked, and 2 permille of the chunks were lost...
    assert wave(conn, "w4") == "600m" and basis_of(conn, "w4") == "chunk_down"       # ...so 800m x 0.75
    assert wave(conn, "w5") == "700m" and basis_of(conn, "w5") == "chunk_up"


def test_a_cut_is_by_the_percent_of_the_code_and_never_below_the_floor(conn, monkeypatch):
    wave(conn, "w1")
    wave(conn, "w2", lost=50)                                     # 10 permille at 700m
    assert wave(conn, "w3") == "525m"                              # x 0.75
    monkeypatch.setattr(chunk_rate, "CUT_PERCENT", 10)            # what the brain may calibrate later
    assert wave(conn, "w4", lost=50) == "625m"                     # w3 was clean: 525m + 100m
    assert wave(conn, "w5") == "150m"                              # 625 x 0.10 = 62m: the floor, 25% of the start rate (`choose`)


def test_the_loss_that_cuts_the_rate_is_the_constant_of_the_code(conn, monkeypatch):
    wave(conn, "w1", lost=10)                                      # 2 permille: over the default 1
    assert wave(conn, "w2", lost=10) == "450m"                     # cut
    monkeypatch.setattr(chunk_rate, "LOSS_PERMILLE", 5)           # the same 2 permille is now under the threshold
    assert wave(conn, "w3") == "550m" and basis_of(conn, "w3") == "chunk_up"


def test_the_step_is_the_constant_of_the_code_not_a_setting(conn, monkeypatch):
    wave(conn, "w1")
    set_setting(conn, "chunk_rate_step_mbps", "50")                # a value left by an older server is not read (14/11)
    assert wave(conn, "w2") == "700m"
    monkeypatch.setattr(chunk_rate, "STEP_MBPS", 50)
    assert wave(conn, "w3") == "750m"


def test_a_wave_whose_loss_was_not_measured_holds_the_rate_it_does_not_raise_it(conn):
    wave(conn, "w1")
    assert wave(conn, "w2", lost=None) == "700m"
    assert wave(conn, "w3") == "700m" and basis_of(conn, "w3") == "chunk_hold"        # w2 taught nothing new
    assert wave(conn, "w4") == "800m"


def test_an_engine_that_could_not_keep_its_pace_does_not_raise_the_rate(conn):
    wave(conn, "w1", pace=0.7)                                     # pushed 70% of the asked rate: under `KEEP_UP`
    assert wave(conn, "w2") == "600m" and basis_of(conn, "w2") == "chunk_hold"


def test_a_rate_proven_with_fewer_cloners_is_not_raised_for_more_but_is_cut_for_loss(conn):
    wave(conn, "w1", macs=(MAC_A,))
    three = (MAC_A, MAC_B, MAC_C)
    assert wave(conn, "w2", macs=three, lost=10) == "600m"        # one machine proved 600m: held for three
    assert basis_of(conn, "w2") == "chunk_hold"
    assert wave(conn, "w3", macs=three) == "450m"                 # and the three of them lost chunks: cut, whatever the count
    assert basis_of(conn, "w3") == "chunk_down"


# --- 4. a wave that teaches nothing ---------------------------------------------------------------------------


def test_a_wave_with_a_disconnect_does_not_teach_and_does_not_move_the_rate(conn):
    wave(conn, "w1")
    assert wave(conn, "w2") == "700m"
    wave(conn, "w3", away=1, lost=2000)                            # a cable pulled on purpose: 40% lost, a gap
    assert taught(conn, "w3") is False
    skip = json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = 'w3'").fetchone()["stream"])["chunk"]["skip"]
    assert skip == "chunk_net_gap"
    assert wave(conn, "w4") == "800m"                              # from w2 (clean at 700m), as if w3 had not run
    assert basis_of(conn, "w4") == "chunk_up"
    assert any("teaches nothing: chunk_net_gap" in r["detail"]
               for r in conn.execute("SELECT detail FROM journal WHERE event = 'chunk_rate_recorded'"))


@pytest.mark.parametrize("ended,skip", [("send_failed", "ended=send_failed"), ("send_stopped", "ended=send_stopped")])
def test_a_wave_that_did_not_end_in_send_done_teaches_nothing(conn, ended, skip):
    wave(conn, "w1", ended=ended)
    assert taught(conn, "w1") is False
    assert chunk_rate.last_sample(conn) is None
    assert json.loads(conn.execute("SELECT stream FROM wave_rates").fetchone()["stream"])["chunk"]["skip"] == skip


def test_a_wave_too_short_to_measure_teaches_nothing(conn):
    wave(conn, "w1", chunks=100)                                   # 420MB
    assert taught(conn, "w1") is False and chunk_rate.last_sample(conn) is None


def test_a_wave_the_library_disk_held_back_teaches_nothing(conn, monkeypatch):
    monkeypatch.setattr(chunk_rate.server_io, "bound", lambda c, sid: sid == "w1")
    wave(conn, "w1")
    wave(conn, "w2")
    assert taught(conn, "w1") is False and taught(conn, "w2") is True
    assert chunk_rate.last_sample(conn)["session_id"] == "w2"


def test_a_wave_without_a_first_pass_teaches_nothing(conn):
    rate = drive_rates.decide(conn, "w1", lambda: _drives([MAC_A]), None)
    chunk_rate.record(conn, "w1", {"ended": "send_done"})                # the engine had no wave to describe
    assert taught(conn, "w1") is False and rate == "600m"


# --- 5. the network budget -------------------------------------------------------------------------------------


def test_the_rate_never_goes_above_the_budget_ceiling_minus_what_http_keeps(conn):
    set_setting(conn, budget.SETTING, "600")                       # the operator's ceiling for the distribution network
    assert wave(conn, "w1", ceiling="590m") == "590m"
    assert wave(conn, "w2", ceiling="590m") == "590m"              # 690m wanted: the wave's own ceiling is lower
    assert drive_rates.decision(conn, "w2")["previous"]["capped_by"] == "ceiling"
    rate = wave(conn, "w3")                                        # 690m wanted, the ceiling is 600m minus FLOOR_BPS
    assert rate == "592m" and basis_of(conn, "w3") == "chunk_cap"
    assert drive_rates.decision(conn, "w3")["previous"]["capped_by"] == "budget"
    assert drive_rates.parse_bitrate(rate) <= 600_000_000 - budget.FLOOR_BPS


def test_the_card_sets_the_ceiling_when_the_budget_setting_is_automatic(conn):
    link = lambda: {"interface": "eth1", "speed_mbps": 1000, "why": None}   # noqa: E731 -- 1GbE: 900m, 892m for the multicast
    for i in range(1, 6):
        wave(conn, f"w{i}", link=link)
    assert drive_rates.decision(conn, "w5")["max_bitrate"] == "892m"
    assert basis_of(conn, "w5") == "chunk_cap"
    assert wave(conn, "w6", link=link) == "892m"                   # and it stays there, however clean the waves


def test_the_ceiling_of_the_wave_itself_holds(conn):
    for i in range(1, 5):
        wave(conn, f"w{i}", ceiling="650m")
    assert drive_rates.decision(conn, "w4")["max_bitrate"] == "650m"


def test_a_cut_wave_lands_on_the_floor_not_below_it(conn):
    rates = [drive_rates.parse_bitrate(wave(conn, f"w{i}", lost=2000)) for i in range(1, 9)]   # 40% lost, every wave
    assert rates[:3] == [600_000_000, 450_000_000, 337_000_000]
    assert rates[-1] == 150_000_000 and min(rates) == 150_000_000    # 25% of the start rate: where `choose` stops too


# --- 6. switched off, and the other engine -----------------------------------------------------------------------


def test_with_the_learning_switched_off_nothing_is_recorded_and_the_rate_is_the_fixed_ceiling(conn):
    set_setting(conn, drive_rates.SETTING, "false")
    assert wave(conn, "w1") == "900m" and wave(conn, "w2") == "900m"          # #3036: the ceiling, not the start
    assert conn.execute("SELECT COUNT(*) FROM wave_rates WHERE stream IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'chunk_rate_recorded'").fetchone()[0] == 0


def test_a_udpcast_wave_is_not_changed_by_chunk_waves_that_came_before_it(conn):
    wave(conn, "w1")
    udp = drive_rates.decide(conn, "u1", lambda: _drives([MAC_A, MAC_B]), None)          # no `adjust`: udp-sender
    chosen = drive_rates.decision(conn, "u1")
    assert chosen["basis"] != "chunk_up" and udp == chosen["max_bitrate"]


def test_a_failure_in_the_learning_is_journaled_and_the_wave_goes_out_at_the_rate_of_today(conn, monkeypatch):
    wave(conn, "w1")
    monkeypatch.setattr(chunk_rate, "next_rate", lambda *a: 1 / 0)
    assert wave(conn, "w2") == "600m"
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'chunk_rate_failed'").fetchone()[0] == 1


def test_the_constants_are_the_udpcast_ones():
    assert (chunk_rate.LOSS_PERMILLE, chunk_rate.STEP_MBPS, chunk_rate.CUT_PERCENT) == (
        round(drive_rates.LOSS_DOWN_RATIO * 1000), drive_rates.STEP_UP // 10 ** 6, round(drive_rates.DOWN_FACTOR * 100))
