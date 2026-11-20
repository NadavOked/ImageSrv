"""#2985 — הפצה ישירה שנסגרה אינה נשארת "פעילה" בקונסולה.

הקונסולה מציגה "פעיל" לפי שלוש שורות: משימת המקור, הגל (`/overview.session`) והסבב
(`/overview.room`). כל נתיב סגירה חייב לסגור את שלושתן, ובשם. הנתיבים: ביטול מפעיל (כבר
תקין, שומר), כשל המקור (בלי המניפסט ואחריו), מקור שנעלם אחרי שהסבב נסגר, וסבב שנכשל בנתיב
שלא עצר את המקור. ושני שומרים נגד תיקון-יתר: מקור ש`done` והגל עוד רץ, ומקור שעוד בתוך
חלון הדיווח.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from test_direct_server import BUILD, _build_hello, _put_manifest, _setup, _token
from test_server_room import (  # noqa: F401 — room_server הוא fixture
    CLONER1, cloner_hello, heartbeat, report, room, room_server,
)

from server.core.tasks import TOKEN_HEADER
from server.rounds import direct_settle, room as room_module

CLONERS = ["S1", "S2"]
ACTIVE_TASK = ("pending", "running", "verifying")


def _progress(room_server, opened, state):
    head = {TOKEN_HEADER: _token(room_server, opened["task_id"])}
    return room_server["anon"].post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": state,
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 4096, "state": "waiting"}],
    }, headers=head)


def _running_wave(room_server):
    opened = _setup(room_server)
    cloner_hello(room_server["anon"], CLONER1, CLONERS)
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(room_server["anon"], CLONER1, CLONERS)
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    return opened


def _console_active(room_server) -> dict:
    """מה שהקונסולה מחשיבה "פעיל": הגל, הסבב והמשימות — מה-API שלה, לא מה-DB."""
    admin = room_server["admin"]
    overview = admin.get("/api/console/overview").json()
    tasks = admin.get("/api/console/tasks").json()
    return {
        "session": (overview["session"] or {}).get("state") in ("open", "running"),
        "room": (overview["room"] or {}).get("state") == "active",
        "tasks": [t["id"] for t in tasks if t["state"] in ACTIVE_TASK],
    }


def _nothing_active(room_server):
    assert _console_active(room_server) == {"session": False, "room": False, "tasks": []}


def _task(ctx, task_id):
    return ctx.conn.execute("SELECT state, error FROM tasks WHERE id = ?", (task_id,)).fetchone()


def _round(ctx, round_id):
    return ctx.conn.execute(
        "SELECT state, failed_reason FROM room_rounds WHERE id = ?", (round_id,)).fetchone()


def _later(monkeypatch, seconds):
    real = room_module._utcnow
    monkeypatch.setattr(room_module, "_utcnow", lambda: real() + timedelta(seconds=seconds))


def test_an_operator_stop_closes_task_wave_and_round(room_server):
    opened = _running_wave(room_server)
    assert room_server["admin"].post(
        "/api/console/room/close", json={"confirm_name": "build-01:sda"}).status_code == 200
    _nothing_active(room_server)
    assert _task(room_server["ctx"], opened["task_id"])["state"] == "cancelled"


def test_a_source_failure_mid_wave_fails_wave_and_round_by_name(room_server):
    opened = _running_wave(room_server)
    ctx = room_server["ctx"]
    assert _progress(room_server, opened, "failed").status_code == 200
    heartbeat(room_server)
    _nothing_active(room_server)
    rnd = _round(ctx, opened["id"])
    assert rnd["state"] == "failed" and rnd["failed_reason"], "הסבב נשאר active אחרי שהמקור נכשל"
    assert _task(ctx, opened["task_id"])["state"] == "failed"
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?",
                            (opened["wave_session_id"],)).fetchone()["state"] == "failed"


def test_a_source_failure_before_the_manifest_fails_wave_and_round_by_name(room_server):
    opened = _setup(room_server)
    ctx = room_server["ctx"]
    cloner_hello(room_server["anon"], CLONER1, CLONERS)
    assert _progress(room_server, opened, "failed").status_code == 200
    heartbeat(room_server)
    _nothing_active(room_server)
    assert _round(ctx, opened["id"])["state"] == "failed"


def test_the_source_error_is_the_reason_when_it_gave_one(room_server):
    opened = _running_wave(room_server)
    ctx = room_server["ctx"]
    head = {TOKEN_HEADER: _token(room_server, opened["task_id"])}
    room_server["anon"].post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "failed",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 4096,
                     "state": "failed", "error": "קריאה מהדיסק נכשלה"}]}, headers=head)
    heartbeat(room_server)
    assert "קריאה מהדיסק נכשלה" in _round(ctx, opened["id"])["failed_reason"]


def test_a_source_that_vanished_after_the_round_closed_is_closed_by_name(room_server, monkeypatch):
    opened = _running_wave(room_server)
    ctx = room_server["ctx"]
    report(room_server["anon"], opened["wave_session_id"], CLONER1, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)
    assert _round(ctx, opened["id"])["state"] == "closed"
    # בתוך חלון הדיווח של המקור: עוד לא נוגעים (שומר נגד תיקון-יתר)
    assert _task(ctx, opened["task_id"])["state"] == "running"
    _later(monkeypatch, direct_settle.SOURCE_SILENT_S + 5)
    heartbeat(room_server)
    task = _task(ctx, opened["task_id"])
    assert task["state"] == "failed" and direct_settle.SOURCE_SILENT in task["error"]
    _nothing_active(room_server)
    assert "direct_source_closed" in [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]


def test_a_failed_round_stops_its_source_task_even_by_a_path_that_forgot_to_(room_server, monkeypatch):
    """כשל הכנת האינדקס (#3137) מכשיל את הסבב דרך `_fail_round` בלי `cancel_source_task`."""
    opened = _setup(room_server)
    ctx = room_server["ctx"]
    round_row = ctx.conn.execute("SELECT * FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()
    assert room_module._fail_round(ctx.conn, ctx.store, round_row, "אינדקס נכשל")
    heartbeat(room_server)
    # המקור לומד שהגל נכשל מהמשימה שלו, ומדווח בעצמו: בתוך החלון המשימה נשארת (שומר)
    assert _task(ctx, opened["task_id"])["state"] == "pending"
    assert _build_hello(room_server["anon"])["task"]["direct"]["session_state"] == "closed"
    _later(monkeypatch, direct_settle.SOURCE_SILENT_S + 5)
    heartbeat(room_server)
    task = _task(ctx, opened["task_id"])
    assert task["state"] == "cancelled" and direct_settle.ROUND_FAILED in task["error"]
    _nothing_active(room_server)
    assert _build_hello(room_server["anon"])["task"] is None


def test_a_source_that_finished_while_the_wave_still_runs_does_not_end_the_round(room_server):
    opened = _running_wave(room_server)
    assert _progress(room_server, opened, "done").status_code == 200
    heartbeat(room_server)
    assert _round(room_server["ctx"], opened["id"])["state"] == "active"
    assert _task(room_server["ctx"], opened["task_id"])["state"] == "done"
    assert _console_active(room_server)["session"] and _console_active(room_server)["room"]


def test_a_source_failing_after_a_receiver_finished_leaves_the_round_to_close_normally(room_server):
    opened = _running_wave(room_server)
    ctx = room_server["ctx"]
    report(room_server["anon"], opened["wave_session_id"], CLONER1, {"sda": "done", "sdb": "done"})
    assert _progress(room_server, opened, "failed").status_code == 200
    heartbeat(room_server)
    rnd = _round(ctx, opened["id"])
    assert rnd["state"] == "closed" and rnd["failed_reason"] is None
