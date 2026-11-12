"""#2905 -- a `running` task whose machine stopped reporting closes `failed`, by name.

Found by the 11/11 state-machine scan, confirmed against the code (not on metal):
the only ways out of `tasks.state = 'running'` were the agent's own report, the
manifest, an operator's cancel, the UPS and a server restart. Nothing measured
silence. Two cases stayed `running` for ever:

1. A capture the build machine walked away from (powered off, booted Windows).
   `busy_db.reason` kept saying "a capture is running", so the daily backup,
   the scheduled reboot and "apply update" all waited -- and with the reboot
   blocked, `reconcile_on_start` never came either. `order_capture` answered
   409 for that machine.
2. The source task of a direct round whose wave already ended. `_finish_wave`
   closes the round and *deliberately* leaves the task to the source's own final
   report (`test_direct_server`: a wave that ended is no evidence the source
   finished). A source that never reports again kept it `running`, and every new
   task for that build machine was refused.

The rule now (mirrors `pulls.SILENT_SECONDS`, #1573): a `running` task with no
report and no new upload bytes for `SILENT_SECONDS` becomes `failed` with the
reason in `tasks.error`. What must NOT close: a task that still reports, an
upload still writing bytes, a `pending` task (an operator may order a capture
from a machine that is off), and a live task across a wall-clock step (#1947).
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from server.core.tasks import TOKEN_HEADER, active_task, staging_dir  # noqa: E402
from server.system import busy_db, clockstep  # noqa: E402
from test_capture import PART_A, make_task, setup_build_machine, task_token  # noqa: E402
from test_direct_server import (  # noqa: E402,F401 -- room_server is a fixture
    BUILD, _build_hello, _run_wave, _token, room_server,
)
from test_server_room import CLONER1, cloner_hello, report, room  # noqa: E402

try:                                   # negative control: without the module the
    from server.system import silent_tasks  # tests run and fail on behaviour,
except ImportError:                    # not on an ImportError
    silent_tasks = None

SILENT = getattr(silent_tasks, "SILENT_SECONDS", 600)
QUIET = SILENT + 120                   # the machine went quiet this long ago


def _sweep(ctx, now=None, up_for=SILENT + 60) -> None:
    """One pass, from a server that has been answering for `up_for` seconds."""
    if silent_tasks is not None:
        silent_tasks.sweep(ctx.conn, ctx.store, ctx.library.root,
                           clockstep._mono() - up_for, now=now)


def _task(ctx, task_id):
    return ctx.conn.execute("SELECT state, error FROM tasks WHERE id = ?", (task_id,)).fetchone()


def _events(ctx, event):
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY rowid", (event,))]


def _running_capture(server) -> str:
    """A capture the agent took: one partition landed, so the task is `running`."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    head = {TOKEN_HEADER: task_token(server, created["id"])}
    client = TestClient(server["app"], raise_server_exceptions=False)
    assert client.put(f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
                      content=PART_A, headers=head).status_code == 200
    assert _task(server["ctx"], created["id"])["state"] == "running"
    return created["id"]


def _went_quiet(ctx, task_id, seconds=QUIET, *, files=True) -> None:
    """The last report -- and, with `files`, the last upload byte -- `seconds` ago."""
    then = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ctx.conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?",
                     (then.isoformat(timespec="seconds"), task_id))
    ctx.conn.commit()
    folder = staging_dir(ctx.library.root, task_id)
    if files and folder.is_dir():
        for path in (folder, *folder.iterdir()):
            os.utime(path, (then.timestamp(), then.timestamp()))


# --- 1. a capture the machine walked away from ---------------------------------------


