"""#3770 -- a wall-clock step does not make a live machine "not connected" in the monitor.

`machine_rows` and `_target` decide "connected" by `now - net_devices.last_seen`
(ONLINE_SECONDS). `last_seen` is a stored wall-clock stamp, so a +3 h step makes a
machine that reported seconds ago look gone, and a -3 h step puts the stamp in the
future. #1947's `clockstep.rebase` moves `last_seen` too, but only when somebody
calls `clockstep.guard` -- the question here is whether a monitor request does so
before it decides. The monitor module's `datetime` is faked where production reads
it, and a step moves only the wall clock, never the monotonic clock (what a step is).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from server.console import monitor
from server.system import clockstep

from test_monitor import IP, MAC, prepare_build_machine

STEP = 3 * 60 * 60


class _Clock:
    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.mono = time.monotonic()

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    def jump(self, seconds: float) -> None:      # a step: wall clock only
        self.now += timedelta(seconds=seconds)


def _install(monkeypatch) -> _Clock:
    clock = _Clock()

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is not None else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(monitor, "datetime", _FakeDatetime)
    monkeypatch.setattr(clockstep, "_mono", lambda: clock.mono)
    return clock


@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_a_clock_step_does_not_disconnect_a_live_machine_in_the_monitor(server, monkeypatch, step):
    ctx, admin = server["ctx"], server["admin"]
    prepare_build_machine(server)                      # last_seen = real now (seconds ago)
    clock = _install(monkeypatch)
    clock.now = datetime.now(timezone.utc)             # same instant as last_seen
    # the server's clock watcher has sampled before the step (it runs all the time)
    assert clockstep.guard(ctx.conn, ctx.store, clock.now) is None
    clock.advance(5)
    assert admin.get("/api/console/monitor/machines").json()[0]["online"] is True
    clock.jump(step)

    rows = admin.get("/api/console/monitor/machines").json()
    assert [(r["mac"], r["online"]) for r in rows] == [(MAC, True)], \
        "a clock step made a machine that reported seconds ago look disconnected"
    assert monitor._target(ctx, MAC)[0] == IP, "_target refused (409) a live machine after a step"

    # over-fix guard: real silence after the step is still "not connected"
    clock.advance(monitor.ONLINE_SECONDS + 30)
    rows = admin.get("/api/console/monitor/machines").json()
    assert [(r["mac"], r["online"]) for r in rows] == [(MAC, False)]
    with pytest.raises(HTTPException) as err:
        monitor._target(ctx, MAC)
    assert err.value.status_code == 409


@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_silence_before_a_clock_step_stays_silence(server, monkeypatch, step):
    """The step corrects only the step's own offset: a machine already silent
    for longer than the window stays "not connected" (a rebase that sets the
    stamp to "now" would revive it)."""
    ctx, admin = server["ctx"], server["admin"]
    prepare_build_machine(server)
    clock = _install(monkeypatch)
    clock.now = datetime.now(timezone.utc)
    assert clockstep.guard(ctx.conn, ctx.store, clock.now) is None
    clock.advance(monitor.ONLINE_SECONDS + 30)         # real silence, before the step
    assert admin.get("/api/console/monitor/machines").json()[0]["online"] is False
    clock.jump(step)

    rows = admin.get("/api/console/monitor/machines").json()
    assert [(r["mac"], r["online"]) for r in rows] == [(MAC, False)],         "a clock step revived a machine that was silent before it"
    with pytest.raises(HTTPException) as err:
        monitor._target(ctx, MAC)
    assert err.value.status_code == 409
