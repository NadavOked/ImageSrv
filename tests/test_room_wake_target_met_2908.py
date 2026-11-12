"""#2908 -- `wake_check` lowers the target to what is already written: the round is DONE.

`wake_check` (#1954) takes a slot whose disk was swapped while the cloner slept out of
the round and lowers `target_drives` by one -- without recording it as skipped or
cancelled. When that brings the target down to exactly what the earlier waves wrote and the
next wave is open, `tick` saw `ready == 0 and remaining <= 0` and failed the round as
"every drawer is red" (`written=1/1`, `skipped=[]`, a `room_failed` mail): the operator
reads a failure for a round that wrote everything it was still allowed to write.

Now a target that is met closes the round `room_done`, with the number of slots skipped at
the wake in the journal. Nothing is written to the swapped disk (#1954), and a round whose
whole target went away before anything was written is still not a "done".
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.rounds import room as room_mod  # noqa: E402
from test_room_sleeping_cloner_1954 import _asleep, _item, _round_row  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, _open_with_slots, cloner_hello, report, room_server,
)


def _events(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY rowid", (event,))]


def _row(ctx) -> dict:
    return dict(ctx.conn.execute(
        "SELECT state, failed_reason, written_drives, target_drives, wave_session_id"
        " FROM room_rounds ORDER BY rowid DESC").fetchone())


def _wave2_waits_for_a_sleeper(srv) -> None:
    """Wave 1 writes cloner 1's disk; wave 2 is open for cloner 2, bound to a disk (it was off)."""
    ctx, anon, deploy = srv["ctx"], srv["anon"], srv["deploy"]
    cloner_hello(anon, CLONER1, ["S1"])
    cloner_hello(anon, CLONER2, ["S3"])
    _asleep(srv, CLONER2)
    opened = _open_with_slots(srv["admin"], [{"mac": CLONER1, "ports": [1]},
                                             {"mac": CLONER2, "ports": [1]}], target=2)
    assert opened.status_code == 200, opened.text
    wave1 = cloner_hello(anon, CLONER1, ["S1"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200
    report(anon, wave1, CLONER1, {"sda": "done"})
    room_mod.sweep(ctx.conn, ctx.store)
    row = _row(ctx)
    assert (row["state"], row["written_drives"], row["target_drives"]) == ("active", 1, 2), row
    assert row["wave_session_id"] != wave1
    assert _item(srv, CLONER2)["expect"] == {"1": "S3"}


def test_a_target_lowered_to_what_is_written_closes_the_round_done_not_failed(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _wave2_waits_for_a_sleeper(room_server)
    room_mod.sweep(ctx.conn, ctx.store)                       # both states: still waiting for the sleeper
    assert _row(ctx)["state"] == "active"

    answer = cloner_hello(anon, CLONER2, ["X9"])              # it woke with another disk in the slot
    assert answer["session"] is None                          # never written (#1954)
    assert _item(room_server, CLONER2)["wake_skipped"] == [{"port": 1, "serial": "S3", "found": "X9"}]
    room_mod.sweep(ctx.conn, ctx.store)

    row = _row(ctx)
    assert row["state"] == "closed", row
    assert (row["written_drives"], row["target_drives"]) == (1, 1)
    assert row["failed_reason"] is None
    assert _events(ctx, "room_failed") == [], "the round is reported as a failure and mailed"
    assert [d.split(" ", 1)[1] for d in _events(ctx, "room_done")] == ["written=1/1 wake_skipped=1"]
    assert not ctx.conn.execute(
        "SELECT id FROM sessions WHERE state IN ('open', 'running')").fetchall(), "the wave kept the slot"


def test_a_round_whose_whole_target_went_away_before_anything_was_written_is_not_done(room_server):
    """Guard against an over-broad fix: written 0 of a target that wake_check took to 0 is no
    completion (#2841: `written=0/N` must not look like one)."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    _asleep(room_server, CLONER1)
    assert _open_with_slots(room_server["admin"], [{"mac": CLONER1, "ports": [2]}], target=1).status_code == 200
    assert cloner_hello(anon, CLONER1, ["S1", "X9"])["session"] is None
    assert _round_row(room_server)["target_drives"] == 0
    room_mod.sweep(ctx.conn, ctx.store)
    assert _row(ctx)["state"] != "closed"
    assert _events(ctx, "room_done") == []
