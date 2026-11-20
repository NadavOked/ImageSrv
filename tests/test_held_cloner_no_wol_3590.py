"""#3590 -- a cloner that is lit and holds its results (#2024/#2031) is not sent a WoL.

On metal (17/11, cloner 2): a drawer was pulled in the middle of wave 1, the cloner stayed ON
on the failure screen (#2024, by design) and answered ping -- but it sends no hello, only the
light `held` report, so the server saw it as "not back in the queue". Wave 2 re-woke it six
times (`room_rewake`, a WoL to a machine that is already on), and 9 minutes after the wave
opened the round failed with the #2031 sentence "דלוק ומחכה ללחיצה על 'כבה'" -- the server
knew the machine was waiting for a human and kept sending it WoL anyway.

Wanted: no WoL (not the wave's, not the re-wake, not the early resend) to a machine with a
fresh `held` report. NOT wanted: a longer ceiling, a hidden failure (the round still fails in
the cloner's name, on the same clock), shutting the cloner down for the wave (#2024: it stays
on for the human), or `awake=True` for every lit machine (a held machine is lit *and*
reporting -- positive evidence -- a machine that is lit and silent is not).

A cloner with NO fresh held report (really off) is re-woken exactly as in #1721.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import db  # noqa: E402
from server.agent import held  # noqa: E402
from server.learn import wol as learn_wol  # noqa: E402
from server.brain import silence  # noqa: E402
from server.net import wol  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_held_console_2031 import held_report  # noqa: E402
from test_room_rewake_1721 import CAP, STEP, _events, _packets_to, _round, _wave1_partial  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, room_server  # noqa: E402,F401

DEADLINE = silence.REWAKE_MAX * silence.REWAKE_S + silence.REWAKE_BOOT_S


def _tick_until_failed(room_server, clock, report_held: bool) -> int:
    """Seconds after the wave opened at which the round failed (None-safe: asserts it did)."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    for ticks in range(1, CAP + 10):
        clock.now += timedelta(seconds=STEP)
        if report_held:
            held_report(anon)                      # the hold's own beat (#2031)
        room_mod.sweep(ctx.conn, ctx.store)
        if _round(ctx)["state"] == "failed":
            return ticks * STEP
    raise AssertionError(f"the round never failed: {_round(ctx)}")


def test_a_held_cloner_in_an_open_wave_is_not_re_woken_and_the_round_still_fails_in_its_name(
        room_server, monkeypatch):
    """The metal run: wave 2 open, the only cloner it waits for is lit and held."""
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, _wave2 = _wave1_partial(room_server, monkeypatch)
    before, sent_rows = _packets_to(woken, CLONER1), len(_events(ctx, "wol_sent"))
    held_report(anon)

    failed_at = _tick_until_failed(room_server, clock, report_held=True)

    assert _events(ctx, "room_rewake") == [], "a WoL to a machine that is lit and waiting for a human"
    assert _packets_to(woken, CLONER1) == before, "a magic packet reached a held cloner"
    assert len(_events(ctx, "wol_sent")) == sent_rows
    # the failure is the same failure, on the same clock: nothing is hidden and nothing is longer
    assert failed_at == DEADLINE, f"failed after {failed_at}s, the ceiling is {DEADLINE}s"
    row = _round(ctx)
    assert "shich-1" in row["failed_reason"] and "מחכה ללחיצה על 'כבה'" in row["failed_reason"], row["failed_reason"]
    assert len(_events(ctx, "room_wake_failed")) == 1


def test_a_cloner_that_is_really_off_is_still_re_woken_six_times(room_server, monkeypatch):
    """Guard (both states): no held report = the #1721 rule as it was -- six WoL, then it fails."""
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _wave2 = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)

    failed_at = _tick_until_failed(room_server, clock, report_held=False)

    assert _packets_to(woken, CLONER1) - before == CAP
    assert len(_events(ctx, "room_rewake")) == CAP
    assert failed_at == DEADLINE
    assert "Wake-on-LAN" in _round(ctx)["failed_reason"]


