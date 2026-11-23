"""#3912 — re-wakes after a held cloner goes silent are spaced from that moment.

Metal 20/11, ses_b0759389: the wave opened at 10:46:35Z and cloner 2 stayed held
(no WoL, correctly) for about nine minutes. At 10:55:18Z it went off. Four
`room_rewake` rows then landed in 15 seconds, one per room tick (~5 s), because
the age was the wave's age (530 s already satisfied every slot up to the cap).
After the sixth note the same age was already past the boot ceiling, so the
round would have failed with no time left to boot.

The interval stays `REWAKE_S`, counted from the moment the machine went from
held to silent. The boot ceiling (`REWAKE_BOOT_S`) is counted from the last
note. A machine that was never held keeps the wave-open clock.
"""

from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from server.agent import held  # noqa: E402
from server.brain import silence  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_held_console_2031 import held_report  # noqa: E402
from test_room_rewake_1721 import _packets_to, _round, _wave1_partial  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401

TICK = 5
#: Under the ceiling (540 s). The metal wave had already waited ~530 s.
HELD_S = 500


def _advance(clock, ctx, seconds: int) -> None:
    assert seconds % TICK == 0, seconds
    for _ in range(seconds // TICK):
        clock.now += timedelta(seconds=TICK)
        room_mod.sweep(ctx.conn, ctx.store)


def _end_hold(conn, mac: str, now) -> None:
    """A delivered power-off. The room clock is faked, so the release is stamped
    with that clock when the server records one. Before #3912, `clear` takes no clock."""
    if "now" in inspect.signature(held.clear).parameters:
        held.clear(conn, mac, now=now)
    else:
        held.clear(conn, mac)


def test_rewakes_after_a_long_hold_are_spaced_from_the_release(room_server, monkeypatch):
    """The metal case: ~500 s held, then off. Notes every 60 s from the release, not every tick."""
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)
    held_report(anon)

    for _ in range(HELD_S // TICK):
        clock.now += timedelta(seconds=TICK)
        held_report(anon)
        room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before, "a WoL while the cloner was still held"
    assert _round(ctx)["state"] == "active"

    _end_hold(ctx.conn, CLONER1, clock.now)
    sent = lambda: _packets_to(woken, CLONER1) - before  # noqa: E731

    _advance(clock, ctx, silence.REWAKE_S - TICK)
    assert sent() == 0, (
        f"{sent()} re-wakes in the first {silence.REWAKE_S - TICK}s after leaving held "
        "(the wave had already been open long enough for every slot)")

    _advance(clock, ctx, TICK)
    assert sent() == 1, "the first re-wake is due 60s after the machine went silent"

    _advance(clock, ctx, 15)
    assert sent() == 1, f"{sent()} re-wakes in 15s — one per room tick, not one per minute"

    elapsed = silence.REWAKE_S + 15
    for n in range(2, silence.REWAKE_MAX + 1):
        gap = n * silence.REWAKE_S - elapsed
        _advance(clock, ctx, gap - TICK)
        elapsed += gap - TICK
        assert sent() == n - 1, f"note {n} went out at {elapsed}s, before the minute was up ({sent()} sent)"
        _advance(clock, ctx, TICK)
        elapsed += TICK
        assert sent() == n
        assert _round(ctx)["state"] == "active", f"the round failed on note {n}, with no boot time left"

    limit = silence.REWAKE_MAX * silence.REWAKE_S + silence.REWAKE_BOOT_S
    _advance(clock, ctx, limit - elapsed - TICK)
    assert _round(ctx)["state"] == "active", (
        f"failed {limit - elapsed - TICK}s before the boot ceiling, with {sent()} notes sent")
    _advance(clock, ctx, TICK)
    assert sent() == silence.REWAKE_MAX
    assert _round(ctx)["state"] == "failed"


def test_a_cloner_that_was_never_held_still_waits_a_minute_between_ticks(room_server, monkeypatch):
    """Guard: no hold, so the clock is still the wave's opening. A 5 s tick does not burn the notes."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)

    _advance(clock, ctx, silence.REWAKE_S - TICK)
    assert _packets_to(woken, CLONER1) == before

    _advance(clock, ctx, TICK)
    assert _packets_to(woken, CLONER1) == before + 1

    _advance(clock, ctx, 15)
    assert _packets_to(woken, CLONER1) == before + 1
    assert _round(ctx)["state"] == "active"
