"""#3707: `close_round` writes under `_write_lock`, like every other hot writer.

The round-close button ran three bare statements on the request thread's own
connection: the `active -> closing` claim, the `closing -> closed` write, and (when
anything went wrong in between) the `closing -> active` restore. None of them took
`_write_lock`. Another thread that is inside `with _write_lock, writing(conn)` --
the brain gates thread that `open_round` starts (#3064) writes `brain_decisions`
rows for as long as it runs -- holds sqlite's write lock, and sqlite does not
promise a waiting writer ever gets it (the fairness note on `db._write_lock`, #272).
The waiter sleeps in the busy handler until `busy_timeout` and then raises
`database is locked`.

It is worse than a 500: the restore is the third bare write, so the same collision
on the way out leaves the round in `closing` for good ("כבר נסגר או נמצא בתהליך
סגירה" on every later click). Seen on the Testrunner on 18/11
(`test_close_marks_closing_before_sender_stop_so_tick_opens_no_wave`: the
`closed` write at room.py:888, then the restore at :895, both `database is locked`).

The race is made deterministic instead of waited for: a thread stands in for the
gates thread, holds `_write_lock` *and* a real open write transaction on its own
connection, and lets go only once `close_round` is queued behind the Python lock
(fixed code) or has given up (old code). The three tests arm it before the claim,
before the `closed` write, and before the restore.
"""

from __future__ import annotations

import threading

import pytest
from conftest import HANG_S
from test_server_room import CLONER1, room_body, room_server   # noqa: F401 -- room_server is a fixture

from server import db as db_module
from server.brain import gates
from server.rounds import room as room_module
from server.rounds.sessions import SessionError

LABEL = "Office 2024 Standard"


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


class _Writer:
    """A stand-in for the gates thread caught in the middle of a write."""

    def __init__(self, ctx, monkeypatch) -> None:
        self.ctx, self.monkeypatch = ctx, monkeypatch
        self.queued = threading.Event()      # close_round reached for the Python lock
        self.release = threading.Event()     # the writer may let go: queued, or close_round is over
        self._held = threading.Event()
        self._thread: threading.Thread | None = None

    def arm(self) -> None:
        """Take the lock and an open write transaction, then wait for `close_round`."""
        self.monkeypatch.setattr(
            room_module, "_write_lock",
            _QueueProbe(db_module._write_lock, self.queued, self.release))

        def hold() -> None:
            with db_module._write_lock:
                # A real open write transaction on this thread's own connection.
                self.ctx.conn.execute("UPDATE room_rounds SET failed_reason = failed_reason")
                self._held.set()
                # Let go once close_round queues behind us (fixed code) or has given up
                # (old code, which never asks for the lock and so never queues).
                self.release.wait(HANG_S)
                self.ctx.conn.rollback()

        self._thread = threading.Thread(target=hold, name="stand-in-gates-writer", daemon=True)
        self._thread.start()
        assert self._held.wait(HANG_S), "the stand-in writer never took the lock"

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join(HANG_S)
            assert not self._thread.is_alive(), "the stand-in writer never let go"


def _close_on_its_own_thread(ctx) -> list[BaseException]:
    """`close_round` on a fresh thread, so it has a connection of its own, as on a request."""
    outcome: list[BaseException] = []

    def run() -> None:
        try:
            ctx.conn.execute("PRAGMA busy_timeout = 100")     # a short wait, not 5 s, on old code
            room_module.close_round(ctx, "labtech", LABEL)
        except BaseException as exc:                          # noqa: BLE001 -- the test reads it
            outcome.append(exc)

    thread = threading.Thread(target=run, name="close-round", daemon=True)
    thread.start()
    thread.join(HANG_S)
    assert not thread.is_alive(), "close_round never returned"
    return outcome


def _state(ctx, round_id: str) -> str:
    return ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (round_id,)).fetchone()["state"]


@pytest.fixture()
def opened(room_server):
    ctx = room_server["ctx"]
    body = room_body(room_server, 2, {CLONER1: ["S1"]})
    round_id = room_server["deploy"].post("/api/console/room", json=body).json()["id"]
    # The real gates thread is another writer; this test stands in for it, on purpose.
    assert gates.wait_idle(HANG_S), "the real gates thread is still writing"
    return ctx, round_id


@pytest.mark.parametrize("where", ["claim", "closed_write"])
def test_close_round_queues_behind_a_writer_instead_of_failing_with_locked(
    opened, monkeypatch, where,
):
    ctx, round_id = opened
    writer = _Writer(ctx, monkeypatch)

    if where == "claim":
        writer.arm()
    else:
        # The `closing -> closed` write comes after the sender is stopped.
        def stop(*_args):
            writer.arm()
            return None
        monkeypatch.setattr(ctx.sender, "stop", stop)

    outcome = _close_on_its_own_thread(ctx)
    writer.release.set()
    writer.join()

    assert outcome == [], f"close_round failed behind a writer: {outcome!r}"
    assert writer.queued.is_set(), "close_round never asked for the write lock"
    assert _state(ctx, round_id) == "closed"


def test_the_restore_after_a_failed_close_queues_too_and_the_round_is_not_left_closing(
    opened, monkeypatch,
):
    ctx, round_id = opened
    writer = _Writer(ctx, monkeypatch)

    def stop(*_args):
        writer.arm()
        return 4242          # udp-sender is still alive after the stop: close_round must undo `closing`

    monkeypatch.setattr(ctx.sender, "stop", stop)

    outcome = _close_on_its_own_thread(ctx)
    writer.release.set()
    writer.join()

    assert [type(exc) for exc in outcome] == [SessionError], (
        f"expected the sender SessionError, got {outcome!r}")
    assert writer.queued.is_set(), "the restore never asked for the write lock"
    assert _state(ctx, round_id) == "active", "the round was left in 'closing'"
