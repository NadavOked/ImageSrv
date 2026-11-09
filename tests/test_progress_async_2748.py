"""#2748: a progress report while the DB is write-locked does not stall the server.

`agent_progress` was an `async def` that ran sqlite synchronously on the event
loop. While another connection held the write lock, sqlite's busy handler waited
(up to `busy_timeout`, 5s) *on the loop itself* -- and no other request, a
console `/me` included, was served until the lock was released. The fix reads the
body on the loop and runs the DB work on a worker thread, like hello.

Negative control: on the code before the fix, `/me` finishes only after the lock
is released (~LOCK_SECONDS), and the first assertion below fails.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

LOCK_SECONDS = 2.0
MAC = "02:00:5e:10:00:48"


def test_a_progress_report_on_a_locked_db_does_not_block_another_request(server):
    admin = server["admin"]
    # Warm-up: the session's `touch` writes at most once a minute -- after this
    # call, `/me` below only reads, and reads are not blocked by a writer (WAL).
    assert admin.get("/api/console/me").status_code == 200

    locker = sqlite3.connect(server["ctx"].conn._path, isolation_level=None,
                             check_same_thread=False)
    locker.execute("BEGIN IMMEDIATE")            # the write lock, held by "someone else"
    released = threading.Event()

    def release():
        locker.execute("ROLLBACK")
        released.set()

    timer = threading.Timer(LOCK_SECONDS, release)

    # A report on a round that does not exist is journaled
    # (`report_on_closed_session`) -- a write that waits for the lock.
    report = {"session_id": "ses_2748_none", "mac": MAC, "state": "writing", "targets": []}

    async def scenario():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                     cookies=admin.cookies) as client:
            t0 = time.monotonic()

            async def progress():
                r = await client.post("/api/v1/agent/progress", json=report)
                return r, time.monotonic() - t0

            async def light():
                await asyncio.sleep(0.2)          # the report is already waiting on the lock
                r = await client.get("/api/console/me")
                return r, time.monotonic() - t0

            return await asyncio.gather(progress(), light())

    timer.start()
    try:
        (prog, prog_at), (me, me_at) = asyncio.run(scenario())
    finally:
        timer.cancel()
        if not released.is_set():
            release()
        locker.close()

    assert me.status_code == 200, me.text
    assert me_at < LOCK_SECONDS - 0.5, (
        f"/me finished {me_at:.2f}s after the lock was taken; the lock was held"
        f" {LOCK_SECONDS}s -- the progress report blocked the event loop")
    # The report itself is unchanged: it waited for the lock and then answered as before.
    assert prog.status_code == 200 and prog.json()["code"] == "not_open", prog.text
    assert prog_at >= LOCK_SECONDS - 0.1, prog_at
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'report_on_closed_session'"
        " AND detail LIKE ?", (f"{MAC}%",)).fetchone()["n"] == 1
