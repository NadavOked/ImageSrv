"""#3173: what udpcast's counters showed, and the chunk engine measures too -- filled from the chunk engine, never invented.

1. The room page: "קצב זרם" (`round.throughput_bps`) and the card "מצב הרשת" (`round.network`, #3978 option A, in place
   of the tile of the old sender's `loss_blocks`) -- live from the wave the engine holds (`ChunkEngine.stream_view`, and
   `status()` for `/overview.sender`), else from the last wave of the round that `chunk_rate.record` kept
   (`room_stream.recorded`); and the column "הרשת" of the history (`/room/history[].network`).
2. The round history (`memory_view`): "איבוד" of a chunk wave (`lost_chunks`) and what it sent (`stream_zbytes`).
3. The facts the brain keeps of a wave and of a round (`memory._wave`, `_ROUND_SELECT`).
4. B58's net-loss row names only what is measured: no `rcvbuf`/`timeouts` keys that are always empty.
5. The catalog and its READMEs: `test_room_tiles_catalog_3173.py` (the catalog is private).

No measurement is None, "לא נמדד" on the screen (principle 5), and a 0 the engine counted stays 0.
The negative control (`bakara-shlilit`): every production file back at the base, `room_stream.py` (new) mutated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.brain import memory, memory_view
from server.rounds import chunk_rate, room_stream

from test_chunksend_2533 import ALL, SID, Runner, rig, wait_for   # noqa: F401 -- rig is a fixture

ROOT = Path(__file__).resolve().parent.parent
T0 = "2026-11-21T08:00:00+00:00"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "tiles.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _wave(conn, sid, created_at, chunk=None, stream=None):
    """A wave of the room (a multicast session of the cloners) and what `wave_rates` kept of it."""
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state, opened_by,"
                 " created_at, last_join_at, kind) VALUES (?, 'grp_CLONERS', 'img', '', 1, 60, 'closed', 'x', ?, 0, 'multicast')",
                 (sid, created_at))
    if chunk is not None or stream is not None:
        conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?, ?, ?, ?)",
                     (sid, json.dumps({"max_bitrate": "600m"}), created_at,
                      json.dumps(stream if stream is not None else {"chunk": chunk})))
    conn.commit()


def _round(current, created_at=T0, closed_at=None):
    return {"wave_session_id": current, "created_at": created_at, "closed_at": closed_at}


class Engine:
    """What `ChunkEngine.stream_view` answers: the wave it holds, or None for any other."""

    def __init__(self, session_id, view):
        self.session_id, self.view = session_id, view

    def stream_view(self, session_id):
        return self.view if session_id == self.session_id else None


# --- 1. the room page: the rate and the network card -------------------------------------------------------------

FIRST = {"first_pass": True, "chunks": 412_000, "lost_chunks": 1_236, "repair_rounds": 1, "multicast_chunks": 1_180,
         "fetched_chunks": 56, "first_pass_s": 372.0, "stream_zbytes": 41_200_000_000, "away": 0,
         "paced_ms": {"wire": 300, "disk": 4_100}, "write_kbps": 896_000}


def test_the_live_wave_of_the_engine_gives_the_rate_and_the_card(conn):
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00")
    got = room_stream.view(conn, Engine("ses_1", {"throughput_bps": 75_000_000, **FIRST}), _round("ses_1"))
    assert got["throughput_bps"] == 75_000_000
    n = got["network"]
    assert n["measuring"] is False and n["chunks"] == 412_000 and n["lost_chunks"] == 1_236
    assert n["loss_permille"] == pytest.approx(3.0) and n["state"] == "loss", "3 permille is above the 1 the rate cuts at"
    assert (n["repair_rounds"], n["multicast_chunks"], n["fetched_chunks"]) == (1, 1_180, 56)
    assert (n["first_pass_s"], n["stream_zbytes"], n["away"], n["write_kbps"]) == (372.0, 41_200_000_000, 0, 896_000)
    assert n["paced_ms"] == {"wire": 300, "disk": 4_100} and n["repair_warn"] is False


def test_before_a_first_pass_ended_the_card_says_measuring_and_the_rate_is_the_last_kept_wave(conn):
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00",
          chunk={"sent_bps": 800_000_000, "chunks": 100, "lost_chunks": 0, "repair_rounds": 0})
    _wave(conn, "ses_2", "2026-11-21T08:20:00+00:00")
    live = {"throughput_bps": None, "first_pass": False, "repair_rounds": 0, "multicast_chunks": 0, "fetched_chunks": 0,
            **{k: None for k in ("chunks", "lost_chunks", "first_pass_s", "stream_zbytes", "away", "paced_ms", "write_kbps")}}
    got = room_stream.view(conn, Engine("ses_2", live), _round("ses_2"))
    assert got["throughput_bps"] == 100_000_000, "bits to bytes, from the last wave the engine kept"
    assert got["network"]["measuring"] is True and got["network"]["state"] is None
    assert all(got["network"][k] is None for k in room_stream.NUMBERS), "the previous wave's numbers are not this wave's"


def test_without_the_engine_the_newest_kept_chunk_wave_of_this_round_answers(conn):
    _wave(conn, "ses_before", "2026-11-21T07:00:00+00:00", chunk={"sent_bps": 8_000, "chunks": 10, "lost_chunks": 9})
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00", chunk={"sent_bps": 400_000_000, "chunks": 1000, "lost_chunks": 300})
    _wave(conn, "ses_2", "2026-11-21T08:10:00+00:00", chunk={"sent_bps": 480_000_000, "chunks": 1000, "lost_chunks": 1,
                                                              "repair_rounds": 3, "multicast_chunks": 1, "fetched_chunks": 0})
    _wave(conn, "ses_3", "2026-11-21T08:30:00+00:00", stream={"sent_bytes": 10, "loss_blocks": 2})  # the old sender's row
    _wave(conn, "ses_4", "2026-11-21T08:40:00+00:00")                                                # open, nothing kept
    for engine in (None, object(), Engine("ses_other", {"throughput_bps": 1, **FIRST})):
        got = room_stream.view(conn, engine, _round("ses_4"))
        assert got["throughput_bps"] == 60_000_000
        assert got["network"]["state"] == "ok" and got["network"]["loss_permille"] == 1.0, "1 permille is still ok"
        assert got["network"]["repair_warn"] is True, "3 repair rounds are more than 2"


def test_the_verdict_is_the_servers_by_the_catalog_thresholds():
    assert room_stream.NET_OK_PERMILLE == chunk_rate.LOSS_PERMILLE
    assert [room_stream.verdict(x) for x in (None, 0, 1, 1.01, 100, 100.5, 240)] == \
        [None, "ok", "ok", "loss", "loss", "bad", "bad"]


def test_nothing_measured_is_none_never_zero(conn):
    assert room_stream.view(conn, None, _round(None)) == {"throughput_bps": None, "network": None}
    _wave(conn, "ses_old", "2026-11-21T08:05:00+00:00", stream={"sent_bytes": 10, "loss_blocks": 0})
    assert room_stream.view(conn, None, _round("ses_old")) == {"throughput_bps": None, "network": None}, \
        "a row of the removed sender is not a chunk wave"
    _wave(conn, "ses_c", "2026-11-21T08:06:00+00:00", chunk={"sent_bps": None, "chunks": 50, "lost_chunks": None})
    n = room_stream.view(conn, None, _round("ses_c"))["network"]
    assert n["state"] is None and n["loss_permille"] is None and n["repair_rounds"] is None and n["away"] is None, n
    assert n["chunks"] == 50 and n["measuring"] is False


def test_a_closed_round_reads_only_its_own_waves(conn):
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00", chunk={"sent_bps": 800, "chunks": 10, "lost_chunks": 0})
    _wave(conn, "ses_next", "2026-11-21T09:30:00+00:00", chunk={"sent_bps": 1600, "chunks": 10, "lost_chunks": 5})
    got = room_stream.view(conn, None, _round("ses_1", closed_at="2026-11-21T09:00:00+00:00"))
    assert got["throughput_bps"] == 100 and got["network"]["state"] == "ok"


def test_the_sample_of_a_wave_keeps_what_the_repairs_sent_and_who_was_lost():
    got = chunk_rate.sample_of(None, {"multicast_chunks": 7, "fetched_chunks": 0, "away": 1})
    assert (got["multicast_chunks"], got["fetched_chunks"], got["away"]) == (7, 0, 1)
    got = chunk_rate.sample_of(None, {"multicast_chunks": -1})
    assert (got["multicast_chunks"], got["fetched_chunks"], got["away"]) == (None, None, None)


def test_the_room_page_round_and_the_history_carry_the_numbers_of_the_engine(tmp_path):
    """The wiring: `/room.round` (`room.status_view`) and `/room/history` have the last kept wave's numbers."""
    pytest.importorskip("fastapi")
    from conftest import MANIFEST_256, write_image          # noqa: PLC0415
    from server.app import create_app                      # noqa: PLC0415
    from server.rounds import room, room_console           # noqa: PLC0415

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:7712")
    ctx = app.state.ctx
    try:
        ctx.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at, closed_at,"
                         " wave_session_id, failed_reason) VALUES ('rr_t', 'img', 2, 'failed', 'noc', ?, ?, 'ses_2', 'x')",
                         (T0, "2026-11-21T09:00:00+00:00"))
        _wave(ctx.conn, "ses_1", "2026-11-21T08:01:00+00:00",
              chunk={"sent_bps": 960_000_000, "chunks": 1000, "lost_chunks": 240, "repair_rounds": 4})
        _wave(ctx.conn, "ses_2", "2026-11-21T08:20:00+00:00")
        got = room.status_view(ctx)["round"]
        assert got["throughput_bps"] == 120_000_000 and got["network"]["state"] == "bad", got
        assert "loss_blocks" not in got and "resent_chunks" not in got
        # the history lists the rounds that ended and are not the one `/room` shows (`round_history`)
        ctx.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at, closed_at)"
                         " VALUES ('rr_old', 'img', 2, 'closed', 'noc', ?, ?)",
                         ("2026-11-21T06:00:00+00:00", "2026-11-21T07:00:00+00:00"))
        _wave(ctx.conn, "ses_old", "2026-11-21T06:10:00+00:00",
              chunk={"sent_bps": 8, "chunks": 1000, "lost_chunks": 0, "repair_rounds": 1})
        [row] = [h for h in room_console.round_history(ctx, 10) if h["id"] == "rr_old"]
        assert row["network"]["state"] == "ok" and row["network"]["repair_rounds"] == 1, row
    finally:
        ctx.chunk.close()


