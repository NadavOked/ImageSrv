"""#2940 -- a cloner switched off from the console is "unseen", so the round wakes it.

The hold of a cloner that keeps its results (#2031) is cleared by "כבה" and the machine
goes off, but its `last_seen` is still after the wave's opening: `_unseen` called it
"seen" and no WoL was ever sent -- the round waited for a switched-off cloner. "Seen"
now also means the last hello is not older than `AWAKE_SECONDS`. A cloner that beats
(~10 s) is left alone, as before.

#3129 (A-DB-08): a reported number that is not a number is counted 0 as before, but the
server log says so once.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.rounds import room as room_mod  # noqa: E402
from test_room_rewake_1721 import STEP, _packets_to, _wave1_partial  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401


def _iso(delta_s: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_s)).isoformat(timespec="seconds")


def _setup(room_server, monkeypatch, last_seen_ago_s):
    """Wave 2 opened ten minutes ago; the cloner's last hello was `last_seen_ago_s` ago (after the opening)."""
    ctx = room_server["ctx"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    ctx.conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (_iso(-600), wave2))
    ctx.conn.execute("UPDATE net_devices SET last_seen = ? WHERE mac = ?", (_iso(-last_seen_ago_s), CLONER1))
    ctx.conn.commit()
    clock.now = datetime.now(timezone.utc)          # the wave has been open 600 s: a re-wake is due
    return ctx, clock


def test_a_cloner_whose_last_hello_is_old_is_woken_even_though_it_was_seen_after_the_opening(
        room_server, monkeypatch):
    woken = room_server["woken"]
    ctx, _ = _setup(room_server, monkeypatch, last_seen_ago_s=300)
    before = _packets_to(woken, CLONER1)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before + 1, "a switched-off cloner got no WoL (#2940)"


def test_a_cloner_that_is_still_beating_is_left_alone(room_server, monkeypatch):
    woken = room_server["woken"]
    ctx, _ = _setup(room_server, monkeypatch, last_seen_ago_s=5)
    before = _packets_to(woken, CLONER1)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before, "a cloner that is up was woken"


def test_a_number_that_is_not_a_number_is_zero_and_said_once(caplog):
    room_mod._BAD_INT_SEEN.clear()
    with caplog.at_level(logging.WARNING, logger="imagesrv.room"):
        assert room_mod._int("seven", "bytes_written") == 0
        assert room_mod._int("seven", "bytes_written") == 0
        assert room_mod._int(None, "bytes_written") == 0
        assert room_mod._int("12", "bytes_written") == 12
    said = [r for r in caplog.records if "bytes_written" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in caplog.records]
