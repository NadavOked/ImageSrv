"""#3151: a listener that is slow to start under IO load is not a listener that failed.

14/11, vm1113-srv: six virtual cloners wrote to one HDD, and the server died once
with `ListenerError http_boot`; systemd brought it back. The only way `serve()` raises
that at boot, other than a bind failure (which ends the listener task at once and is
tested in test_port_listeners.py), is `_open` giving up after OPEN_TIMEOUT (5 s) without
`started` -- and `started` is set by uvicorn only after its startup work, which on a disk
shared with six writers is exactly what stretches.

A stalled startup is simulated here by a worker thread blocked the way a thread blocked
on a busy disk is (`time.sleep` in `asyncio.to_thread`, the shape of `vmupdate.clear_leftovers`
in the lifespan). OPEN_TIMEOUT is scaled down; the stall is three times it.

Positive evidence stays the rule: a listener that never reports `started` is still a failure
at boot, and one whose task ends (bind failure) is still a failure at once.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from server.net import ports  # noqa: E402

from tests.test_port_listeners import _FakeServer, _factory, _wait, can_connect, free_port  # noqa: E402

OPEN = 0.3          # the 5 s of production, scaled
STALL = 3 * OPEN    # a startup the old ceiling cannot wait out


class _StalledStart(uvicorn.Server):
    """A real uvicorn.Server whose startup waits on a thread blocked in IO."""

    stall = STALL

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await asyncio.to_thread(time.sleep, self.stall)
        await super().startup(sockets)


def _stalled_factory(port: int):
    return lambda: _StalledStart(uvicorn.Config(
        _factory_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))


async def _factory_app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_a_slow_start_at_boot_is_waited_for_not_a_crash(monkeypatch):
    monkeypatch.setattr(ports, "OPEN_TIMEOUT", OPEN)
    port = free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_boot", [_stalled_factory(port)], port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        # the old code raised ListenerError after OPEN seconds: the task is done, the port never opens
        await _wait(lambda: task.done() or mgr.is_open("http_boot"), timeout=15)
        assert not task.done(), f"serve() ended at boot: {task.exception()!r}"
        assert can_connect(port) and ("127.0.0.1", port) in mgr.bound("http_boot")
        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_a_listener_that_never_reports_started_still_fails_boot(monkeypatch):
    """The ceiling moved, it did not go away: no `started` is still a loud failure at boot."""
    monkeypatch.setattr(ports, "OPEN_TIMEOUT", OPEN)
    monkeypatch.setattr(ports, "BOOT_OPEN_TIMEOUT", 2 * OPEN, raising=False)

    class _NeverStarts(_FakeServer):
        async def serve(self):
            while not self.should_exit:
                await asyncio.sleep(0.01)

    server = _NeverStarts("wait")
    mgr = ports.Listeners()
    mgr.add("http_boot", [lambda: server], port=1, hosts=[])
    with pytest.raises(ports.ListenerError):
        asyncio.run(asyncio.wait_for(mgr.serve(lambda pid: True), 15))
    assert server.should_exit


def test_a_runtime_open_keeps_the_short_ceiling(monkeypatch):
    """A console toggle has a person waiting: it fails in OPEN_TIMEOUT, as a request error,
    and the listeners that are up stay up."""
    monkeypatch.setattr(ports, "OPEN_TIMEOUT", OPEN)
    boot_port, late_port = free_port(), free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_boot", [_factory(boot_port)], port=boot_port, hosts=["127.0.0.1"])
        mgr.add("late", [_stalled_factory(late_port)], port=late_port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: pid == "http_boot"))
        await _wait(lambda: mgr.is_open("http_boot"))
        with pytest.raises(ports.ListenerError):
            await mgr.set_open("late", True)
        assert mgr.is_open("http_boot") and can_connect(boot_port)
        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
