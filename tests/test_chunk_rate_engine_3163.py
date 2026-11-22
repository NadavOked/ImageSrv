"""#3163: what the chunk engine tells the network-rate learning at the end of a wave (`ChunkEngine.rate_outcome`),
and the wiring of `server.app` (`chunk_rate.record` at the end of a wave, `chunk_rate.adjust` before the next).

The engine runs with a fake `chunkcast` (as `test_chunksend_2533`): the first pass, what each machine says
it has, the repair rounds. Nothing here sleeps: every wait is on a condition (`wait_for`), and the clock is a number
the fake `chunkcast` advances when its pass "ends".
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.db import now_iso
from server.rounds import chunksend

from test_chunk_nic_gate_2533 import _sending
from test_chunksend_2533 import ALL, SID, Runner, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import CLONER1, room_server  # noqa: F401 -- shared fixture


class TimedRunner(Runner):
    """A fake `chunkcast` whose pass takes `costs[n]` seconds of the engine's clock."""

    def __init__(self, clock: list, costs: list):
        super().__init__()
        self.clock, self.costs = clock, costs

    def __call__(self, cmd):
        process = super().__call__(cmd)
        cost = self.costs[len(self.commands) - 1]
        wait = process.wait

        def timed(timeout=None):
            self.clock[0] += cost
            return wait(timeout)
        process.wait = timed
        return process


def _listening(rig):
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)


def _done(engine):
    assert wait_for(lambda: engine.status()["state"] == "done"), engine.status()


# --- the engine's outcome ---------------------------------------------------------------------------------------


def test_a_clean_wave_says_nothing_was_lost_how_much_it_sent_and_to_how_many(rig):
    engine = rig.make()
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": ALL}, skipped={"C1": 0})["action"] == "done"
    _done(engine)
    got = engine.rate_outcome(SID)
    assert got["chunks"] == 10 and got["bytes"] == 10 * chunksend.CHUNK_BYTES
    assert got["lost_chunks"] == 0 and got["away"] == 0 and got["cloners"] == 3 and got["repair_rounds"] == 0
    assert isinstance(got["first_pass_s"], float) and got["first_pass_s"] >= 0


def test_chunks_that_did_not_arrive_are_counted_for_the_worst_machine_not_summed_over_machines(rig):
    engine = rig.make()
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": [[0, 4], [7, 9]]}, skipped={"A1": 0})            # 2 lost
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    c = rig.ask("c", 1, {"C1": [[0, 1]]}, skipped={"C1": 0})                          # 8 lost
    assert c["action"] in ("receive", "fetch"), c
    got = engine.rate_outcome(SID)
    assert got["lost_chunks"] == 8 and got["away"] == 0 and got["repair_rounds"] == 1


def test_a_machine_that_reported_no_skipped_makes_the_loss_unmeasured_not_zero(rig):
    engine = rig.make()
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": ALL})                                            # an older agent: no `skipped`
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": ALL}, skipped={"C1": 0})["action"] == "done"
    _done(engine)
    assert engine.rate_outcome(SID)["lost_chunks"] is None


def test_the_server_losing_a_machine_during_the_first_pass_is_counted_as_away(rig):
    engine = rig.make()
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.members = ["a", "b"]                                  # c is lost: the server no longer counts it alive
    assert wait_for(lambda: engine._wave is not None and engine._wave.away), "the engine never noticed c was gone"
    rig.members = ["a", "b", "c"]
    rig.tell("b", 1, "ended", {"B1": ALL}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": [[0, 2]]}, skipped={"C1": 0})["action"] == "fetch"
    assert engine.rate_outcome(SID)["away"] == 1
    assert wait_for(lambda: rig.named("chunk_net_gap")), "the engine never journaled the gap"


def test_only_the_first_pass_is_timed_the_repair_rounds_are_not(rig):
    clock = [100.0]
    rig.runner = TimedRunner(clock, costs=[5.0, 7.0])         # the first pass takes 5s of the clock, the repair 7s
    engine = rig.make(clock=lambda: clock[0])
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]}, skipped={"C1": 0})["action"] == "receive"
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)                             # the repair pass went out
    rig.tell("b", 2, "ended", {"B1": ALL})
    assert rig.ask("c", 2, {"C1": [[0, 8]]})["action"] == "fetch"
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    got = engine.rate_outcome(SID)
    assert got["first_pass_s"] == 5.0 and got["repair_rounds"] == 2
    assert got["lost_chunks"] == 3                              # c missed 5, 6 and 9 of the first pass; b only 5 and 6


def test_a_wave_the_engine_does_not_hold_has_no_outcome(rig):
    engine = rig.make()
    assert engine.rate_outcome("another-wave") == {}