# --- the engine itself -------------------------------------------------------------------------------------------


def test_the_engine_counts_the_first_passes_that_ended_and_what_the_repair_sent_again(rig):
    clock = [100.0]

    class Timed(Runner):
        """the first pass takes 5s of the engine's clock, the repair 7s (as `test_chunk_rate_engine_3163`)"""
        def __call__(self, cmd):
            process = super().__call__(cmd)
            cost, wait = (5.0, 7.0)[len(self.commands) - 1], process.wait

            def timed(timeout=None):
                clock[0] += cost
                return wait(timeout)
            process.wait = timed
            return process

    rig.runner = Timed()
    engine = rig.make(clock=lambda: clock[0])
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    before = engine.stream_view(SID)
    assert before["throughput_bps"] is None and before["first_pass"] is False, "no first pass ended: not measured"
    assert before["first_pass_s"] is None and before["away"] is None and before["chunks"] is None
    assert before["lost_chunks"] is None
    assert engine.status()["first_pass_s"] is None and engine.status()["away"] is None, "/overview.sender: not 0 before a pass"
    assert engine.stream_view("another-wave") is None
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]}, skipped={"B1": 0})
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]}, skipped={"C1": 0})["action"] == "receive"
    live, status = engine.stream_view(SID), engine.status()
    assert live["throughput_bps"] == 2, live              # ten one-byte frames (`Lib`) over the 5s of the first pass
    assert live["first_pass"] is True and live["chunks"] == 10 and live["lost_chunks"] == 3 and live["away"] == 0, live
    assert live["first_pass_s"] == 5.0 and live["stream_zbytes"] == 10
    assert live["multicast_chunks"] == status["multicast_chunks"] > 0 and live["repair_rounds"] == status["repair_rounds"]
    assert (status["first_pass_s"], status["lost_chunks"], status["stream_zbytes"]) == (5.0, 3, 10), status
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    rig.tell("b", 2, "ended", {"B1": ALL})
    assert rig.ask("c", 2, {"C1": [[0, 8]]})["action"] == "fetch"
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    done, outcome = engine.stream_view(SID), engine.rate_outcome(SID)
    assert done["throughput_bps"] == 2 and done["first_pass_s"] == 5.0, "the repair rounds are not in the rate"
    assert (outcome["multicast_chunks"], outcome["fetched_chunks"]) == (done["multicast_chunks"], done["fetched_chunks"])
    assert outcome["fetched_chunks"] > 0 and outcome["repair_rounds"] == 2


