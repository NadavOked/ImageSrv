"""#1936 (FI O2, 21/10 07:00, VCLONE-2) -- "בטל דיסק זה" in a room round.

A pull to drawers 1 and 2; mid-wave the operator cancelled drawer 2. The drawer
was cancelled and drawer 1 finished `done` -- and then the room opened wave 2
for the cancelled drawer, woke the cloner, and ended `room_failed written=1/2`:

    07:08:22 room_wave room_21ca41ab wave=2 written=1/2   + wol_sent grp_CLONERS
    07:08:45 client_done   (at once, nothing written)
    07:08:47 room_failed room_21ca41ab written=1/2

An operator's cancel is final for that drawer **in this round**: it closes its
place like a red skip (#1760) -- no further wave, no wake, not a failure. The
cancel window says "in the next round the disk is chosen as a normal write":
the next round, not wave 2 of this one.

Negative control: revert server/rounds/room.py and server/db.py together (#439).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_cancel_drawer_1386_server import CANCEL, _drawer, _progress
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, cloner_hello, heartbeat, room, room_body, room_server,
)


def _events(ctx, name: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (name,))]


def _cancel_mid_wave(room_server, target: int) -> tuple[str, int]:
    """A round of `target` over S1/S2; S2 cancelled by the operator, S1 done.
    Returns the round and how many WoL packets were sent before the wave ended."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, target, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"}).status_code == 200
    woken = len(room_server["woken"])
    _progress(anon, wave, CLONER1, {"sda": "done", "sdb": "cancelled"}, top="done")
    heartbeat(room_server)
    return opened.json()["id"], woken


def test_o2_one_written_one_cancelled_ends_the_round_done_without_a_second_wave(room_server):
    """O2 itself: 1 written + 1 cancelled = the round's 2. Not `room_wave wave=2`,
    not a WoL, not `room_failed`."""
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    round_id, woken = _cancel_mid_wave(room_server, 2)

    assert room(deploy)["round"] is None, room(deploy)["round"]
    row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (round_id,)).fetchone()
    assert (row["state"], row["written_drives"], row["failed_reason"]) == ("closed", 1, None)
    assert json.loads(row["cancelled_serials"]) == ["S2"]
    assert _events(ctx, "room_done") == [f"{round_id} written=1/2 cancelled=1"]
    assert _events(ctx, "room_wave") == []
    assert _events(ctx, "room_failed") == []
    assert len(room_server["woken"]) == woken, "the cloner was woken for a wave 2"
    # still not a failure: no red memory for S2 (#1386)
    assert ctx.conn.execute("SELECT COUNT(*) FROM disk_failures").fetchone()[0] == 0


def test_with_more_to_write_the_cancelled_drawer_is_closed_not_rewritten(room_server):
    """Target 4 over two drawers: the round goes on for swapped drawers, but the
    cancelled one is closed in it -- remaining is 2, not 3; the same drawers do not
    join wave 2; a swapped drawer beside it joins, and only its slot is written."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    round_id, _ = _cancel_mid_wave(room_server, 4)

    view = room(deploy)["round"]
    assert (view["written_drives"], view["remaining_drives"], view["wave_number"]) == (1, 2, 2), view
    row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (round_id,)).fetchone()
    assert json.loads(row["cancelled_serials"]) == ["S2"]
    # the same drawers again: S1 written, S2 cancelled -- nothing to write, no join
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"] is None
    sdb = _drawer(room(deploy), CLONER1, "sdb")
    assert (sdb["fresh"], sdb["cancelled_round"]) == (False, True), sdb
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert machine["fresh_drawers"] == 0
    # S1 swapped for S3: the cloner joins, and wave 2 writes slot 1 only -- not S2's slot 2
    session = cloner_hello(anon, CLONER1, ["S3", "S2"])["session"]
    assert session is not None and session["target_ports"] == [1], session


def test_a_failed_drawer_beside_it_is_still_written_in_the_next_wave(room_server):
    """The guard: only the operator's cancel closes a drawer. A drawer that failed
    stays fresh for wave 2 (#1386/#1760 unchanged)."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post("/api/console/room",
                         json=room_body(room_server, 3, {CLONER1: ["S1", "S2", "S3"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]
    if room(deploy)["round"]["wave_state"] == "open":
        assert deploy.post("/api/console/room/start").status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing", "sdc": "writing"})
    assert deploy.post(CANCEL, json={"mac": CLONER1, "dev": "sdb"}).status_code == 200
    _progress(anon, wave, CLONER1, {"sda": "done", "sdb": "cancelled", "sdc": "failed"},
              top="partial")
    heartbeat(room_server)

    view = room(deploy)["round"]
    assert (view["written_drives"], view["remaining_drives"], view["wave_number"]) == (1, 1, 2), view
    assert _events(ctx, "room_failed") == []
    session = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]
    assert session is not None and session["target_ports"] == [3], session
