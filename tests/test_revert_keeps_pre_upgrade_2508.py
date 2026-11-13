"""#2508: "back to the previous version" overwrote imagesrv.db.pre-upgrade.

/update/revert runs the same tools/server-upgrade.sh, which copied the live DB
over ``imagesrv.db.pre-upgrade`` before the checkout. After an update that
migrated the DB, the old code refuses it (``db.SchemaError``) and its message
tells the operator to restore ``imagesrv.db.pre-upgrade`` -- the one manual way
back, already overwritten with the migrated DB by the revert itself.

The rule now: an existing copy is kept when the tag's code cannot open the
current DB (its ``SCHEMA_VERSION`` is below ``user_version``). The name stays,
because the message that points at it lives in the OLD code. Read from git;
"could not read" stops the run like a failed copy does (principle 5).

The script runs for real against stubs (git, systemctl, the health probe), as
in test_installer_1131; it was NOT run on a VM.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from server import db
from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    REPO, UPGRADE, _fake_repo, _run_upgrade, _seed_db, _status, _upgrade_env, _upgrade_fakes,
    bindir, calls, fake_tool, posix, pytestmark, run_bash,
)

VERSION = db.SCHEMA_VERSION

#: the stub git of _upgrade_fakes, plus `-C <dir> show <tag>:server/db.py`
GIT = ('[[ "$1" == -C ]] && shift 2\n'
       'case "$1" in describe) echo "$FAKE_TAG" ;; checkout) exit 0 ;; '
       'rev-parse) echo "commit-of-$FAKE_TAG" ;; rev-list) echo "commit-of-$4" ;;\n'
       '  show) [[ -z "${FAKE_SHOW_FAIL:-}" ]] || { echo "fatal: bad object" >&2; exit 128; }\n'
       '        printf "import x\\nSCHEMA_VERSION = %s  # comment\\n" "$FAKE_TARGET_SCHEMA" ;;\n'
       'esac')


def _journal(path: Path, event: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO journal (ts, user, event, detail) VALUES ('t', '', ?, '')", (event,))
    conn.commit()
    conn.close()


def _events(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT event FROM journal")}
    finally:
        conn.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _updated_then_used(tmp_path: Path, bindir: Path) -> tuple[Path, Path, Path, str]:
    """An update that succeeded and left its copy; then the new version wrote to the DB."""
    data = tmp_path / "data"
    _seed_db(data)
    _journal(data / "imagesrv.db", "before_update")
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data, tag="v0.49.0")
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    snapshot = data / "imagesrv.db.pre-upgrade"
    assert snapshot.is_file()
    assert "before_update" in _events(snapshot)
    _journal(data / "imagesrv.db", "after_update")          # life under the new version
    fake_tool(bindir, "git", GIT)
    return data, repo, snapshot, _digest(snapshot)


def _revert(tmp_path, bindir, repo, data, *, target_schema: int, live_ok: bool, **env):
    if not live_ok:   # the old code refuses the DB: the health probe never answers
        (repo / "deploy" / "console-live.sh").write_text(
            '#!/usr/bin/env bash\necho "no answer" >&2; exit 1\n', encoding="utf-8", newline="\n")
    tag = "v0.48.0"
    full = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag,
            "FAKE_TARGET_SCHEMA": str(target_schema), **env}
    return run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
                    tmp_path, bindir, env=full)


def test_a_revert_past_a_migration_keeps_the_copy_the_old_code_points_at(tmp_path, bindir):
    data, repo, snapshot, before = _updated_then_used(tmp_path, bindir)
    proc = _revert(tmp_path, bindir, repo, data, target_schema=VERSION - 1, live_ok=False)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode != 0                                   # the old code refused the DB
    assert _digest(snapshot) == before, log                       # the copy is the same bytes
    assert "after_update" not in _events(snapshot)
    assert "after_update" in _events(data / "imagesrv.db")        # the live DB was not "restored"
    assert "systemctl stop imagesrv-server" not in calls(tmp_path)
    status = _status(data) or {}
    assert status.get("state") == "failed", status
    assert "נשמר ולא נדרס" in status.get("error", ""), status


def test_the_name_is_the_one_schema_error_tells_the_operator_to_restore(tmp_path):
    """The copy is kept under the name the refusing code's message gives."""
    assert 'DB_SNAPSHOT="$DB.pre-upgrade"' in UPGRADE.read_text(encoding="utf-8")
    newer = tmp_path / "imagesrv.db"
    raw = sqlite3.connect(newer)
    raw.execute(f"PRAGMA user_version = {VERSION + 1}")
    raw.close()
    with pytest.raises(db.SchemaError) as refused:
        db.connect(newer)
    assert "(imagesrv.db.pre-upgrade)" in str(refused.value)


def test_a_version_that_opens_the_db_still_gets_a_fresh_copy(tmp_path, bindir):
    """Guard: an ordinary update (the tag knows this schema or a newer one)
    still replaces the copy -- its own rollback needs this run's DB."""
    data, repo, snapshot, before = _updated_then_used(tmp_path, bindir)
    proc = _revert(tmp_path, bindir, repo, data, target_schema=VERSION, live_ok=True)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert _digest(snapshot) != before
    assert "after_update" in _events(snapshot)


def test_a_target_schema_that_could_not_be_read_stops_before_the_checkout(tmp_path, bindir):
    data, repo, snapshot, before = _updated_then_used(tmp_path, bindir)
    proc = _revert(tmp_path, bindir, repo, data, target_schema=VERSION - 1, live_ok=True,
                   FAKE_SHOW_FAIL="1")
    assert proc.returncode != 0
    assert _digest(snapshot) == before
    assert not [c for c in calls(tmp_path) if " checkout --detach v0.48.0" in c]
    status = _status(data) or {}
    assert status.get("state") == "failed" and "לא משדרגים" in status.get("error", ""), status


def test_the_script_reads_the_line_server_db_really_has():
    """The sed in server-upgrade.sh reads `SCHEMA_VERSION = <n>`; a reformatted
    line would silently turn the guard off (the tag would look pre-#1894)."""
    text = (REPO / "server" / "db.py").read_text(encoding="utf-8")
    found = re.findall(r"^SCHEMA_VERSION = ([0-9]+)", text, flags=re.M)
    assert found == [str(VERSION)]
    assert "/^SCHEMA_VERSION = [0-9]/" in UPGRADE.read_text(encoding="utf-8")
