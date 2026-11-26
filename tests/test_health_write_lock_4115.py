"""#4115: the wait for the DB write lock (#3129) is a health row the operator can see.

`db.write_wait_stats()` was counted and read by nobody. The row `write_lock` of `GET /api/console/health`
(the list the console renders as it is) now reads the recent window of those waits: green with none, yellow
for a long wait, red for a writer that gave up, and "unknown" (never green) when the counter was not read.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login

from server import db
from server.system import health


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.setattr(db, "_write_wait", {"waits": 0, "total_s": 0.0, "max_s": 0.0, "slow": 0, "gave_up": 0})
    monkeypatch.setattr(db, "_write_wait_recent", deque(maxlen=256))


@pytest.fixture()
def admin(tmp_path: Path, images_root: Path, clock):
    from server.console import users
    from server.app import create_app

    # No "write_wait" hook here: the row must come from the real default, the counter of server.db.
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", now_fn=clock,
                     health_hooks={"interfaces": lambda: []})
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    client = TestClient(app)
    complete_console_login(client, ctx.conn, "noc", "admin-pass-123")
    yield client
    ctx.chunk.stop()


def _row(admin) -> dict:
    rows = {r["id"]: r for r in admin.get("/api/console/health").json()}
    assert "write_lock" in rows, sorted(rows)
    return rows["write_lock"]


def test_the_row_is_green_when_nothing_waited_and_a_take_without_a_wait_is_not_recorded(admin):
    lock = db._TimedLock()
    with lock:
        pass
    assert not db._write_wait_recent                      # the fast path writes nothing
    assert _row(admin)["state"] == "ok"


def test_a_long_wait_turns_the_row_yellow(admin, monkeypatch):
    monkeypatch.setattr(db, "WRITE_WAIT_WARN_S", 0.02)
    lock = db._TimedLock()
    lock.acquire()
    threading.Timer(0.08, lock.release).start()
    assert lock.acquire(timeout=2) is True                  # waited ~0.08 s, got it
    lock.release()
    row = _row(admin)
    assert row["state"] == "warn", row
    assert "1 המתנות" in row["detail"] and "60 הדקות" in row["detail"]


def test_a_writer_that_gave_up_turns_the_row_red(admin):
    lock = db._TimedLock()
    with lock:
        assert lock.acquire(timeout=0.02) is False          # a short give-up: not long, but it failed
    row = _row(admin)
    assert row["state"] == "bad", row
    assert "1 כתיבות ויתרו" in row["detail"]


def test_a_wait_older_than_the_window_clears_the_row(admin):
    db._write_wait_recent.append((time.monotonic() - db.WRITE_WAIT_WINDOW_S - 5, True, False))
    assert _row(admin)["state"] == "ok"
    db._write_wait_recent.append((time.monotonic() - 5, True, True))
    assert _row(admin)["state"] == "warn"


@pytest.mark.parametrize("unread", [None, {}, {"long": 0}, {"long": 0, "gave_up": 0}, {"long": "0", "gave_up": 0, "window_s": 1},
                                    {"long": True, "gave_up": False, "window_s": 3600}, "ok", []])
def test_a_counter_that_was_not_read_is_unknown_never_green(unread):
    row = health.write_lock_check(unread)
    assert row["id"] == "write_lock" and row["state"] == "unknown", row


def test_a_failing_counter_hook_is_unknown_and_does_not_fail_the_page(admin):
    def broken():
        raise OSError("boom")

    hooks = {**health.default_hooks(), "interfaces": lambda: [], "write_wait": broken}
    rows = health.collect(admin.app.state.ctx, hooks, "http://10.44.12.10:7712")
    state = {r["id"]: r["state"] for r in rows}
    assert state["write_lock"] == "unknown" and "db_space" in state


def test_without_the_hook_there_is_no_row_not_a_green_one(admin):
    hooks = {**health.default_hooks(), "interfaces": lambda: []}
    del hooks["write_wait"]
    rows = health.collect(admin.app.state.ctx, hooks, "http://10.44.12.10:7712")
    assert "write_lock" not in {r["id"] for r in rows}
