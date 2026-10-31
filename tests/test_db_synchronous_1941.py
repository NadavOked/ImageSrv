"""#1941 (QA-XR-SVR-02) -- ``PRAGMA synchronous`` is set, and read back, on every connection.

``server/db.py:_open`` set ``journal_mode``, ``foreign_keys`` and ``busy_timeout``
and left ``synchronous`` to the library. Measured 21/10 on the testrunner (Debian
13.7, libsqlite3-0 3.46.1-7+deb13u2): the default there is 2 (FULL), WAL or not --
so a commit is durable today by the build's choice, not by ours. A library built
with ``SQLITE_DEFAULT_WAL_SYNCHRONOUS=1`` (NORMAL) loses the last commit on a power
cut: a ``done`` that was answered and is gone is the restore loop of CLAUDE.md.

The library's default is simulated by wrapping ``sqlite3.connect`` so that every
connection arrives with NORMAL, as such a build hands it out.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from server import db

FULL = 2


@pytest.fixture
def normal_by_default(monkeypatch):
    """Every new connection starts at NORMAL (1), as a build with that default gives it."""
    real = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.execute("PRAGMA synchronous = NORMAL")
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # the simulation itself held
        return conn

    monkeypatch.setattr(db.sqlite3, "connect", connect)


def test_the_connection_is_full_whatever_the_library_says(tmp_path, normal_by_default):
    database = db.connect(tmp_path / "imagesrv.db")
    assert database.execute("PRAGMA synchronous").fetchone()[0] == FULL


def test_a_second_thread_gets_its_own_connection_at_full_too(tmp_path, normal_by_default):
    """uvicorn runs every plain ``def`` in a pool thread, each with its own connection (#54)."""
    database = db.connect(tmp_path / "imagesrv.db")
    seen: list[int] = []
    worker = threading.Thread(target=lambda: seen.append(database.execute("PRAGMA synchronous").fetchone()[0]))
    worker.start()
    worker.join(30)
    assert seen == [FULL]


def test_a_value_that_did_not_hold_stops_the_server(tmp_path, monkeypatch):
    """Principle 5: the read-back is the evidence. A connection that still says NORMAL
    after it was told FULL is an error, not a warning in a log nobody reads."""
    real = sqlite3.connect

    class Stuck:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.replace(" ", "").upper() == "PRAGMASYNCHRONOUS=FULL":
                return self._conn.execute("PRAGMA synchronous = NORMAL")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            if name == "_conn":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    monkeypatch.setattr(db.sqlite3, "connect", lambda *a, **k: Stuck(real(*a, **k)))
    with pytest.raises(RuntimeError, match="synchronous"):
        db.connect(tmp_path / "imagesrv.db").execute("SELECT 1")


def test_the_data_still_lands(tmp_path):
    """The guard: an ordinary database opens, writes and reads back as before."""
    database = db.connect(tmp_path / "imagesrv.db")
    database.execute("CREATE TABLE t (v TEXT)")
    database.execute("INSERT INTO t VALUES ('x')")
    database.commit()
    assert database.execute("SELECT v FROM t").fetchone()[0] == "x"