# --- 2. the round history ----------------------------------------------------------------------------------------


def test_the_history_reads_a_chunk_wave_loss_and_what_it_sent(conn):
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00", chunk={"stream_zbytes": 5_000_000_000, "lost_chunks": 3})
    _wave(conn, "ses_2", "2026-11-21T08:10:00+00:00", chunk={"stream_zbytes": 4_000_000_000, "lost_chunks": None})
    _wave(conn, "ses_3", "2026-11-21T08:20:00+00:00", chunk={"stream_zbytes": 1_000_000_000, "lost_chunks": 4})
    waves = memory_view.round_waves(conn, T0, "2026-11-21T09:00:00+00:00")
    assert [(w["sent_bytes"], w["lost_chunks"], w["loss_blocks"]) for w in waves] == [
        (5_000_000_000, 3, None), (4_000_000_000, None, None), (1_000_000_000, 4, None)]
    got = memory_view.flow(waves)
    assert got["lost_chunks"] == 7 and got["loss_blocks"] is None and got["rate_bps"] == 600_000_000, got


def test_the_history_of_a_chunk_wave_that_measured_no_loss_is_not_zero(conn):
    _wave(conn, "ses_1", "2026-11-21T08:01:00+00:00", chunk={"stream_zbytes": None, "lost_chunks": None})
    got = memory_view.flow(memory_view.round_waves(conn, T0, "2026-11-21T09:00:00+00:00"))
    assert got["lost_chunks"] is None and got["loss_blocks"] is None
    [wave] = memory_view.round_waves(conn, T0, "2026-11-21T09:00:00+00:00")
    assert wave["sent_bytes"] is None