# --- the wiring in server.app ----------------------------------------------------------------------------------------


def _taught_by_an_earlier_wave(ctx, asked_bps, at=None):
    """A wave that taught (`chunk_rate.record` wrote this) in `wave_rates`: 8 cloners, clean, kept its pace.
    #3269: and it is this wave's -- the same cloner and drawers (`_room_wave`: CLONER1, S1 and S2), now (`at`, unless
    given), and its write check (`model_speeds`) the one the drawers report now (150MB/s, `_sending`)."""
    sample = {"asked_bps": asked_bps, "sent_bps": asked_bps, "achieved_bps": asked_bps, "loss_permille": 0.0,
              "lost_chunks": 0, "chunks": 5000, "first_pass_s": 300.0, "bytes": 5000 * chunksend.CHUNK_BYTES,
              "cloners": 8, "repair_rounds": 0, "ended": "send_done", "skip": None, "teaches": True}
    roster = [{"mac": CLONER1, "serial": s, "model": "Drawer SSD"} for s in ("S1", "S2")]
    ctx.conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?, ?, ?, ?)",
                     ("ses_earlier", json.dumps({"max_bitrate": f"{asked_bps // 10 ** 6}m", "roster": roster}),
                      at or now_iso(), json.dumps({"chunk": sample})))
    ctx.conn.execute("INSERT INTO model_speeds (session_id, serial, model, mac, mbps, at) VALUES (?,?,?,?,?,?)",
                     ("ses_earlier", "S1", "Drawer SSD", CLONER1, 150, at or now_iso()))
    ctx.conn.commit()


def test_the_server_sends_a_chunk_wave_at_the_rate_the_last_wave_taught_and_records_how_this_one_ended(room_server):
    ctx = room_server["ctx"]
    _taught_by_an_earlier_wave(ctx, 700_000_000)
    runner = Runner(block=True)
    ctx, wave, _process = _sending(room_server, runner)
    try:
        cmd = runner.commands[0]
        assert cmd[cmd.index("--kbps") + 1] == "800000"                               # 700m clean: one step up
        chosen = json.loads(ctx.conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?",
                                             (wave,)).fetchone()["decision"])
        assert chosen["max_bitrate"] == "800m" and chosen["basis"] == "chunk_up"
        assert chosen["previous"]["session_id"] == "ses_earlier"
        assert chosen["start_check"]["why"] == ["fresh_matching"]          # #3269: asked, and kept
        ctx.chunk.on_event("send_done", wave, wave)              # the engine's own event, as `app.on_chunk_event` gets it
        got = json.loads(ctx.conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?",
                                          (wave,)).fetchone()["stream"])["chunk"]
        assert got["asked_bps"] == 800_000_000 and got["ended"] == "send_done"
        assert got["teaches"] is False and got["skip"] == "no_first_pass"             # no pass was run: nothing to learn
        assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'chunk_rate_recorded' AND round_id = ?",
                                (wave,)).fetchone()[0] == 1
    finally:
        ctx.chunk.stop()


def test_a_stale_learned_network_rate_is_not_lifted_by_the_write_check_of_the_wave(room_server):
    """#3269, the wiring of `chunk.wave_start` in `server.app`: the same earlier wave, two days old. Metal 21/11: the write
    check (150MB/s = 1200m) is a ceiling of the drives, not a network rate -- a stale start the AIMD learned from a wave
    whose loss was measured is checked (`stale`) and kept at 800m, not taken to the ceiling."""
    ctx = room_server["ctx"]
    _taught_by_an_earlier_wave(ctx, 700_000_000, at="2026-01-01T10:00:00+00:00")
    runner = Runner(block=True)
    ctx, wave, _process = _sending(room_server, runner)
    try:
        cmd = runner.commands[0]
        chosen = json.loads(ctx.conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?",
                                             (wave,)).fetchone()["decision"])
        assert chosen["start_check"]["why"] == ["stale", "net_learned"]
        assert chosen["start_check"]["was"] == "800m" and chosen["start_check"]["probe_mbps"] == 150
        assert chosen["basis"] == "chunk_up" and chosen["max_bitrate"] == "800m"
        assert cmd[cmd.index("--kbps") + 1] == "800000"
    finally:
        ctx.chunk.stop()


def test_a_chunk_wave_with_no_history_goes_out_at_the_rate_of_today(room_server):
    runner = Runner(block=True)
    ctx, wave, _process = _sending(room_server, runner)
    try:
        cmd = runner.commands[0]
        assert cmd[cmd.index("--kbps") + 1] == "600000"
        assert json.loads(ctx.conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?",
                                           (wave,)).fetchone()["decision"])["basis"] == "start"
    finally:
        ctx.chunk.stop()
