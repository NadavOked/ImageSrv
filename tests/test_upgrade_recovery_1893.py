"""#1893 -- an upgrade cut by a power loss is rolled back on the next boot, and
only what that run did is undone.

``tools/server-upgrade.sh`` keeps a durable marker (stage, the DB snapshot
version and whether the boot payload was saved -- of THIS run);
``tools/server-upgrade-recover.sh`` (unit ``imagesrv-upgrade-recover``, before
the server) reads it at boot. Behaviour, through the real scripts with stand-ins
for git/dpkg/systemctl/logger and a real DB:

* cut at ``fetch`` with a DB snapshot and a saved payload left by an OLDER run:
  nothing is restored -- the data written since that run stays (the marker
  says this run had no snapshot and saved no payload);
* cut at ``restart`` after the new server migrated the DB: the tree goes back,
  dpkg is configured, the DB and the payload of this run come back;
* a recovery step that fails says so in the console's status and keeps the
  marker -- "could not roll back" is not "rolled back";
* a successful upgrade leaves no marker, and while it ran the marker named the
  stage, the snapshot version and the saved payload.

**Negative control** (Testrunner 22/10): Codex's c8432aa7 recover script (its
/etc/systemd/system made overridable, nothing else) -- the ``fetch`` test fails
"the DB was rolled back to an older run"; the dpkg-failure test fails with no
status written at all; the restart test differs only in the spelling of the
checkout. server-upgrade.sh of 9001fc91: the marker test fails -- no marker.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    REPO, _fake_repo, _run_upgrade, _seed_db, _status, bindir, calls, fake_tool, posix, pytestmark,
    run_bash,
)

RECOVER = REPO / "tools" / "server-upgrade-recover.sh"


def _db(data: Path) -> sqlite3.Connection:
    return sqlite3.connect(data / "imagesrv.db")


def _version(path: Path) -> int:
    with sqlite3.connect(path) as c:
        return c.execute("PRAGMA user_version").fetchone()[0]


def _journal_has(data: Path, detail: str) -> bool:
    with _db(data) as c:
        return c.execute("SELECT 1 FROM journal WHERE detail = ?", (detail,)).fetchone() is not None


def _box(tmp_path: Path, bindir: Path, *, dpkg_fails: bool = False) -> tuple[Path, Path, Path]:
    """A data dir with a real DB and an older run's DB snapshot (one row behind),
    a boot dir whose payload differs from an older run's saved copy, a repo."""
    data = tmp_path / "data"
    _seed_db(data)
    from server.db import connect, journal
    snap = data / "imagesrv.db.pre-upgrade"
    with sqlite3.connect(data / "imagesrv.db") as live, sqlite3.connect(snap) as copy:
        live.backup(copy)
    conn = connect(data / "imagesrv.db")
    journal(conn, "test_marker", "written after the old snapshot")
    conn.close()
    boot = tmp_path / "boot"
    (boot / ".pre-upgrade").mkdir(parents=True)
    (boot / "initrd.img").write_text("current initrd\n", newline="\n")
    (boot / ".pre-upgrade" / "0").write_text("older initrd\n", newline="\n")
    (boot / ".pre-upgrade" / "list").write_text(f"0 {posix(boot / 'initrd.img')}\n", newline="\n")
    repo = _fake_repo(tmp_path, live_ok=True)
    (repo / ".git").mkdir()
    # #2026: the recovery reads the cut tag's units from git before going back.
    fake_tool(bindir, "git", 'case "$1" in ls-tree) echo deploy/imagesrv-server.service ;; esac')
    fake_tool(bindir, "dpkg", "exit 1" if dpkg_fails else "exit 0")
    fake_tool(bindir, "systemctl", "exit 0")
    fake_tool(bindir, "logger", "exit 0")
    (tmp_path / "units").mkdir()
    return data, boot, repo


def _marker(data: Path, repo: Path, boot: Path, stage: str, *, db_snap_ver: str = "",
            payload_saved: bool = False) -> None:
    (data / "server-upgrade.state").write_text(json.dumps({
        "repo": posix(repo), "previous": "v0.48.0", "tag": "v0.49.0", "stage": stage,
        "http_root": posix(boot), "db_snap_ver": db_snap_ver, "payload_saved": payload_saved}),
        encoding="utf-8")


