"""#3706: the session store writes under `_write_lock`, like every other hot writer.

`record_hello` (every hello of every machine), `close`, `_transition` (start),
`fail` and `open` ran on the request thread's own connection with no
`_write_lock` -- `record_hello` and `fail` inside `writing` only, the other three
with a bare `commit()` (the debt list of `test_write_lock_rule`). Another thread
inside `with _write_lock, writing(conn)` holds sqlite's write lock, and sqlite does
not promise a waiting writer ever gets it (the fairness note on `db._write_lock`,
#272): the bare statement sleeps in the busy handler until `busy_timeout` and raises
`database is locked`. Seen on the Testrunner on 18/11: a hello, straight after a
round was opened, died at the `INSERT INTO session_members`
(`test_the_crc_rise_of_the_round_reaches_the_drawer_and_absence_is_not_zero`).

The race is made deterministic instead of waited for: a thread stands in for the
brain gates thread, holds `_write_lock` *and* a real open write transaction on its
own connection, and lets go only once the operation has queued behind the Python
lock (fixed code) or has given up (old code). No sleeps.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from conftest import HANG_S

from server import db as db_module
from server.db import connect
from server.rounds import sessions as sessions_module
from server.rounds.sessions import SessionStore

GROUP = "grp_LAB1"
IMAGE = "img_7f3a91"
MAC = "aa:bb:cc:dd:ee:01"


class _QueueProbe:
    """The real write lock, plus events that say someone has queued for it."""

    def __init__(self, real, *queued: threading.Event) -> None:
        self._real, self._queued = real, queued

    def __enter__(self):
        for event in self._queued:
            event.set()
        self._real.acquire()
        return self

    def __exit__(self, *_exc):
        self._real.release()
        return False


@pytest.fixture()
def store(tmp_path: Path):
    db = connect(tmp_path / "sessions.db")
    db.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
               (GROUP, "כיתה"))
    db.commit()
    yield SessionStore(db)
    db.close()


def _open(store: SessionStore) -> str:
    return store.open(GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc")


def _row(store: SessionStore, session_id: str) -> sqlite3.Row:
    return store.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()


def _state(store: SessionStore, session_id: str) -> str:
    return _row(store, session_id)["state"]


#: name -> (what to call, given the store and an open session id; what it must return)
OPERATIONS = {
    "record_hello": (lambda s, sid: s.record_hello(_row(s, sid), MAC), True),
    "close": (lambda s, sid: s.close(sid, "noc"), True),
    "start": (lambda s, sid: s.start_auto(sid), None),
    "fail": (lambda s, sid: s.fail(sid, "the sender died"), True),
    "open": (lambda s, sid: _open(s) and None, None),
    "open_replacing": (lambda s, sid: s.open(
        GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc", replaces=sid) and None, None),
}


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_a_session_write_queues_behind_a_writer_instead_of_failing_with_locked(
    store, monkeypatch, name,
):
    call, expected = OPERATIONS[name]
    # `open` needs the slot free; everything else needs a session to act on.
    session_id = None if name == "open" else _open(store)

    queued, release = threading.Event(), threading.Event()
    # `raising=False`: on the old module the name does not exist, and the test must fail
    # on what the code does (`database is locked`), not on a missing attribute.
    monkeypatch.setattr(
        sessions_module, "_write_lock",
        _QueueProbe(db_module._write_lock, queued, release), raising=False)
    held = threading.Event()

    def hold() -> None:
        """A thread caught in the middle of `with _write_lock, writing(conn)`."""
        with db_module._write_lock:
            store.conn.execute("INSERT INTO settings (key, value) VALUES ('probe', '1')"
                               " ON CONFLICT (key) DO UPDATE SET value = excluded.value")
            held.set()
            # Let go once the operation queues behind us (fixed code) or has given up
            # (old code, which never asks for the lock and so never queues).
            release.wait(HANG_S)
            store.conn.rollback()

    writer = threading.Thread(target=hold, name="stand-in-writer", daemon=True)
    writer.start()
    assert held.wait(HANG_S), "the stand-in writer never took the lock"

    outcome: list[object] = []

    def run() -> None:
        try:
            store.conn.execute("PRAGMA busy_timeout = 100")   # a short wait, not 5 s, on old code
            outcome.append(call(store, session_id))
        except BaseException as exc:                          # noqa: BLE001 -- the test reads it
            outcome.append(exc)

    worker = threading.Thread(target=run, name=f"session-{name}", daemon=True)
    worker.start()
    worker.join(HANG_S)
    assert not worker.is_alive(), f"{name} never returned"
    release.set()
    writer.join(HANG_S)
    assert not writer.is_alive(), "the stand-in writer never let go"

    assert len(outcome) == 1 and not isinstance(outcome[0], BaseException), \
        f"{name} failed behind a writer: {outcome!r}"
    assert outcome[0] == expected, f"{name} returned {outcome[0]!r}"
    assert queued.is_set(), f"{name} never asked for the write lock"

    # What it wrote is there, read back from the main thread's own connection.
    if name == "record_hello":
        assert store.is_member(session_id, MAC)
    elif name == "close":
        assert _state(store, session_id) == "closed"
    elif name == "start":
        assert _state(store, session_id) == "running"
    elif name == "fail":
        assert _state(store, session_id) == "failed"
    elif name == "open":
        assert store.conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1
    else:
        assert _state(store, session_id) == "closed"
        assert store.conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE state = 'open'").fetchone()["n"] == 1