# --- 3. the facts the brain keeps ------------------------------------------------------------------------------


def test_the_brain_keeps_a_chunk_wave_sent_bytes_and_lost_chunks():
    row = {"decision": json.dumps({"max_bitrate": "600m"}), "drops": None,
           "stream": json.dumps({"chunk": {"stream_zbytes": 7_000_000_000, "lost_chunks": 6}})}
    got = memory._wave(row)
    assert got["sent_bytes"] == 7e9 and got["lost_chunks"] == 6.0 and got["loss_blocks"] is None
    old = memory._wave({**row, "stream": json.dumps({"sent_bytes": 10, "loss_blocks": 2})})
    assert old["sent_bytes"] == 10.0 and old["loss_blocks"] == 2.0 and old["lost_chunks"] is None


def test_the_brain_sums_a_round_of_chunk_waves(tmp_path):
    from datetime import timedelta                                         # noqa: PLC0415
    from server.system import housekeeping                                  # noqa: PLC0415
    from test_brain_memory_3185 import NOW, ago, summary                    # noqa: PLC0415
    c = db.connect(tmp_path / "memory.db")
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("INSERT INTO room_rounds (id, image_id, target_drives, written_drives, state, opened_by, created_at, closed_at)"
              " VALUES ('rnd_c', 'img', 2, 2, 'closed', 'noc', ?, ?)",
              ((NOW - timedelta(days=85, minutes=40)).isoformat(timespec="seconds"), ago(85)))
    for sid, days, zbytes, lost in (("w1", 85.02, 3_000_000_000, 4), ("w2", 85.01, 2_000_000_000, 1)):
        c.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?,?,?,?)",
                  (sid, json.dumps({"max_bitrate": "600m"}), ago(days),
                   json.dumps({"chunk": {"stream_zbytes": zbytes, "lost_chunks": lost}})))
    c.commit()
    assert housekeeping.purge_tables(c, NOW)["failed"] == {}
    num = {k: v["last"] for k, v in summary(c, "round", "rnd_c", "room_rounds")["num"].items()}
    assert num["sent_bytes"] == 5_000_000_000 and num["lost_chunks"] == 5 and "loss_blocks" not in num, num


# --- 4. B58 names only what is measured -----------------------------------------------------------------------


def test_the_net_loss_row_has_local_and_no_always_empty_keys(conn):
    from server.brain import sendlimit, veto                                 # noqa: PLC0415
    veto.forget()
    assessed = {"state": "ok", "kind": "chunk", "interface": "eth1", "drawers": 2, "rate": "400m", "loss": 0.5,
                "baseline": 0.4, "waves": 3, "local": 1.5, "local_baseline": 1.0}
    assert sendlimit.note_netloss(conn, "ses_n", assessed) is not None
    [row] = [d for d in memory.decisions(conn, rule="B58", limit=50) if d["action"] == "NET_LOSS"]
    evidence = row["evidence"] if isinstance(row["evidence"], str) else " ".join(row["evidence"])
    assert "local=" in evidence and "local_baseline=" in evidence, evidence
    for gone in ("rcvbuf", "timeouts"):
        assert gone not in evidence, evidence
