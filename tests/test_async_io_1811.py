"""#1811 -- a hello that waits on the DB, and a console upload that waits on a
slow disk, no longer hold the event loop that every agent, the console and
GRUB's ``/boot`` share.

One event loop, as in production (httpx ASGITransport, the pattern of
test_capture_verify_1470.py): the slow step is held by a gate -- in the hello,
``build_answer`` (where the DB work is: a ``busy_timeout`` of 5 s on a held
write lock looks exactly like this); in the upload, ``import_tar``. While it is
held, a second request on the same loop must be answered; the gate is released
only after it. Before the fix the loop itself sat in the gate, the second
request waited for the release, and the recorded order was the other way round.

**Negative control** (server/agent/api.py and server/console/console_library.py of 9001fc91):
both tests fail on the order -- ``['released', 'second']``.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from server.agent import api  # noqa: E402
from server.console import console_library  # noqa: E402

from conftest import hello_body  # noqa: E402
from test_capture import setup_build_machine  # noqa: E402


class Gate:
    """Wraps ``real``: the first call waits for ``release`` (or 8 s)."""

    def __init__(self, real) -> None:
        self.real = real
        self.entered = threading.Event()
        self.release = threading.Event()
        self.order: list[str] = []

    def __call__(self, *args, **kwargs):
        if not self.entered.is_set():
            self.entered.set()
            self.release.wait(8)
            self.order.append("released")
        return self.real(*args, **kwargs)


async def _second_request_while_held(app, gate: Gate, first, cookies=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                 cookies=cookies) as client:
        held = asyncio.create_task(first(client))
        assert await asyncio.to_thread(gate.entered.wait, 10), "the slow step was never reached"
        second = await asyncio.wait_for(client.get("/api/console/health/live"), 15)
        gate.order.append("second")
        gate.release.set()
        return second, await held


def test_a_hello_held_in_the_db_does_not_hold_the_loop(server, monkeypatch):
    mac = setup_build_machine(server)
    gate = Gate(api.build_answer)
    monkeypatch.setattr(api, "build_answer", gate)
    second, hello = asyncio.run(_second_request_while_held(
        server["app"], gate, lambda c: c.post("/api/v1/agent/hello", json=hello_body(mac))))
    assert gate.order == ["second", "released"], (gate.order, "the loop waited for the hello's DB (#1811)")
    assert second.status_code == 200, second.text
    assert hello.status_code == 200 and hello.json()["known"] is True, hello.text


def test_an_upload_held_in_the_import_does_not_hold_the_loop(server, monkeypatch):
    gate = Gate(console_library.import_tar)
    monkeypatch.setattr(console_library, "import_tar", gate)
    second, upload = asyncio.run(_second_request_while_held(
        server["app"], gate,
        lambda c: c.post("/api/console/images/upload", content=b"not a tar" * 200_000),
        cookies=dict(server["admin"].cookies)))
    assert gate.order == ["second", "released"], (gate.order, "the loop waited for the upload's import (#1811)")
    assert second.status_code == 200, second.text
    assert upload.status_code == 400, upload.text        # the real import_tar ran after: not a tar
