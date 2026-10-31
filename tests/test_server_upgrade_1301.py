"""‏#1301 — the upgrade script writes a status the console screen can read.

The console's "server is updating" screen shows the four steps of
``tools/server-upgrade.sh`` (fetch · verify · install · restart), a clock that
survives a page reload, and a named failure: signature refused, download
failed, rolled back. It reads all of that from ``settings.update_status`` —
never from the Hebrew ``error`` text. Here the real bash script runs against
PATH shims (the #1193 harness) and the assertions are on the stored document.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH,
    _fake_repo,
    _seed_db,
    _status,
    fake_tool,
)
from test_server_upgrade_1193 import _git_shim, _run, _success_tools

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

TAG = "v0.53.0"
STARTED = "2026-01-01T00:00:00+00:00"   # in the past: stage stamps come after it


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _seed_running(data: Path, **doc) -> None:
    """What ``update._apply_in_background`` writes just before it launches the script."""
    from server import update
    from server.db import connect
    update._set_status(connect(data / "imagesrv.db"),
                       {"state": "running", "tag": TAG, "started_at": STARTED,
                        "by": "nadav", "from": "v0.48.0", **doc})


def _journal(data: Path) -> list[tuple[str, str, str]]:
    from server.db import connect
    rows = connect(data / "imagesrv.db").execute(
        "SELECT event, detail, user FROM journal WHERE event LIKE 'update_apply_%' ORDER BY id").fetchall()
    return [(r["event"], r["detail"], r["user"]) for r in rows]


def _setup(tmp_path: Path, *, live_ok: bool = True, previous: str | None = "v0.48.0"):
    data = tmp_path / "data"
    _seed_db(data, previous=previous)
    _seed_running(data)
    repo = _fake_repo(tmp_path, live_ok=live_ok)
    return data, repo


def _stage_order(status: dict) -> list[str]:
    stages = status["stages"]
    return sorted(stages, key=lambda k: (stages[k], ["fetch", "verify", "install", "payload", "restart", "done"].index(k)))


def test_a_successful_run_records_every_stage_with_a_timestamp_and_ends_done(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    _success_tools(bindir)

    proc = _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert status["state"] == "done" and status["stage"] == "done"
    # #1463: payload (the initrd build, ~90% of the run) is a stage of its own
    assert list(status["stages"]) == ["fetch", "verify", "install", "payload", "restart", "done"]
    assert _stage_order(status) == ["fetch", "verify", "install", "payload", "restart", "done"]
    assert all(ts.endswith("+00:00") and ts >= STARTED for ts in status["stages"].values())
    assert "reason" not in status and "error" not in status
    # the run update.py started: same clock, same operator, same "from"
    assert (status["started_at"], status["by"], status["from"]) == (STARTED, "nadav", "v0.48.0")
    assert _journal(data) == [("update_apply_done", f"{TAG}: done", "nadav")]


def test_fetch_failure_is_reason_fetch_at_stage_fetch(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    fake_tool(bindir, "git", _git_shim(fetch='echo "Could not resolve host: github.com" >&2; exit 128'))
    fake_tool(bindir, "systemctl", "exit 70")

    proc = _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert proc.returncode != 0
    assert (status["state"], status["reason"], status["stage"]) == ("failed", "fetch", "fetch")
    assert list(status["stages"]) == ["fetch"]
    assert "Could not resolve host" in status["detail"] and status["detail"] == status["error"]
    assert _journal(data)[-1][0] == "update_apply_failed"
    assert _journal(data)[-1][2] == "nadav"


@pytest.mark.parametrize("verifier", [
    'import sys; sys.stderr.write("unsigned: no signature\\n"); sys.exit(1)',
    'import sys; sys.stderr.write("unchecked: allowed-signers missing\\n"); sys.exit(2)',
])
def test_signature_refusal_is_reason_signature_at_stage_verify_and_never_checks_out(tmp_path, bindir, verifier):
    data, repo = _setup(tmp_path)
    (repo / "tools" / "verify-release-tag.py").write_text(verifier + "\n", encoding="utf-8", newline="\n")
    _success_tools(bindir)

    proc = _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert proc.returncode != 0
    assert (status["state"], status["reason"], status["stage"]) == ("failed", "signature", "verify")
    assert list(status["stages"]) == ["fetch", "verify"]
    assert "לא אומת כחתום" in status["detail"]
    log = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert " checkout " not in log


def test_checkout_failure_is_reason_install_without_a_rollback(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    fake_tool(bindir, "git", _git_shim(checkout='echo "fatal: reference is not a tree" >&2; exit 128'))
    fake_tool(bindir, "systemctl", "exit 70")

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert (status["reason"], status["stage"]) == ("install", "install")
    assert "הוחזר" not in status["error"]


def test_install_failure_that_rolled_back_is_reason_rollback_and_detail_is_the_cause_only(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    (repo / "tools" / "boot-payload-build.sh").write_text(
        '#!/usr/bin/env bash\nexit 1\n', encoding="utf-8", newline="\n")
    _success_tools(bindir)

    proc = _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert proc.returncode != 0
    assert (status["state"], status["reason"], status["stage"]) == ("failed", "rollback", "payload")
    assert status["detail"].startswith("בניית ה-initrd נכשלה")
    assert "הוחזר ל-v0.48.0" in status["error"] and "הוחזר" not in status["detail"]
    assert "restart" not in status["stages"]


def test_new_server_not_answering_in_time_rolls_back_at_stage_restart(tmp_path, bindir):
    data, repo = _setup(tmp_path, live_ok=False)
    _success_tools(bindir)

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert (status["reason"], status["stage"]) == ("rollback", "restart")
    assert "/health/live" in status["detail"]


def test_without_a_previous_tag_it_still_rolls_back_to_the_starting_commit(tmp_path, bindir):
    """‏#1546: בלי `update_previous` (הרצה ידנית) יעד החזרה הוא הקומיט שהעץ
    היה עליו — החזרה קרתה, ולכן ה-reason הוא rollback. כשל של החזרה עצמה
    שומר את הסיבה כ-reason (הטסט הבא)."""
    data, repo = _setup(tmp_path, live_ok=False, previous=None)
    _success_tools(bindir)

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert (status["reason"], status["stage"]) == ("rollback", "restart")
    assert "הוחזר ל-" in status["error"] and "לא הוחזר" not in status["error"]


def test_a_rollback_that_failed_too_keeps_the_cause_as_the_reason(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    (repo / "tools" / "boot-payload-build.sh").write_text(
        '#!/usr/bin/env bash\nexit 1\n', encoding="utf-8", newline="\n")
    fake_tool(bindir, "git", _git_shim(
        checkout='case "$*" in *v0.48.0*) echo "cannot lock ref" >&2; exit 128 ;; esac; exit 0'))
    fake_tool(bindir, "systemctl", "exit 0")
    fake_tool(bindir, "curl", "exit 0")

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert (status["reason"], status["stage"]) == ("install", "payload")
    assert "נכשלה גם היא" in status["error"]


def test_open_round_refusal_is_reason_round_before_any_stage(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, open_round=True)
    _seed_running(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _success_tools(bindir)

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert (status["state"], status["reason"]) == ("failed", "round")
    assert "stage" not in status and status["stages"] == {}


def test_a_manual_run_does_not_adopt_a_stale_status_of_another_tag(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    _seed_running(data, tag="v0.40.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    _success_tools(bindir)

    _run(tmp_path, bindir, repo, data, TAG)
    status = _status(data)

    assert status["state"] == "done"
    assert status["started_at"] != STARTED, "another tag's clock is not this run's clock"
    assert status["by"] is None and status["from"] == "v0.48.0"


def test_the_status_document_is_json_the_console_reads_as_is(tmp_path, bindir):
    data, repo = _setup(tmp_path)
    _success_tools(bindir)
    _run(tmp_path, bindir, repo, data, TAG)
    from server import update
    from server.db import connect, get_setting
    raw = get_setting(connect(data / "imagesrv.db"), update.STATUS_KEY)
    assert json.loads(raw) == _status(data)
