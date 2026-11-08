"""#2626: deleting the upgrade marker is made durable (sync of its directory).

`rm` is a change of the directory like `os.replace` is (#2623); without a sync
a power cut right after it brings the marker back and the boot recovery rolls
back an upgrade that succeeded.  The real scripts run with a stand-in `sync`
that logs its arguments and whether the marker was already gone when it ran.

**Negative control**: server-upgrade.sh and server-upgrade-recover.sh of
origin/main (0b48b5b3) -- every test below fails on "no sync -f of the data dir".
"""
from __future__ import annotations

import pytest

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    _fake_repo, _run_upgrade, _seed_db, _upgrade_fakes, bindir, calls, fake_tool, posix, pytestmark,
)
from test_upgrade_recovery_1893 import _box, _marker, _recover

SYNC = ('if [ -e "$FAKE_MARKER" ]; then echo "marker-still-there" >> "$FAKE_LOG"; fi\n'
        'exit "${FAKE_SYNC_RC:-0}"')


def _sync_calls(tmp_path, data):
    return [c for c in calls(tmp_path) if c.startswith("sync ")], [c for c in calls(tmp_path) if c == "marker-still-there"]


def _fake_sync(bindir, monkeypatch, data, rc=0):
    fake_tool(bindir, "sync", SYNC)
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
    synced, early = _sync_calls(tmp_path, data)
    assert f"sync -f {posix(data)}" in synced, calls(tmp_path)      # no sync -f of the data dir
    assert not early, "sync ran while the marker was still there"
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
    synced, early = _sync_calls(tmp_path, data)
    assert f"sync -f {posix(data)}" in synced, calls(tmp_path)
    assert not early


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
    synced, early = _sync_calls(tmp_path, data)
    assert f"sync -f {posix(data)}" in synced, calls(tmp_path)
    assert not early
    assert not (data / "server-upgrade.state").exists()


def test_a_recovery_whose_sync_fails_says_so(tmp_path, bindir, monkeypatch):
    data, boot, repo = _box(tmp_path, bindir)
    _marker(data, repo, boot, "fetch")
    _fake_sync(bindir, monkeypatch, data, rc=1)
    proc = _recover(tmp_path, bindir, data)
    assert "sync of the marker's directory failed" in proc.stdout + proc.stderr