def test_a_cloner_whose_hold_ended_is_re_woken_again(room_server, monkeypatch):
    """The human pressed "כבה": the hold report goes stale (and a delivered power-off clears
    it). From then on the machine IS off -- the re-wake must come back, not stay muted."""
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, _wave2 = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)
    held_report(anon)
    clock.now += timedelta(seconds=STEP + 1)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before                  # held: nothing

    held.clear(ctx.conn, CLONER1)                                  # what a delivered power-off does
    clock.now += timedelta(seconds=STEP)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) > before, "the hold ended and the cloner was never woken"
    assert _events(ctx, "room_rewake"), "a re-wake with no journal line"


def test_the_group_wake_skips_a_held_cloner_and_wakes_the_other(room_server):
    """The wave-open path (`on_opened` -> `wake_group`): a held cloner is lit, so no packet --
    the other registered cloner is woken as before."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])         # the machine has said hello once, as on metal
    held_report(anon)
    packets: list[bytes] = []

    sent = wol.wake_group(ctx.conn, "grp_CLONERS", send=packets.append)

    assert int(sent) == 1 and _packets_to(packets, CLONER2) == 1 and _packets_to(packets, CLONER1) == 0
    held.clear(ctx.conn, CLONER1)
    packets.clear()
    assert int(wol.wake_group(ctx.conn, "grp_CLONERS", send=packets.append)) == 2    # both states


# --- the early resend of the wake learner (#1847) -------------------------------------

MAC = "78:ac:c0:9b:11:c2"
T0 = 1_760_000_000.0


@pytest.fixture()
def learner(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, '02', 'grp_CLONERS', 'x')", (MAC,))
    c.execute("INSERT INTO net_devices (mac) VALUES (?)", (MAC,))
    c.commit()
    at = T0                                           # three answered wakes: a typical time of 14 s
    for _ in range(learn_wol.MIN_SAMPLES):
        learn_wol.sent(c, MAC, now=at)
        learn_wol.answered(c, MAC, now=at + 14)
        at += learn_wol.ANSWER_S + 10
    learn_wol.sent(c, MAC, now=at)                    # the wake of this wave, still open
    c.at = at
    return c


def test_the_early_resend_is_not_sent_to_a_held_cloner(learner):
    """The wave's WoL went out four seconds before the hold began; 30 s later the learner
    re-sent it (`wol_resent`) to a lit machine."""
    now = learner.at + 30
    held.note(learner, MAC, [], now=datetime.fromtimestamp(now - 5, timezone.utc))
    packets: list[bytes] = []

    learn_wol.sweep(learner, send=packets.append, now=now)

    assert packets == [] and learner.execute(
        "SELECT COUNT(*) FROM journal WHERE event = 'wol_resent'").fetchone()[0] == 0


def test_the_early_resend_still_goes_to_a_cloner_that_is_not_held(learner):
    """Guard (both states): no hold = the #1847 resend as it was."""
    packets: list[bytes] = []

    learn_wol.sweep(learner, send=packets.append, now=learner.at + 30)

    assert len(packets) == 1 and learner.execute(
        "SELECT COUNT(*) FROM journal WHERE event = 'wol_resent'").fetchone()[0] == 1


def test_a_held_wave_with_a_future_timestamp_still_fails_at_the_ceiling(room_server, monkeypatch):
    """#1771 meets #3590: a wave whose `created_at` is in the future and that waits only for a
    held cloner sends no re-wake -- nothing real-time to measure from. It must not freeze."""
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, wave = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)
    future = (clock.now + timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (future, wave))
    ctx.conn.commit()

    failed_at = _tick_until_failed(room_server, clock, report_held=True)

    assert failed_at <= DEADLINE + STEP, f"a future created_at delayed the failure to {failed_at}s"
    assert _packets_to(woken, CLONER1) == before and _events(ctx, "room_rewake") == []
    assert "מחכה ללחיצה על 'כבה'" in _round(ctx)["failed_reason"]
