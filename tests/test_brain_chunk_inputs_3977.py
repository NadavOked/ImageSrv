"""#3977: two inputs the wave rate (K8) of the chunk engine did not get when the old sender was removed (#3173).

1. **Goodput of each cloner.** `chunk_rate.record` keeps what every cloner made durable in the first passes, per drawer and
   second; before a climb `chunk_rate.adjust` compares the rates that were tried, and a rate above the best that was not
   faster for any cloner of the wave stops the climb (`chunk_peak`).
2. **The rate a cloner fell at.** A cloner the server lost in a first pass is remembered with the rate the wave asked (a row
   of the machine in `brain_decisions`); two falls that nothing refuted are a wall, and a wave with that cloner does not
   land on it or above it (`chunk_unsafe`).

Both can only hold or lower the rate, and **a wave that measured nothing changes nothing**: the rate is the one the AIMD
gives without them (the tests named `..._not_measured_...`).

**The switch** (`brain_rate_inputs`, Nadav 21/11: three rounds in shadow, then one enforced). ``shadow`` is the default:
the same verdicts are rows with the rate the wave would have gone out at, and the rate that goes out is the AIMD's.
Sections 1-4 run with the switch on ``enforce`` (the fixture `conn`); section 5 is the switch itself, on a server as it
is installed (the fixture `fresh`), and each of its waves has an enforced twin in sections 2 and 4.

**Negative control:** `server/rounds/chunk_rate.py`, `server/rounds/chunksend.py`, `server/app.py`, `server/brain/say.py`,
`server/console/api.py` and `server/console/journal_he.py` back at the base, and `rateinputs.peak` / `rateinputs.wall_of`
mutated to never decide: the behavioural tests fail on the rate that comes out of the next wave ("800m" where the peak is
"600m", "700m" where the wall gives "600m"), and the shadow tests on the row that is not there.
No test here sleeps: the times are written into the rows.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, rateinputs, veto
from server.rounds import chunk_rate, drive_rates

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: F401 -- rig is a fixture

CHUNK = 4 * 1024 * 1024
MAC_A, MAC_B, MAC_C = "aa:bb:cc:00:00:21", "aa:bb:cc:00:00:22", "aa:bb:cc:00:00:23"
CHUNKS = 5000                          # 21GB: long and big enough to teach (`MIN_SAMPLE_S`, `MIN_SAMPLE_BYTES`)
NOW = datetime.now(timezone.utc).replace(microsecond=0)      # the falls are read in a window that ends now: no fixed date
M = 10 ** 6


@pytest.fixture()
def fresh(tmp_path):
    """A server as it is installed: nobody set the switch of the two inputs (shadow)."""
    return db.connect(tmp_path / "imagesrv.db")


@pytest.fixture()
def conn(fresh):
    """The switch on ``enforce``: the two inputs move the rate (sections 1-4)."""
    db.set_setting(fresh, rateinputs.SETTING, rateinputs.ENFORCE)
    return fresh


def _drives(macs):
    return [{"serial": f"S{i}-{mac}", "model": "EVO 870", "mac": mac, "port": i + 1} for i, mac in enumerate(macs)]


def outcome(asked, macs, *, took=None, away=(), stream=CHUNKS * CHUNK, lost=0, ended="send_done"):
    """What `ChunkEngine.rate_outcome` says of a wave that pushed the rate it asked. `took`: {mac: the share of the rate
    asked that its drawer made durable in the first pass}; None = the engine did not know the sizes (`drives` is None)."""
    seconds = stream * 8 / asked
    drives = None if took is None else {d["mac"]: {d["serial"]: int(stream * took[d["mac"]])}
                                        for d in _drives(macs) if d["mac"] in took}
    return {"chunks": CHUNKS, "bytes": CHUNKS * CHUNK, "first_pass_s": seconds, "lost_chunks": lost, "away": len(away),
            "away_macs": sorted(away), "cloners": len(macs), "repair_rounds": 0, "ended": ended,
            "stream_zbytes": stream, "drives": drives}


def decide(conn, sid, macs=(MAC_A, MAC_B)) -> str:
    """The rate the server chooses for a wave (`decide` -> `adjust`, with the wave's id as `server.app` gives it). The
    base's `adjust` takes no id: there the call is the base's, so that what fails in the negative control is the rate."""
    told = (sid,) if "session_id" in inspect.signature(chunk_rate.adjust).parameters else ()
    return drive_rates.decide(conn, sid, lambda: _drives(macs), None,
                              adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives, *told))


def wave(conn, sid, macs=(MAC_A, MAC_B), **kw) -> str:
    """One whole wave the way the server runs it: the rate is chosen, then the wave ends and says what it saw. Returns
    the rate the wave went out at."""
    rate = decide(conn, sid, macs)
    chunk_rate.record(conn, sid, outcome(drive_rates.parse_bitrate(rate), macs, **kw))
    return rate


def flat(mbps, asked, macs=(MAC_A, MAC_B)):
    """`took` of a wave in which every cloner made `mbps` durable whatever the rate asked."""
    return {mac: mbps * M / drive_rates.parse_bitrate(asked) for mac in macs}


def basis_of(conn, sid) -> str:
    return drive_rates.decision(conn, sid)["basis"]


def sample(conn, sid) -> dict:
    return json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (sid,)).fetchone()["stream"])["chunk"]


def rows(conn, action, sid=None) -> list[dict]:
    got = [d for d in memory.decisions(conn, rule="K8", limit=200) if d["action"] == action]
    return [d for d in got if sid is None or d["round_id"] == sid]


def at(conn, table, sid, when: datetime) -> None:
    """The row of a wave (`wave_rates`), or the falls of a wave (`brain_decisions`), happened at `when`."""
    key = "session_id" if table == "wave_rates" else "round_id"
    conn.execute(f"UPDATE {table} SET at = ? WHERE {key} = ?", (when.isoformat(timespec="seconds"), sid))
    conn.commit()


# --- 1. goodput: what is recorded ---------------------------------------------------------------------------------


def test_a_wave_keeps_what_each_cloner_made_durable_per_drawer_and_second(conn):
    assert wave(conn, "w1", took={MAC_A: 0.9, MAC_B: 0.5}) == "600m"
    got = sample(conn, "w1").get("goodput") or {}
    assert set(got) == {MAC_A, MAC_B}
    assert got[MAC_A] == {"bps": pytest.approx(540 * M, rel=1e-6), "drives": 1}
    assert got[MAC_B] == {"bps": pytest.approx(300 * M, rel=1e-6), "drives": 1}


def test_goodput_is_per_drawer_that_was_measured_and_a_drawer_that_was_not_is_not_a_zero():
    got = rateinputs.goodput_of({"a": {"S1": 1000, "S2": 3000, "S3": None}, "b": {"S4": None}}, 8)
    assert got == {"a": {"bps": 2000, "drives": 2}}                     # (1000 + 3000) x 8 / 8s / 2 drawers; b is absent


@pytest.mark.parametrize("drives, seconds", [(None, 100), ({}, 100), ({"a": {"S1": 1000}}, None), ({"a": {"S1": 1000}}, 0)])
def test_goodput_that_was_not_measured_is_none_not_zero(drives, seconds):
    assert rateinputs.goodput_of(drives, seconds) is None


# --- 2. goodput: the decision --------------------------------------------------------------------------------------


def test_a_rate_that_was_tried_and_was_not_faster_for_any_cloner_stops_the_climb(conn):
    assert wave(conn, "w1", took=flat(300, "600m")) == "600m"
    assert wave(conn, "w2", took=flat(300, "700m")) == "700m"           # one rate tried: nothing to compare yet, the AIMD climbs
    assert rows(conn, "WAVE_GOODPUT", "w2")[0]["verdict"] == "UNDECIDED"
    # 700m was tried, and the cloners made durable what they did at 600m: the same time at a higher rate is not a reason
    assert wave(conn, "w3", took=flat(300, "600m")) == "600m" and basis_of(conn, "w3") == "chunk_peak"
    (row,) = rows(conn, "WAVE_GOODPUT", "w3")
    assert row["verdict"] == "PEAK" and row["mode"] == "enforce" and row["evidence"][0]["rate"] == 600 * M
    assert [p["rate"] for p in row["evidence"][0]["tried"]] == [600 * M, 700 * M]
    assert drive_rates.decision(conn, "w3")["previous"]["goodput"]["verdict"] == "PEAK"
    # and it stays there while the wave at 700m is in the window
    assert wave(conn, "w4", took=flat(300, "600m")) == "600m" and basis_of(conn, "w4") == "chunk_peak"


def test_a_climb_that_one_cloner_wrote_more_at_goes_on(conn):
    assert wave(conn, "w1", took={MAC_A: 0.5, MAC_B: 0.9}) == "600m"       # A 300, B 540
    assert wave(conn, "w2", took={MAC_A: 300 / 700, MAC_B: 0.9}) == "700m"  # A still 300, B 630: more than 3% over 540
    assert wave(conn, "w3", took={MAC_A: 0.3, MAC_B: 0.9}) == "800m" and basis_of(conn, "w3") == "chunk_up"
    assert rows(conn, "WAVE_GOODPUT", "w3")[0]["verdict"] == "CLIMB"


def test_a_rise_inside_the_noise_is_not_faster(conn):
    assert wave(conn, "w1", took=flat(300, "600m")) == "600m"
    assert wave(conn, "w2", took=flat(300 * (1 + rateinputs.GOODPUT_NOISE) - 1, "700m")) == "700m"
    assert wave(conn, "w3", took=flat(300, "600m")) == "600m" and basis_of(conn, "w3") == "chunk_peak"


def test_waves_whose_goodput_was_not_measured_change_nothing(conn):
    """The engine did not know the sizes (`drives` is None): the rate is the AIMD's, exactly, and nothing is a zero."""
    assert [wave(conn, sid) for sid in ("w1", "w2", "w3")] == ["600m", "700m", "800m"]
    assert "goodput" in sample(conn, "w2") and sample(conn, "w2")["goodput"] is None and basis_of(conn, "w3") == "chunk_up"
    (row,) = rows(conn, "WAVE_GOODPUT", "w3")
    assert row["verdict"] == "UNDECIDED" and row["evidence"][0]["why"] == "last_wave_not_measured"


def test_a_cloner_that_was_not_measured_in_the_wave_is_not_a_cloner_that_was_not_faster(conn):
    assert wave(conn, "w1", took=flat(300, "600m")) == "600m"
    assert wave(conn, "w2", took={MAC_A: 300 / 700}) == "700m"           # B's drawer was not measured at 700m
    assert wave(conn, "w3", took=flat(300, "800m")) == "800m" and basis_of(conn, "w3") == "chunk_up"
    assert rows(conn, "WAVE_GOODPUT", "w3")[0]["verdict"] == "UNDECIDED"


def test_a_wave_of_another_stream_is_not_compared(conn):
    assert wave(conn, "w1", took=flat(300, "600m")) == "600m"
    assert wave(conn, "w2", took=flat(300, "700m"), stream=CHUNKS * CHUNK // 2) == "700m"      # another image
    assert wave(conn, "w3", took=flat(300, "800m")) == "800m" and basis_of(conn, "w3") == "chunk_up"


def _taught(rate, mbps, *, sent=1.0, macs=(MAC_A,), stream=10 ** 10, sid="w"):
    return {"session_id": sid, "asked_bps": rate * M, "sent_bps": int(rate * M * sent), "stream_zbytes": stream,
            "goodput": {mac: {"bps": mbps * M, "drives": 1} for mac in macs}}


def test_a_wave_that_did_not_run_at_the_rate_it_asked_is_not_a_point():
    last, slow = _taught(600, 300), _taught(700, 300, sent=0.5)          # 700m asked, 350Mb/s pushed: it says nothing of 700m
    assert rateinputs.peak([last, slow], {MAC_A: 1})["why"] == "one_rate_tried"
    assert rateinputs.peak([last, _taught(700, 300)], {MAC_A: 1})["verdict"] == "PEAK"


def test_a_point_leaves_the_window_and_the_rate_above_is_tried_again():
    waves = [_taught(600, 300) for _ in range(rateinputs.GOODPUT_WINDOW)]
    assert rateinputs.peak([*waves, _taught(700, 300)], {MAC_A: 1})["verdict"] == "UNDECIDED"
    assert rateinputs.peak([*waves[1:], _taught(700, 300)], {MAC_A: 1})["verdict"] == "PEAK"


def test_another_number_of_drawers_is_not_this_wave():
    assert rateinputs.peak([_taught(600, 300), _taught(700, 300)], {MAC_A: 2})["why"] == "last_wave_not_measured"


# --- 3. the rate a cloner fell at: what is remembered ---------------------------------------------------------------


def test_the_engine_says_which_machine_it_lost_in_the_first_pass(rig):   # noqa: F811
    engine = rig.make()
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.members = ["a", "b"]                                  # c is lost: the server no longer counts it alive
    assert wait_for(lambda: engine._wave is not None and engine._wave.away), "the engine never noticed c was gone"
    rig.members = ["a", "b", "c"]
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": [[0, 2]]}, skipped={"C1": 0})["action"] == "fetch"
    assert engine.rate_outcome(SID).get("away_macs") == ["c"] and engine.rate_outcome(SID)["away"] == 1


def test_a_cloner_lost_in_a_first_pass_is_remembered_with_the_rate_the_wave_asked(conn):
    assert wave(conn, "w1", away=[MAC_B]) == "600m"
    assert sample(conn, "w1").get("away_macs") == [MAC_B] and sample(conn, "w1")["skip"] == "chunk_net_gap"
    (row,) = rows(conn, "CLONER_DROP")
    assert (row["entity_kind"], row["entity_key"], row["verdict"], row["round_id"]) == ("machine", MAC_B, "DROPPED", "w1")
    assert row["evidence"] == [{"asked_bps": 600 * M}]
    assert rateinputs.drops_of(conn, MAC_B, datetime.now(timezone.utc).timestamp()) == [(600 * M, pytest.approx(
        datetime.fromisoformat(row["at"]).timestamp()))]
    assert rateinputs.drops_of(conn, MAC_A, datetime.now(timezone.utc).timestamp()) == []


def test_a_fall_older_than_the_window_is_not_read(conn):
    wave(conn, "w1", away=[MAC_B])
    at(conn, "brain_decisions", "w1", NOW - timedelta(days=rateinputs.UNSAFE_AGE_DAYS + 1))
    assert rateinputs.drops_of(conn, MAC_B, NOW.timestamp()) == []


# --- 4. the rate a cloner fell at: the decision ------------------------------------------------------------------------


def test_one_fall_is_not_a_wall(conn):
    assert wave(conn, "w1") == "600m"
    assert wave(conn, "w2", away=[MAC_B]) == "700m"
    assert wave(conn, "w3") == "700m" and basis_of(conn, "w3") == "chunk_up"
    (row,) = rows(conn, "WAVE_UNSAFE_RATE", "w3")
    assert row["verdict"] == "NO_WALL" and row["evidence"][0]["why"] == "few_drops"


def test_two_falls_are_a_wall_and_a_wave_with_that_cloner_does_not_land_on_it(conn):
    assert wave(conn, "w1") == "600m"
    assert wave(conn, "w2", away=[MAC_B]) == "700m"
    assert wave(conn, "w3", away=[MAC_B]) == "700m"                       # the wave before taught nothing: 700m again
    assert wave(conn, "w4") == "600m" and basis_of(conn, "w4") == "chunk_unsafe"
    (row,) = rows(conn, "WAVE_UNSAFE_RATE", "w4")
    assert row["verdict"] == "WALL" and row["mode"] == "enforce"
    assert (row["evidence"][0]["rate"], row["evidence"][0]["mac"]) == (700 * M, MAC_B)
    assert drive_rates.decision(conn, "w4")["previous"]["unsafe"]["cloners"][MAC_B]["drops"] == [700 * M, 700 * M]


def test_the_wall_is_the_cloners_not_the_waves(conn):
    """The cloner that fell is not left out of anything, and a wave without it climbs as before."""
    wave(conn, "w1")
    wave(conn, "w2", away=[MAC_B])
    wave(conn, "w3", away=[MAC_B])
    assert wave(conn, "w4", macs=(MAC_A,)) == "700m" and basis_of(conn, "w4") == "chunk_up"
    assert rows(conn, "WAVE_UNSAFE_RATE", "w4") == []                     # no fall of its cloners in memory: no row


def test_falls_with_no_wave_that_ever_taught_still_hold_the_rate_of_today(conn):
    """A cloner that falls in every wave leaves no wave that teaches: the wall does not need one."""
    assert wave(conn, "w1", away=[MAC_B]) == "600m"
    assert wave(conn, "w2", away=[MAC_B]) == "600m"
    assert wave(conn, "w3") == "500m" and basis_of(conn, "w3") == "chunk_unsafe"
    assert drive_rates.decision(conn, "w3")["previous"]["unsafe"]["rate"] == 600 * M


def test_a_memory_that_was_not_read_changes_nothing(conn, monkeypatch):
    wave(conn, "w1")
    wave(conn, "w2", away=[MAC_B])
    wave(conn, "w3", away=[MAC_B])

    def broken(*_a, **_kw):
        raise RuntimeError("the memory is not readable")
    monkeypatch.setattr(memory, "decisions", broken)
    rate = decide(conn, "w4")
    monkeypatch.undo()
    assert rate == "700m" and basis_of(conn, "w4") == "chunk_up"          # the AIMD's rate: not read is not "a wall"
    (row,) = rows(conn, "WAVE_UNSAFE_RATE", "w4")
    assert row["verdict"] == "UNDECIDED" and row["evidence"][0]["unread"] == [MAC_A, MAC_B]


def test_the_wall_is_the_second_lowest_rate_of_the_falls():
    assert rateinputs.wall_of([(400, 1.0), (600, 2.0)], [], reprobe=3) == (600, "wall")      # one of the two may be a cable
    assert rateinputs.wall_of([(700, 3.0), (400, 1.0), (600, 2.0)], [], reprobe=3) == (600, "wall")
    assert rateinputs.wall_of([(600, 2.0)], [], reprobe=3) == (None, "few_drops")
    assert rateinputs.wall_of([], [(600, 2.0)], reprobe=3) == (None, "no_drop")


def test_a_clean_wave_at_the_rate_or_above_after_a_fall_refutes_it():
    drops = [(700, 1.0), (700, 2.0)]
    assert rateinputs.wall_of(drops, [(800, 3.0)], reprobe=3) == (None, "refuted")
    assert rateinputs.wall_of(drops, [(700, 1.5)], reprobe=3) == (None, "few_drops")          # only the first is refuted
    assert rateinputs.wall_of(drops, [(600, 3.0)], reprobe=3) == (700, "wall")                # a lower rate proves nothing
    assert rateinputs.wall_of(drops, [(800, 0.5)], reprobe=3) == (700, "wall")                # before the falls


def test_the_wall_is_spent_after_enough_clean_waves_newer_than_the_newest_fall():
    drops = [(700, 1.0), (700, 2.0)]
    assert rateinputs.wall_of(drops, [(600, 3.0), (600, 4.0)], reprobe=3) == (700, "wall")
    assert rateinputs.wall_of(drops, [(600, 3.0), (600, 4.0), (600, 5.0)], reprobe=3) == (None, "spent")


def test_the_wall_stands_until_the_clean_waves_after_it_spend_it(conn):
    wave(conn, "w1", took=flat(300, "600m"))
    wave(conn, "w2", away=[MAC_B])
    wave(conn, "w3", away=[MAC_B])
    for sid, days in (("w1", 10), ("w2", 9), ("w3", 8)):
        at(conn, "wave_rates", sid, NOW - timedelta(days=days))
        at(conn, "brain_decisions", sid, NOW - timedelta(days=days))
    # three clean waves under the wall, newer than the newest fall (`chunk_rate.REPROBE_WAVES`)...
    for n, sid in enumerate(("w4", "w5", "w6")):
        assert wave(conn, sid, took={MAC_A: 0.9, MAC_B: 0.9}) == "600m" and basis_of(conn, sid) == "chunk_unsafe"
        at(conn, "wave_rates", sid, NOW - timedelta(days=7 - n))
    assert chunk_rate.REPROBE_WAVES == 3
    # ...and the loop climbs again
    assert wave(conn, "w7", took={MAC_A: 0.9, MAC_B: 0.9}) == "700m" and basis_of(conn, "w7") == "chunk_up"
    assert rows(conn, "WAVE_UNSAFE_RATE", "w7")[0]["evidence"][0]["why"] == "spent"


def test_a_stale_start_replaced_by_the_write_check_does_not_land_on_the_wall(conn):
    wave(conn, "w1")
    wave(conn, "w2", away=[MAC_B])
    wave(conn, "w3", away=[MAC_B])
    drives = _drives((MAC_A, MAC_B))
    assert decide(conn, "w4") == "600m"
    probes = {(d["mac"], d["serial"]): 200 for d in drives}             # 200MB/s: 1600m, far over the wall
    later = datetime.now(timezone.utc) + timedelta(hours=chunk_rate.STALE_HOURS + 1)
    assert chunk_rate.start(conn, "w4", 600 * M, probes, now=later.timestamp()) == 600 * M
    check = drive_rates.decision(conn, "w4")["start_check"]
    assert "stale" in check["why"] and "unsafe_wall" in check["why"]
    assert check["unsafe_wall"] == {"mode": "enforce", "would": "600m"}


# --- 5. the switch: off / shadow / enforce (Nadav 21/11) ---------------------------------------------------------------


def test_the_switch_is_shadow_until_someone_sets_it_and_its_words_are_the_vetos(fresh):
    assert (rateinputs.OFF, rateinputs.SHADOW, rateinputs.ENFORCE) == veto.MODES
    assert rateinputs.mode(fresh) == "shadow" == rateinputs.DEFAULT
    db.set_setting(fresh, rateinputs.SETTING, "on")                       # not one of the three words: not "enforce"
    assert rateinputs.mode(fresh) == "shadow"
    db.set_setting(fresh, rateinputs.SETTING, "enforce")
    assert rateinputs.mode(fresh) == "enforce"
    db.set_setting(fresh, veto.SETTING, "off")                            # the brain's own switch turns every rule off
    assert rateinputs.mode(fresh) == "off" and rateinputs.setting(fresh) == "enforce"


def test_in_shadow_a_peak_is_a_row_with_the_rate_it_would_have_given_and_the_wave_goes_out_at_the_aimds(fresh):
    """The waves of `test_a_rate_that_was_tried_and_was_not_faster_for_any_cloner_stops_the_climb`: enforced, w3 goes out
    at 600m."""
    assert wave(fresh, "w1", took=flat(300, "600m")) == "600m"
    assert wave(fresh, "w2", took=flat(300, "700m")) == "700m"
    assert wave(fresh, "w3", took=flat(300, "800m")) == "800m" and basis_of(fresh, "w3") == "chunk_up"
    (row,) = rows(fresh, "WAVE_GOODPUT", "w3")
    assert (row["mode"], row["verdict"], row["outcome"]) == ("shadow", "PEAK", "shadow")
    said = row["evidence"][0]
    assert (said["mode"], said["rate"], said["aimd_bps"], said["would_bps"]) == ("shadow", 600 * M, 800 * M, 600 * M)
    assert drive_rates.decision(fresh, "w3")["previous"]["goodput"]["would_bps"] == 600 * M


def test_in_shadow_a_wall_is_a_row_with_the_rate_it_would_have_given_and_the_rate_does_not_move(fresh):
    """The waves of `test_two_falls_are_a_wall_and_a_wave_with_that_cloner_does_not_land_on_it`: enforced, w4 goes out at
    600m. The falls are remembered in shadow too -- without them there is no wall to say anything about."""
    assert wave(fresh, "w1") == "600m"
    assert wave(fresh, "w2", away=[MAC_B]) == "700m"
    assert wave(fresh, "w3", away=[MAC_B]) == "700m"
    assert [(r["mode"], r["entity_key"], r["round_id"]) for r in rows(fresh, "CLONER_DROP")] == [
        ("shadow", MAC_B, "w3"), ("shadow", MAC_B, "w2")]
    assert wave(fresh, "w4") == "700m" and basis_of(fresh, "w4") == "chunk_up"
    (row,) = rows(fresh, "WAVE_UNSAFE_RATE", "w4")
    assert (row["mode"], row["verdict"], row["outcome"]) == ("shadow", "WALL", "shadow")
    said = row["evidence"][0]
    assert (said["mode"], said["rate"], said["mac"]) == ("shadow", 700 * M, MAC_B)
    assert (said["aimd_bps"], said["would_bps"]) == (700 * M, 600 * M)
    assert drive_rates.decision(fresh, "w4")["previous"]["unsafe"]["would_bps"] == 600 * M


def test_the_falls_of_the_shadow_waves_are_the_wall_of_the_first_wave_that_enforces(fresh):
    """Three rounds in shadow, then one enforced: what the shadow rounds saw is what the enforced one stands on."""
    wave(fresh, "w1")
    wave(fresh, "w2", away=[MAC_B])
    wave(fresh, "w3", away=[MAC_B])
    assert wave(fresh, "w4") == "700m"                                    # shadow: the wall is only a row
    db.set_setting(fresh, rateinputs.SETTING, "enforce")
    assert wave(fresh, "w5") == "600m" and basis_of(fresh, "w5") == "chunk_unsafe"
    (row,) = rows(fresh, "WAVE_UNSAFE_RATE", "w5")
    assert (row["mode"], row["verdict"], row["outcome"]) == ("enforce", "WALL", "applied")
    assert (row["evidence"][0]["aimd_bps"], row["evidence"][0]["would_bps"]) == (800 * M, 600 * M)


@pytest.mark.parametrize("switch", [rateinputs.SETTING, veto.SETTING])
def test_off_decides_nothing_remembers_nothing_and_the_rate_is_the_aimds(fresh, switch):
    db.set_setting(fresh, switch, "off")
    assert wave(fresh, "w1", took=flat(300, "600m")) == "600m"
    assert wave(fresh, "w2", away=[MAC_B]) == "700m"
    assert wave(fresh, "w3", away=[MAC_B]) == "700m"
    assert wave(fresh, "w4") == "700m" and basis_of(fresh, "w4") == "chunk_up"
    assert sample(fresh, "w2").get("away_macs") == [MAC_B]                # what the wave saw is still its sample
    assert [rows(fresh, action) for action in ("CLONER_DROP", "WAVE_UNSAFE_RATE", "WAVE_GOODPUT")] == [[], [], []]
    previous = drive_rates.decision(fresh, "w4")["previous"]
    assert "unsafe" not in previous and "goodput" not in previous


def test_in_shadow_a_start_replaced_by_the_write_check_is_not_held_and_says_what_the_wall_would_have_done(fresh):
    """The waves of `test_a_stale_start_replaced_by_the_write_check_does_not_land_on_the_wall`: enforced, 600m."""
    wave(fresh, "w1")
    wave(fresh, "w2", away=[MAC_B])
    wave(fresh, "w3", away=[MAC_B])
    drives = _drives((MAC_A, MAC_B))
    assert decide(fresh, "w4") == "700m"
    probes = {(d["mac"], d["serial"]): 200 for d in drives}             # 200MB/s: 1600m, far over the wall
    later = datetime.now(timezone.utc) + timedelta(hours=chunk_rate.STALE_HOURS + 1)
    assert chunk_rate.start(fresh, "w4", 700 * M, probes, now=later.timestamp()) >= 700 * M      # on the wall or above it
    check = drive_rates.decision(fresh, "w4")["start_check"]
    assert "stale" in check["why"] and "unsafe_wall" not in check["why"]
    assert check["unsafe_wall"] == {"mode": "shadow", "would": "600m"}


def test_the_switch_is_a_setting_of_the_console_api_for_an_admin_only(server):
    admin = server["admin"]
    assert admin.get("/api/console/settings").json()[rateinputs.SETTING] == "shadow"
    assert server["deploy"].post("/api/console/settings", json={rateinputs.SETTING: "enforce"}).status_code == 403
    assert rateinputs.mode(server["ctx"].conn) == "shadow"
    assert admin.post("/api/console/settings", json={rateinputs.SETTING: "enforce"}).status_code == 200
    assert rateinputs.mode(server["ctx"].conn) == "enforce"
    assert admin.get("/api/console/settings").json()[rateinputs.SETTING] == "enforce"
    texts = [r["text"] for r in admin.get("/api/console/journal", params={"event": "setting_change"}).json()]
    assert f"{rateinputs.LABEL_HE}: פעיל (משנה את הקצב)" in texts, texts
