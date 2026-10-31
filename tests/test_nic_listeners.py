"""‏#705: "כבוי בכרטיס" = **לא מאזין** בכתובת שלו — על סוקט אמיתי.

‏`Listeners.retarget` מעביר מאזין לקבוצת כתובות אחרת **בלי** לסגור את מה
שנשאר (החיבור שדרכו המפעיל שומר). ‏127.0.0.1 ו-127.0.0.2 משחקים שני
כרטיסים: כל 127/8 הוא loopback וניתן לקשירה (Linux וגם Windows).
"""

from __future__ import annotations

import asyncio
import socket

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from server.net import ports  # noqa: E402

from test_port_listeners import _app, _wait, free_port  # noqa: E402

A, B = "127.0.0.1", "127.0.0.2"


def connects(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
        except OSError:
            return False
        return True


def factory_for(port: int):
    def make(host: str):
        return lambda: uvicorn.Server(uvicorn.Config(
            _app, host=host, port=port, log_level="warning", lifespan="off"))
    return make


def test_retarget_adds_and_removes_addresses_and_keeps_the_one_that_stays():
    port = free_port()

    async def scenario():
        mgr = ports.Listeners()
        make = factory_for(port)
        mgr.add("http_console", [make(A)], port=port, hosts=[A])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("http_console"))
        kept = mgr._servers["http_console"][0]

        await mgr.retarget("http_console", hosts=[A, B], factory_for=make)
        assert connects(A, port) and connects(B, port)
        assert mgr._servers["http_console"][0] is kept, "the address that stays is not reopened"

        await mgr.retarget("http_console", hosts=[B], factory_for=make)
        assert not connects(A, port), "console off on the NIC = nothing listens there"
        assert connects(B, port)
        assert sorted(h for h, _p in mgr.bound("http_console")) == [B]
        assert mgr.is_open("http_console") and not task.done(), "a planned close is not a crash"

        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_a_new_address_that_cannot_bind_leaves_the_old_ones_alone():
    port = free_port()
    squatter = socket.socket()
    squatter.bind((B, port))
    squatter.listen()

    async def scenario():
        mgr = ports.Listeners()
        make = factory_for(port)
        mgr.add("http_console", [make(A)], port=port, hosts=[A])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("http_console"))
        with pytest.raises(ports.ListenerError):
            await mgr.retarget("http_console", hosts=[B], factory_for=make)
        assert connects(A, port), "the failed retarget did not drop the working address"
        assert mgr.spec("http_console")["hosts"] == [A]
        assert not task.done()
        mgr.stop()
        await asyncio.wait_for(task, 10)

    try:
        asyncio.run(scenario())
    finally:
        squatter.close()


def test_retarget_of_a_port_switched_off_opens_nothing():
    port = free_port()

    async def scenario():
        mgr = ports.Listeners()
        make = factory_for(port)
        mgr.add("http_console", [make(A)], port=port, hosts=[A])
        task = asyncio.ensure_future(mgr.serve(lambda pid: False))
        await asyncio.sleep(0.1)
        await mgr.retarget("http_console", hosts=[B], factory_for=make)
        assert not connects(B, port) and mgr.spec("http_console")["hosts"] == [B]
        await mgr.set_open("http_console", True)
        assert connects(B, port) and not connects(A, port)
        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