def test_a_capture_whose_machine_went_quiet_fails_by_name_and_frees_the_server(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    assert busy_db.reason(ctx.conn) == "קליטת אימג' רצה"
    _went_quiet(ctx, task_id)

    _sweep(ctx)

    row = _task(ctx, task_id)
    assert row["state"] == "failed", "a capture nobody reports on stays running for ever"
    assert "הפסיקה לדווח" in (row["error"] or "")
    assert busy_db.reason(ctx.conn) is None, "backup / scheduled reboot / update still blocked"
    assert not staging_dir(ctx.library.root, task_id).exists()
    assert any(task_id in d and "הפסיקה לדווח" in d for d in _events(ctx, "capture_failed"))
    # the same machine can be ordered again -- no 409 "already has an open task"
    assert make_task(server, "aa:bb:cc:00:00:10").status_code == 200


def test_a_capture_that_still_reports_is_not_touched(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    # ... and then a report arrives: a live agent, a few seconds ago
    r = server["anon"].post("/api/v1/agent/progress", json={
        "task_id": task_id, "mac": "aa:bb:cc:00:00:10", "state": "capturing",
        "targets": [{"dev": "sda", "bytes_written": 10, "bytes_total": 100, "state": "reading"}]},
        headers={TOKEN_HEADER: task_token(server, task_id)})
    assert r.status_code == 200, r.json()

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "running"
    assert busy_db.reason(ctx.conn) == "קליטת אימג' רצה"


def test_an_upload_still_writing_bytes_is_not_silence(server):
    """No report for a while, but the partition file is still growing: alive."""
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id, files=False)       # the upload's mtime stays "now"

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "running"
    assert staging_dir(ctx.library.root, task_id).is_dir()


def test_a_pending_capture_waits_for_its_machine(server):
    """Ordered for a machine that is off: it waits -- that is not silence."""
    ctx = server["ctx"]
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    _went_quiet(ctx, task_id, seconds=24 * 3600)

    _sweep(ctx)

    assert _task(ctx, task_id)["state"] == "pending"


def test_a_clock_step_does_not_fail_a_live_capture(server, monkeypatch):
    """#1947 (QA-G16): the wall clock jumps +3 h while the agent keeps reporting."""
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id, seconds=2, files=True)   # a report two seconds ago
    mono = time.monotonic()
    monkeypatch.setattr(clockstep, "_mono", lambda: mono)   # only the wall clock moves
    now = datetime.now(timezone.utc)
    clockstep.guard(ctx.conn, ctx.store, now)               # the sample before the step

    _sweep(ctx, now=now + timedelta(hours=3))

    assert _task(ctx, task_id)["state"] == "running"


def test_a_server_that_just_came_up_does_not_read_its_own_silence_as_the_machines(server):
    """Down for an hour (update, reboot): nobody could report. A live agent reports
    again seconds after the server answers -- so nothing closes for `SILENT` after it
    comes up, and only then does a task that still says nothing close."""
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id, seconds=3600)

    _sweep(ctx, up_for=5)                       # the first pass after the server came up
    assert _task(ctx, task_id)["state"] == "running", "failed for the server's own silence"

    _sweep(ctx, up_for=SILENT + 5)              # up for SILENT, and still not a word
    assert _task(ctx, task_id)["state"] == "failed"


# --- 2. the source of a direct round that already closed ---------------------------


def test_the_source_task_of_a_closed_direct_round_fails_once_the_source_is_quiet(room_server):
    ctx, anon, admin = room_server["ctx"], room_server["anon"], room_server["admin"]
    opened = _run_wave(room_server)
    report(anon, opened["wave_session_id"], CLONER1, {"sda": "done", "sdb": "done"})
    cloner_hello(anon, CLONER1, ["S1", "S2"])            # the pulse that ends the wave
    assert room(admin)["round"] is None
    assert ctx.conn.execute("SELECT state FROM room_rounds WHERE id = ?",
                            (opened["id"],)).fetchone()["state"] == "closed"
    # by design the wave's end does not close the source task (test_direct_server)
    assert _task(ctx, opened["task_id"])["state"] == "running"

    _sweep(ctx)                                         # the source is still fresh
    assert _task(ctx, opened["task_id"])["state"] == "running"

    _went_quiet(ctx, opened["task_id"])                 # ... and never reports again
    _sweep(ctx)

    row = _task(ctx, opened["task_id"])
    assert row["state"] == "failed", "a closed round's source task stays running for ever"
    assert "הפסיקה לדווח" in (row["error"] or "")
    assert active_task(ctx.conn, BUILD) is None, "every new task for the build machine refused"
    assert any(opened["task_id"] in d for d in _events(ctx, "direct_failed"))


