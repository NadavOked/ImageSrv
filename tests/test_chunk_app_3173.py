"""#3173: what the old sender's tests held about the whole app, on the engine that is left.

`tests/test_sender.py` tested `udp-sender` and went with it. Most of it was the old engine's own command
line; five of its tests were claims about the server that do not depend on the engine, and they ran on a
classroom round (a v2 feature, and one that only the old engine could send). Two of the five already have
a twin on the chunk engine (`test_chunk_nic_gate_2533.py`: a round is not closed while the sender
survives the stop, and a round whose wave already ended still stops the sender). The other three are
here, on a round of the cloning room:

- a round that ripens starts the sender, and closing the round stops it;
- a sender that fails is a failed wave and a failed round, each with the reason, and the cloner that asks
  again is told to boot from its disk -- not to restore again (a restore loop);
- the journal says that the broadcast went out, in Hebrew, with the image's name and not its id.

The sender here is the fake one (`fakesend.Recorder`), and the cloner is `fakesend.Cloners`: the engine
sends a partition only after the cloner said it is listening.
"""

from __future__ import annotations

import threading
from sqlite3 import OperationalError

import pytest

pytest.importorskip("fastapi")

from conftest import HANG_S, MANIFEST_256
from fastapi.testclient import TestClient
from fakesend import Cloners, wait_for
from server.brain.drawerhold import CONTINUE
from test_server_room import CLONER1, cloner_hello, room, room_body, room_server      # noqa: F401 -- a fixture

FIRST_PART = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))


def _wave(room_server) -> tuple[str, Cloners]:
    """A room round of one cloner and one drawer, up to a running wave: the one drawer covers the target, so
    the hello that joins is the one that ripens the wave. (the wave's id, its cloners)"""
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(room_server["anon"], CLONER1, ["S1"])["session"]["id"]
    assert room(room_server["deploy"])["round"]["wave_state"] == "running"
    return wave, Cloners(room_server["anon"], wave, {CLONER1: ["S1"]})


def test_the_round_starts_the_sender_and_closing_stops_it(room_server):
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    recorder.block = True
    wave, cloners = _wave(room_server)
    assert recorder.commands == [], "nothing is sent before a cloner listens"

    cloners.listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    cmd = recorder.commands[0]
    assert cmd[:2] == ["chunkcast", "send"] and cmd[cmd.index("--part") + 1] == str(FIRST_PART)
    status = ctx.chunk.status()
    assert status["session_id"] == wave and status["state"] == "sending"

    closed = room_server["deploy"].post("/api/console/room/close", json={"confirm_name": MANIFEST_256["name"]})
    assert closed.status_code == 200, closed.text
    assert wait_for(lambda: recorder.processes[0].terminated)
    assert len(recorder.commands) == 1, "a closed round sends nothing more"


def test_a_sender_failure_is_a_failed_round_and_the_cloner_boots_from_its_disk(room_server):
    """#854: `send_failed` used to be a line in the journal only; the wave stayed `running` and the member
    was handed the restore again on every boot."""
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    recorder.code = 3
    wave, cloners = _wave(room_server)
    cloners.listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)

    def state():
        return ctx.conn.execute("SELECT state, failed_reason FROM sessions WHERE id = ?", (wave,)).fetchone()

    assert wait_for(lambda: state()["state"] == "failed")
    failed = state()
    assert failed["failed_reason"] and "chunkcast send" in failed["failed_reason"]
    member = ctx.conn.execute("SELECT state, error FROM session_members WHERE session_id = ? AND mac = ?",
                              (wave, CLONER1)).fetchone()
    assert member["state"] == "failed" and member["error"] == failed["failed_reason"]
    assert ctx.store.active_broadcast() is None
    shown = room(room_server["deploy"])["round"]
    assert shown["state"] == "failed" and shown["failed_reason"]

    again = cloner_hello(room_server["anon"], CLONER1, ["S1"], joining=False)
    assert again["session"] is None and again["task"] is None