def _recover(tmp_path: Path, bindir: Path, data: Path):
    return run_bash(f'bash "{posix(RECOVER)}"', tmp_path, bindir, env={
        "IMAGESRV_DATA_DIR": posix(data), "IMAGESRV_UNIT_DIR": posix(tmp_path / "units"),
        "PYTHONPATH": posix(REPO)})


def test_a_cut_at_fetch_restores_nothing_an_older_run_left(tmp_path, bindir):
    data, boot, repo = _box(tmp_path, bindir)
    _marker(data, repo, boot, "fetch")                    # no snapshot, no payload: this run's
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _journal_has(data, "written after the old snapshot"), "the DB was rolled back to an older run"
    assert (boot / "initrd.img").read_text() == "current initrd\n", "an older run's payload came back"
    seen = calls(tmp_path)
    assert not [c for c in seen if c.startswith(("git ", "dpkg "))], seen   # the tree never moved
    status = _status(data)
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert "נקטע" in status["error"] and "fetch" in status["error"]
    assert not (data / "server-upgrade.state").exists()


def test_a_cut_after_the_new_server_migrated_puts_back_this_runs_db_and_payload(tmp_path, bindir):
    data, boot, repo = _box(tmp_path, bindir)
    before = _version(data / "imagesrv.db.pre-upgrade")
    with _db(data) as c:
        c.execute(f"PRAGMA user_version = {before + 1}")            # the new server's migration
    _marker(data, repo, boot, "restart", db_snap_ver=str(before), payload_saved=True)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = calls(tmp_path)
    assert "dpkg --configure -a" in seen
    assert "git checkout -q --detach v0.48.0" in seen
    assert "systemctl daemon-reload" in seen
    assert _version(data / "imagesrv.db") == before
    assert (boot / "initrd.img").read_text() == "older initrd\n"
    assert not (boot / ".pre-upgrade").exists()
    status = _status(data)
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert "הוחזר באתחול ל-v0.48.0" in status["error"] and "מיגרציה" in status["error"]
    assert not (data / "server-upgrade.state").exists()


def test_a_recovery_that_fails_says_so_and_keeps_the_marker(tmp_path, bindir):
    data, boot, repo = _box(tmp_path, bindir, dpkg_fails=True)
    _marker(data, repo, boot, "packages", db_snap_ver="1")
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode != 0
    status = _status(data)
    assert status["state"] == "failed" and status["reason"] == "install", status
    assert "נדרשת התערבות" in status["error"] and "dpkg" in status["error"], status
    assert (data / "server-upgrade.state").exists()
    assert not [c for c in calls(tmp_path) if c.startswith("git ")]   # stopped where it failed


def test_no_marker_is_no_work(tmp_path, bindir):
    data, _boot, _repo = _box(tmp_path, bindir)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0 and calls(tmp_path) == []
    assert _journal_has(data, "written after the old snapshot")


def test_the_upgrade_marks_its_stages_and_leaves_no_marker_when_done(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    marker, seen_at_restart = data / "server-upgrade.state", tmp_path / "marker-at-restart"
    fake_tool(bindir, "git", 'case "$1" in describe) echo "$FAKE_TAG" ;; checkout) exit 0 ;; '
                             'rev-parse) echo "commit-of-$FAKE_TAG" ;; rev-list) echo "commit-of-$4" ;; esac')
    fake_tool(bindir, "systemctl",
              f'case "$1" in restart) [ "$2" != imagesrv-server ] || cp "{posix(marker)}" "{posix(seen_at_restart)}" ;; '
              'show) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;; esac')
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert not marker.exists()
    at_restart = json.loads(seen_at_restart.read_text(encoding="utf-8"))
    assert at_restart["stage"] == "restart" and at_restart["tag"] == "v0.49.0"
    assert at_restart["db_snap_ver"].isdigit() and at_restart["payload_saved"] is True, at_restart
    assert "systemctl enable imagesrv-upgrade-recover.service" in calls(tmp_path)   # with the units, after checkout (#1193)
    assert (tmp_path / "units" / "imagesrv-upgrade-recover.service").exists()
