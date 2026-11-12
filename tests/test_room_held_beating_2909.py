"""#2909 -- a cloner that holds its results (#2024/#2031) but keeps saying hello.

The local clone's screens keep the attended beat (`attended.sh`) alive, also while
`end_countdown` holds the results until "כבה": a non-joining hello every ~10 s next
to the light `held` report. The server counted that hello as "came back" --
`_unseen` compared `last_seen` with the wave's opening only -- so a new round that
chose this cloner got no re-wake, no cap, and sat `active` for ever (the round
failed by name only for a cloner that sent no hello at all: test_held_console_2031).

A cloner with a fresh `held` report is not back in the queue, whatever its hello
says. It is "unseen" for the re-wake rule: the round fails in its name after the cap,
with the #2031 sentence ("דלוק ומחכה ללחיצה על 'כבה'"). A joining hello still
clears the hold, and a cloner that is not held keeps being left alone.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from server.rounds import room as room_mod  # noqa: E402
from test_held_console_2031 import held_report  # noqa: E402
from test_room_rewake_1721 import CAP, STEP, _packets_to, _round, _wave1_partial  # noqa: E402
from test_server_room import CLONER1, cloner_hello, room_server  # noqa: E402,F401


def test_a_held_cloner_that_keeps_saying_hello_does_not_hold_the_round_for_ever(room_server, monkeypatch):
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        held_report(anon)                                          # the hold's own report (#2031)
        cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)   # the local screen's beat (attended.sh)
        assert CLONER1 not in {m["mac"] for m in ctx.store.members(wave2)}, "a non-joining hello joined the wave"
        room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) - before == CAP, "no re-wake: the beat made the cloner 'seen'"
    row = _round(ctx)
    assert row["state"] == "failed", row
    assert "shich-1" in row["failed_reason"] and "מחכה ללחיצה על 'כבה'" in row["failed_reason"], row["failed_reason"]


def test_a_held_cloner_that_comes_back_with_a_joining_hello_joins_the_wave(room_server, monkeypatch):
    """Both states: the hold ends with a joining hello (`held.clear`), and then the cloner is a
    member -- the fix must not keep waking and failing a cloner that is back."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    held_report(anon)
    clock.now += timedelta(seconds=STEP + 1)
    room_mod.sweep(ctx.conn, ctx.store)
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])             # joining by default: it is back in the queue
    assert answer["session"] is not None and answer["session"]["id"] == wave2, answer["session"]
    assert CLONER1 in {m["mac"] for m in ctx.store.members(wave2)}
    assert _round(ctx)["state"] == "active"
