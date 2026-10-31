"""‏#1301 — what ``GET /api/console/update/status`` hands the update screen.

* ``apply`` starts the run with ``started_at``/``by``/``from`` (the script keeps
  them, ``test_server_upgrade_1301.py``) and the read adds the server's ``now``,
  so the clock on the screen is the server's, not the browser's.
* A ``done`` the script wrote itself is verified by the same comparison as
  ``running`` — the running version, read now, against the tag.
* The console session survives a server restart: the screen reloads the page
  after the update and must land in the console, not on a silent login screen.
  "Restart" here is what systemd does — a new process (a new ``create_app``)
  over the same data directory — and the old cookie is presented to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server import update as update_mod


def _hooks(state: dict) -> dict:
    return {"describe": lambda repo_dir: state["current"],
            "ls_remote_tags": lambda url: (True, "", ""),
            "verify_tag": lambda url, tag, repo_dir: {"state": "signed", "detail": "test"},
            "run_upgrade": lambda repo_dir, tag: (True, "")}


def _app(data: Path, images_root: Path, clock, state: dict):
    from server.app import create_app
    return create_app(data, images_root, "http://10.44.12.10:8080", now_fn=clock,
                      update_hooks=_hooks(state), repo_dir="/repo")


@pytest.fixture()
def server(tmp_path: Path, images_root: Path, clock):
    from server.console import users
    state = {"current": "v0.71.0"}
    app = _app(tmp_path / "data", images_root, clock, state)
    users.create(app.state.ctx.conn, "nadav", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "nadav", "password": "admin-pass-123"}).status_code == 200
    assert admin.post("/api/console/settings", json={"update_enabled": True}).status_code == 200
    return {"admin": admin, "app": app, "state": state, "data": tmp_path / "data",
            "images_root": images_root, "clock": clock}


def test_apply_starts_the_run_with_its_clock_its_operator_and_the_version_it_leaves(server):
    admin = server["admin"]
    assert admin.post("/api/console/update/apply", json={"tag": "v0.72.0"}).status_code == 200
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "running" and status["verified"] is False
    assert status["by"] == "nadav" and status["from"] == "v0.71.0"
    assert status["started_at"].endswith("+00:00")
    assert status["now"].endswith("+00:00") and status["now"] >= status["started_at"]


def test_status_carries_the_script_fields_as_written(server):
    conn = server["app"].state.ctx.conn
    doc = {"state": "failed", "tag": "v0.72.0", "stage": "verify", "reason": "signature",
           "detail": "התג v0.72.0 לא אומת כחתום", "error": "התג v0.72.0 לא אומת כחתום",
           "started_at": "2026-10-13T10:00:00+00:00",
           "stages": {"fetch": "2026-10-13T10:00:01+00:00", "verify": "2026-10-13T10:00:19+00:00"},
           "from": "v0.71.0", "by": "nadav"}
    update_mod._set_status(conn, doc)
    status = server["admin"].get("/api/console/update/status").json()
    assert {k: status[k] for k in doc} == doc
    assert "verified" not in status, "a failure is not 'not verified yet'"


@pytest.mark.parametrize("current, verified", [("v0.72.0", True), ("v0.71.0", False)])
def test_a_done_written_by_the_script_is_verified_against_the_running_version(server, current, verified):
    update_mod._set_status(server["app"].state.ctx.conn,
                           {"state": "done", "stage": "done", "tag": "v0.72.0"})
    server["state"]["current"] = current
    status = server["admin"].get("/api/console/update/status").json()
    assert status["state"] == "done" and status["verified"] is verified


def test_running_at_stage_restart_keeps_the_old_reading_and_the_stage_says_the_rest(server):
    """The server answering with the tag still reads ``done`` (unchanged since #748, so a
    script that died after the restart never blocks the next update); the stage tells
    the screen the script has not finished verifying."""
    update_mod._set_status(server["app"].state.ctx.conn,
                           {"state": "running", "stage": "restart", "tag": "v0.72.0"})
    server["state"]["current"] = "v0.72.0"
    status = server["admin"].get("/api/console/update/status").json()
    assert (status["state"], status["verified"], status["stage"]) == ("done", True, "restart")


def test_the_console_session_survives_a_server_restart(server):
    """Same data dir, new process: the signed cookie is still a session (auth.py keeps no
    in-memory sessions). This is why the screen reloads into the console after an update."""
    cookie = server["admin"].cookies.get("imagesrv_session")
    assert cookie
    server["state"]["current"] = "v0.72.0"
    restarted = _app(server["data"], server["images_root"], server["clock"], server["state"])
    after = TestClient(restarted)
    after.cookies.set("imagesrv_session", cookie)
    me = after.get("/api/console/me")
    assert me.status_code == 200 and me.json()["username"] == "nadav"
    assert me.json()["version"] == "v0.72.0"
    assert after.get("/api/console/update/status").status_code == 200
    stranger = TestClient(restarted)
    assert stranger.get("/api/console/update/status").status_code == 401, "and without it — no session"


def test_health_live_answers_without_a_session_with_the_running_version(server):
    """The screen polls this while the server restarts; it must not need the cookie."""
    anonymous = TestClient(server["app"])
    body = anonymous.get("/api/console/health/live").json()
    assert body == {"ok": True, "version": "v0.71.0", "version_error": None}