def test_a_late_done_from_the_source_still_closes_it_done(room_server):
    """The guard of the design: inside the window the source's report wins."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    opened = _run_wave(room_server)
    report(anon, opened["wave_session_id"], CLONER1, {"sda": "done", "sdb": "done"})
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    _sweep(ctx)
    assert anon.post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 4096, "bytes_total": 4096, "state": "done"}]},
        headers={TOKEN_HEADER: _token(room_server, opened["task_id"])}).status_code == 200
    assert _task(ctx, opened["task_id"])["state"] == "done"
    assert _build_hello(anon)["task"] is None


# --- the wiring: the room clock runs it, with nobody watching ------------------------


def test_the_room_clock_closes_a_silent_task_in_the_background(tmp_path, monkeypatch):
    """A sweep that exists but is not called looks exactly like one that works."""
    from conftest import Clock, write_image, MANIFEST_256
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    if silent_tasks is not None:                # the wait after startup, shortened
        monkeypatch.setattr(silent_tasks, "SILENT_SECONDS", 1)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                     now_fn=Clock(), room_clock_interval=0.02)
    ctx = app.state.ctx
    # inserted after startup: `reconcile_on_start` already ran and is not what closes it
    then = (datetime.now(timezone.utc) - timedelta(seconds=QUIET)).isoformat(timespec="seconds")
    ctx.conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, created_by,"
        " created_at, updated_at, state, token)"
        " VALUES ('tsk_gone', 'aa:bb:cc:00:00:10', 'capture', 'sda', 'img_gone', 'קליטה',"
        " 'noc', ?, ?, 'running', 'tok')", (then, then))
    ctx.conn.commit()
    closed = threading.Event()
    try:
        with TestClient(app):                 # __enter__ runs the lifespan (the clock)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if ctx.conn.execute("SELECT state FROM tasks WHERE id = 'tsk_gone'"
                                    ).fetchone()["state"] == "failed":
                    closed.set()
                    break
                time.sleep(0.1)
    finally:
        ctx.sender.stop()
    assert closed.is_set(), "the room clock never closed the silent capture"


# --- 3. (#2904's case) the server died while a direct round was `closing` ------------


def test_a_direct_source_left_running_across_a_restart_fails_once_quiet(tmp_path, monkeypatch):
    """#2904 closes a `closing` round at startup and does not touch the source task;
    `reconcile_on_start` never did (it reconciles captures only). Whatever the round's
    state, the source task is closed here -- by silence, not by the round."""
    from test_direct_server import _put_manifest, _setup
    from test_reconcile_on_start import _boot

    first = _boot(tmp_path, monkeypatch, seed=True)
    first["woken"] = []
    opened = _setup(first)
    assert _put_manifest(first, opened["task_id"]).status_code == 200
    assert _task(first["ctx"], opened["task_id"])["state"] == "running"
    first["ctx"].conn.execute("UPDATE room_rounds SET state = 'closing' WHERE id = ?",
                              (opened["id"],))     # the server dies mid "stop round"
    first["ctx"].conn.commit()
    first["ctx"].sender.stop()

    second = _boot(tmp_path, monkeypatch, seed=False)
    ctx = second["ctx"]
    assert _task(ctx, opened["task_id"])["state"] == "running", "reconcile left it open"
    _went_quiet(ctx, opened["task_id"])          # the build machine never comes back
    _sweep(ctx)

    row = _task(ctx, opened["task_id"])
    assert row["state"] == "failed", "the source task outlived its round across a restart"
    assert "הפסיקה לדווח" in (row["error"] or "")
    assert active_task(ctx.conn, BUILD) is None
    ctx.sender.stop()
