"""#3129, A-DB-04 -- the wait for the write lock is measured.

Until now a queue on `server.db._write_lock` was invisible until a writer gave up with "database is
locked". The lock is a plain Lock to its users (not reentrant, `acquire(blocking, timeout)`,
`locked`, a context manager), and it now counts the takes that had to wait.
"""
from __future__ import annotations

import logging

import pytest

from server import db


@pytest.fixture
def lock(monkeypatch):
    fresh = db._TimedLock()
    monkeypatch.setattr(db, "_write_wait", {"waits": 0, "total_s": 0.0, "max_s": 0.0, "slow": 0, "gave_up": 0})
    return fresh


def test_a_take_that_did_not_wait_is_not_counted(lock):
    with lock:
        assert lock.locked()
    assert not lock.locked()
    assert db.write_wait_stats()["waits"] == 0


def test_it_is_still_not_reentrant_and_the_nonblocking_take_does_not_count(lock):
    with lock:
        assert lock.acquire(blocking=False) is False
        assert lock.acquire(timeout=0.05) is False        # this one waited, and gave up
    stats = db.write_wait_stats()
    assert stats["waits"] == 1 and stats["gave_up"] == 1, stats
    assert stats["max_s"] >= 0.04 and stats["total_s"] >= 0.04, stats


def test_a_wait_over_the_limit_is_said_in_the_log(lock, monkeypatch, caplog):
    monkeypatch.setattr(db, "WRITE_WAIT_WARN_S", 0.0)
    with lock, caplog.at_level(logging.WARNING, logger="imagesrv.db"):
        assert lock.acquire(timeout=0.02) is False
    assert any("write lock" in r.getMessage() and "gave up" in r.getMessage() for r in caplog.records)
    assert db.write_wait_stats()["slow"] == 1


def test_a_wait_under_the_limit_is_counted_but_not_logged(lock, caplog):
    with lock, caplog.at_level(logging.WARNING, logger="imagesrv.db"):
        lock.acquire(timeout=0.02)
    assert db.write_wait_stats()["waits"] == 1 and db.write_wait_stats()["slow"] == 0
    assert not [r for r in caplog.records if "write lock" in r.getMessage()]


def test_the_module_lock_is_the_measured_one_and_a_write_still_goes_through(tmp_path):
    assert isinstance(db._write_lock, db._TimedLock)
    conn = db.connect(tmp_path / "t.db")
    db.set_setting(conn, "k", "v")
    assert db.get_setting(conn, "k") == "v"
    conn.close()
