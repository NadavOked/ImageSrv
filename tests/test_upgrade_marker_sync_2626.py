"""#2626: deleting the upgrade marker is made durable (sync of its directory).

`rm` is a change of the directory like `os.replace` is (#2623); without a sync
a power cut right after it brings the marker back and the boot recovery rolls
back an upgrade that succeeded.  The real scripts run with a stand-in `sync`
that logs its arguments and whether the marker was already gone when it ran.

**Negative control**: server-upgrade.sh and server-upgrade-recover.sh of
origin/main (0b48b5b3) -- every test below fails on "no sync -f of the data dir".

#2795: the scripts now also sync *before* the marker moves or goes (what it
describes must be on disk first), so "no sync while the marker is there" is no
longer the property; "a sync of the data dir once it is gone" is. The stand-in
fails only once the marker is gone -- the sync #2626 is about.
"""
from __future__ import annotations

import pytest

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    _fake_repo, _run_upgrade, _seed_db, _upgrade_fakes, bindir, calls, fake_tool, posix, pytestmark,
)
from test_upgrade_recovery_1893 import _box, _marker, _recover

# present / gone (no marker, and the console's status already names a stage: the
# run had begun, so the marker was written and removed) / before (the first sync of
# the first stage, ahead of both). Read from the DB, not from earlier syncs: the
# code of #2626 syncs only after the removal, and must still pass.
SYNC = ('if [ -e "$FAKE_MARKER" ]; then at=present\n'
        'elif python3 "$FAKE_STAGED" "${FAKE_MARKER%/*}/imagesrv.db"; then at=gone; else at=before; fi\n'
        'echo "sync-saw $* $at" >> "$FAKE_LOG"\n'
        '[ "$at" != gone ] || exit "${FAKE_SYNC_RC:-0}"')
STAGED = ("import json, sqlite3, sys\n"
          "row = sqlite3.connect(sys.argv[1]).execute(\n"
          "    \"SELECT value FROM settings WHERE key = 'update_status'\").fetchone()\n"
          "sys.exit(0 if row and json.loads(row[0]).get('stage') else 1)\n")


def _synced_once_gone(tmp_path):
    return f"sync-saw -f {posix(tmp_path / 'data')} gone" in calls(tmp_path)


def _fake_sync(bindir, monkeypatch, data, rc=0):
    fake_tool(bindir, "sync", SYNC)
    (bindir / "staged.py").write_text(STAGED, encoding="utf-8", newline="\n")
    monkeypatch.setenv("FAKE_STAGED", posix(bindir / "staged.py"))
    monkeypatch.setenv("FAKE_MARKER", posix(data / "server-upgrade.state"))
    monkeypatch.setenv("FAKE_SYNC_RC", str(rc))


def test_a_finished_upgrade_syncs_the_data_dir_after_the_marker_is_gone(tmp_path, bindir, monkeypatch):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    _fake_sync(bindir, monkeypatch, data)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode == 0, proc.stderr
    assert _synced_once_gone(tmp_path), calls(tmp_path)      # no sync -f of the data dir
    assert not (data / "server-upgrade.state").exists()


def test_a_failed_upgrade_syncs_the_data_dir_after_the_marker_is_gone(tmp_path, bindir, monkeypatch):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    # the upgrade's own git calls (rev-parse HEAD before the fetch) must still answer
    fake_tool(bindir, "git", 'if [ "$1" = -C ] && [ "$3" = fetch ]; then echo "no route" >&2; exit 1; fi\n'
                             'case "$1" in rev-parse) echo "commit-of-$FAKE_TAG" ;; esac')
    _fake_sync(bindir, monkeypatch, data)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    assert any(" fetch " in c for c in calls(tmp_path)), calls(tmp_path)     # the failure path really ran
    assert _synced_once_gone(tmp_path), calls(tmp_path)


def test_a_sync_that_fails_is_reported_and_does_not_undo_a_verified_upgrade(tmp_path, bindir, monkeypatch):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    _fake_sync(bindir, monkeypatch, data, rc=1)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, proc.stderr + log                  # not rolled back by the ERR trap
    assert "sync של תיקיית הסמן נכשל" in proc.stderr + log           # ...and not silent


def test_the_recovery_syncs_the_data_dir_after_removing_the_marker(tmp_path, bindir, monkeypatch):
    data, boot, repo = _box(tmp_path, bindir)
    _marker(data, repo, boot, "fetch")
    _fake_sync(bindir, monkeypatch, data)
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _synced_once_gone(tmp_path), calls(tmp_path)
    assert not (data / "server-upgrade.state").exists()


def test_a_recovery_whose_sync_fails_says_so(tmp_path, bindir, monkeypatch):
    data, boot, repo = _box(tmp_path, bindir)
    _marker(data, repo, boot, "fetch")
    _fake_sync(bindir, monkeypatch, data, rc=1)
    proc = _recover(tmp_path, bindir, data)
    assert "sync of the marker's directory failed" in proc.stdout + proc.stderr
