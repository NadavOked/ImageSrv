"""#1893 -- `running` that nobody runs any more is `failed` when the server starts.

``update.py`` writes ``state: running`` before ``server-upgrade.sh`` starts; the
recovery marker of ``imagesrv-upgrade-recover`` is written only at ``stage fetch``.
Power lost in between left ``running`` with no marker: the console said "running"
forever and ``apply`` answered 409 (ESXi scan, 11/11).

Written only on positive evidence: ``running`` the running version does not
confirm, no marker, and the unit ``inactive``/``failed``. A marker, a live unit,
an unread unit state, or the tag already running are all left alone.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import update
from server.db import connect

TAG = "v0.99.0"


def _box(tmp_path, status, *, unit="inactive", marker=False, current="v0.98.0", unit_raises=False):
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    if status is not None:
        update._set_status(conn, status)
    if marker:
        (data / "server-upgrade.state").write_text("{}", encoding="utf-8")

    def unit_state():
        if unit_raises:
            raise OSError("no systemctl")
        return unit

    hooks = {"describe": lambda repo: current, "upgrade_unit_state": unit_state}
    return conn, hooks, data


def _run(conn, hooks, data):
    return update.reconcile_interrupted(conn, hooks, "/repo", data)


RUNNING = {"state": "running", "tag": TAG, "started_at": "2026-11-11T10:00:00+00:00",
           "by": "nadav", "from": "v0.98.0"}


def test_running_with_no_marker_and_no_unit_becomes_failed_in_its_own_words(tmp_path):
    conn, hooks, data = _box(tmp_path, RUNNING)
    why = _run(conn, hooks, data)
    status = update.get_status(conn)
    assert status["state"] == "failed" and status["reason"] == "interrupted", status
    assert status["tag"] == TAG and status["from"] == "v0.98.0" and status["error"] == why
    assert "לפני שהתחיל לשנות דבר" in why
    # the next apply is no longer refused as "already running"
    assert update.running_verdict(status, "v0.98.0") is None


def test_a_status_that_reached_a_later_stage_says_the_marker_is_missing(tmp_path):
    conn, hooks, data = _box(tmp_path, {**RUNNING, "stage": "install"}, unit="failed")
    why = _run(conn, hooks, data)
    assert "install" in why and "סמן השחזור חסר" in why and "לפני שהתחיל" not in why
    assert update.get_status(conn)["state"] == "failed"


@pytest.mark.parametrize("what", ["marker", "active", "activating", "unread", "unknown_word",
                                  "raises", "already_on_the_tag"])
def test_anything_short_of_positive_evidence_leaves_running_alone(tmp_path, what):
    kw = {"marker": {"marker": True}, "active": {"unit": "active"},
          "activating": {"unit": "activating"}, "unread": {"unit": None},
          "unknown_word": {"unit": "maintenance"}, "raises": {"unit_raises": True},
          "already_on_the_tag": {"current": TAG}}[what]
    status = {**RUNNING, "stage": "restart"} if what == "already_on_the_tag" else RUNNING
    conn, hooks, data = _box(tmp_path, status, **kw)
    assert _run(conn, hooks, data) is None
    assert update.get_status(conn) == status


@pytest.mark.parametrize("status", [None, {"state": "idle"}, {"state": "failed", "tag": TAG},
                                    {"state": "done", "tag": TAG}])
def test_only_running_is_touched(tmp_path, status):
    conn, hooks, data = _box(tmp_path, status)
    assert _run(conn, hooks, data) is None
    assert update.get_status(conn) == status


def test_the_write_is_journaled(tmp_path):
    conn, hooks, data = _box(tmp_path, RUNNING)
    _run(conn, hooks, data)
    rows = conn.execute("SELECT event, detail FROM journal WHERE event = 'update_apply_failed'").fetchall()
    assert len(rows) == 1 and "interrupted" in rows[0][1], rows


def test_the_journal_row_reads_in_hebrew_on_the_console(tmp_path):
    """The journal detail is English ("<tag>: interrupted — <why>"); the console's rule must turn the marker into Hebrew."""
    from server.console.journal_he_detail import he_detail
    conn, hooks, data = _box(tmp_path, RUNNING)
    _run(conn, hooks, data)
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'update_apply_failed'").fetchone()[0]
    shown = he_detail("update_apply_failed", detail)
    assert "interrupted" not in shown and f"{TAG}: נקטע — " in shown, shown


def test_the_default_unit_probe_reads_inactive_from_a_nonzero_exit(monkeypatch):
    """`systemctl is-active` prints `inactive` and exits 3: the output counts, not the code."""
    monkeypatch.setattr(update, "_run", lambda cmd, timeout, cwd=None: (False, "inactive\n", ""))
    assert update._upgrade_unit_state() == "inactive"
    monkeypatch.setattr(update, "_run", lambda cmd, timeout, cwd=None: (False, "", "systemctl: not installed"))
    assert update._upgrade_unit_state() is None   # not read = not "not running"


def test_server_start_runs_the_reconcile(tmp_path, images_root, clock):
    """The lifespan calls it: a `running` left by a cut, over a fresh data dir, is `failed` after start."""
    from fastapi.testclient import TestClient
    from server.app import create_app
    data = tmp_path / "data"
    data.mkdir()
    seed = connect(data / "imagesrv.db")
    update._set_status(seed, RUNNING)
    app = create_app(data, images_root, "http://10.44.12.10:7712", now_fn=clock,
                     update_hooks={"describe": lambda repo: "v0.98.0",
                                   "upgrade_unit_state": lambda: "inactive"}, repo_dir="/repo")
    with TestClient(app):
        status = json.loads(app.state.ctx.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (update.STATUS_KEY,)).fetchone()[0])
    assert status["state"] == "failed" and status["reason"] == "interrupted", status
