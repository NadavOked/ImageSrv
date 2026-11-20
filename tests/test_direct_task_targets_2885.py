"""#2885 -- after a direct round the source disk's own state is final, not `waiting`.

tsk_2976 (work server, 11/11): `state=done`, every partition done, and the only entry in
`targets_json` -- the source `sda` -- still `"state": "waiting"`. `target_init` resets it right
before `sending` (both engines: directsend.sh, directchunk.sh) and the success path wrote only the
top word. The agent now closes the source target with the task (`direct_send_task`), and the server
closes any target still open when a direct task reaches its final state, for an agent that did not.
"""

from __future__ import annotations

import json

from test_directsend import MAC, _progress, direct_run

#: the wrapper alone: the send itself and everything around it that talks to a server or a screen
#: are stubbed -- `direct_send_run` / `direct_chunk_run` end the same way, in `direct_send_task`.
_WRAP = ("send_hello; progress_loop() { :; }; report_final() { :; }; build_end_ask() { :; }; "
         "shrink_restore_source() { :; }; capture_unhib_note() { :; }; ui_error_hold() { :; }; "
         "hold_unheard() { :; }; ")


def _sent(rc: int) -> str:
    fail = '_direct_failed "$2" "x"; ' if rc else ""
    return (f'direct_send_run() {{ target_init "$2" 4096; echo sending > "$RUN_DIR/state"; {fail}'
            f'return {rc}; }}; ')


def test_a_direct_send_that_succeeded_closes_the_source_target(tmp_path):
    box, run, out = direct_run(tmp_path, pre=_WRAP + _sent(0), call="direct_send_task")
    assert out.strip().endswith("rc=0"), out + (box / "direct.out").read_text("utf-8", "replace")
    report = _progress(box)
    assert report["state"] == "done", report
    assert [t["state"] for t in report["targets"]] == ["done"], report["targets"]


def test_a_direct_send_that_failed_keeps_the_source_failed(tmp_path):
    box, run, out = direct_run(tmp_path, pre=_WRAP + _sent(1), call="direct_send_task")
    assert out.strip().endswith("rc=1"), out
    report = _progress(box)
    assert report["state"] == "failed" and [t["state"] for t in report["targets"]] == ["failed"], report


# --- the server: one place, for an agent that did not close it ---------------------------


def _task(server, state="running", task="tsk_2885"):
    server["ctx"].conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by, created_at, updated_at, token)"
        " VALUES (?, ?, 'direct_send', 'sda', 'live_1', 'n', ?, 'admin', 'x', 'x', ?)",
        (task, MAC, state, "t" * 48))
    server["ctx"].conn.commit()


def _report(server, state, target_state, task="tsk_2885"):
    from server.agent import reports
    got = reports.ingest(server["ctx"].conn, {
        "task_id": task, "mac": MAC, "state": state,
        "targets": [{"dev": "sda", "state": target_state, "bytes_written": 10, "bytes_total": 10}]}, "t" * 48)
    assert got.get("ok") is True, got
    row = server["ctx"].conn.execute("SELECT state, targets_json FROM tasks WHERE id = ?", (task,)).fetchone()
    return row["state"], [t["state"] for t in json.loads(row["targets_json"])]


def test_the_server_closes_a_waiting_source_when_the_direct_task_is_done(server):
    _task(server)
    assert _report(server, "done", "waiting") == ("done", ["done"])


def test_the_server_closes_a_waiting_source_as_failed_when_the_direct_task_failed(server):
    _task(server)
    assert _report(server, "failed", "sending") == ("failed", ["failed"])


def test_a_running_report_leaves_the_source_as_the_agent_said(server):
    _task(server)
    assert _report(server, "sending", "waiting") == ("running", ["waiting"])


def test_tasks_carries_the_round_of_a_direct_send(server):
    _task(server, state="done")
    server["ctx"].conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, written_drives, skipped_serials, state, failed_reason,"
        " opened_by, created_at, source_kind, source_task_id) VALUES ('room_2885', 'live_1', 3, 0, '[\"S9\"]', 'failed',"
        " 'המשכפל הפסיק לדווח', 'admin', 'x', 'build_disk', 'tsk_2885')")
    server["ctx"].conn.commit()
    (row,) = [t for t in server["admin"].get("/api/console/tasks").json() if t["id"] == "tsk_2885"]
    assert row["round"] == {"id": "room_2885", "state": "failed", "target_drives": 3, "written_drives": 0,
                            "skipped_drives": 1, "failed_reason": "המשכפל הפסיק לדווח"}, row