@pytest.mark.parametrize("fail_write", [False, True])
def test_roster_failure_reaches_the_room_even_when_journal_write_fails(room_server, monkeypatch, fail_write):
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    recorder.block = True
    wave, cloners = _wave(room_server)
    cloners.listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    crashes = []
    monkeypatch.setattr(threading, "excepthook", crashes.append)
    execute = ctx.conn.execute
    journal_rejected = []
    write_rejected = []

    def execute_with_fault(sql, args=()):
        if sql.startswith("INSERT INTO journal") and args[2] == "send_failed":
            journal_rejected.append(True)
            raise OperationalError("journal unavailable")
        if fail_write and not write_rejected and sql.startswith("UPDATE sessions SET state = 'failed'"):
            write_rejected.append(True)
            raise OperationalError("database is locked")
        return execute(sql, args)

    def unreadable(_sid):
        raise OperationalError("roster unavailable")

    monkeypatch.setattr(ctx.conn, "execute", execute_with_fault)
    monkeypatch.setattr(ctx.chunk, "live_members", unreadable)
    recorder.processes[0].release()
    ctx.chunk._thread.join(timeout=HANG_S)
    assert not ctx.chunk._thread.is_alive()
    assert not crashes, [str(c.exc_value) for c in crashes]
    assert journal_rejected == [True]

    if fail_write:
        assert write_rejected
        room_server["app"].state.runtime.retry_chunk_failures()
    _assert_roster_failure(ctx, wave)


def _assert_roster_failure(ctx, wave):
    session = ctx.conn.execute("SELECT state, failed_reason FROM sessions WHERE id = ?", (wave,)).fetchone()
    assert session["state"] == "failed"
    assert "roster unavailable" in session["failed_reason"]
    member = ctx.conn.execute("SELECT state, error FROM session_members WHERE session_id = ? AND mac = ?",
                              (wave, CLONER1)).fetchone()
    assert (member["state"], member["error"]) == ("failed", session["failed_reason"])
    round_row = ctx.conn.execute("SELECT state, failed_reason FROM room_rounds WHERE wave_session_id = ?",
                                 (wave,)).fetchone()
    assert (round_row["state"], round_row["failed_reason"]) == ("failed", session["failed_reason"])


def test_chunk_pass_roster_failure_closes_the_wave_and_stops_the_sender(room_server, monkeypatch):
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    recorder.block = True
    monkeypatch.setattr(ctx.chunk, "carousel_cut", None)
    wave, cloners = _wave(room_server)
    cloners.listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    process = recorder.processes[0]
    assert process.poll() is None

    def unreadable(_sid):
        raise OperationalError("roster unavailable")

    monkeypatch.setattr(ctx.chunk, "live_members", unreadable)
    monkeypatch.setattr(ctx.chunk, "_judge_drawers", lambda *_: {"S1": (CONTINUE, "מגירה איטית")})
    monkeypatch.setattr("server.agent.api.agent_seen", lambda *_: unreadable(wave))
    cloners.client = TestClient(room_server["app"], raise_server_exceptions=False)
    response = cloners.tell(CLONER1, FIRST_PART, "fetching")

    assert response.status_code == 200, response.text
    assert response.json()["action"] == "closed"
    assert process.terminated and process.poll() is not None
    ctx.chunk._thread.join(timeout=HANG_S)
    assert not ctx.chunk._thread.is_alive()
    assert ctx.chunk.status()["state"] == "failed"
    _assert_roster_failure(ctx, wave)


def test_the_journal_reports_the_broadcast_in_hebrew(room_server):
    recorder = room_server["recorder"]
    recorder.block = True
    _wave_id, cloners = _wave(room_server)

    def rows():
        return [r for r in room_server["admin"].get("/api/console/journal").json() if r["event"] == "send_start"]

    assert wait_for(rows), "the engine never said the broadcast went out"
    row = rows()[0]
    assert row["label"] == "השידור יצא לדרך"
    assert MANIFEST_256["name"] in row["text"]
    assert "2 מחיצות" in row["text"]
    assert "img_" not in row["text"]
    cloners.listen(FIRST_PART)                  # the wave goes on to its sender, which the fixture stops
