"""#2424 (Nadav 04/11, option a) -- no backup carries ``console_secret``.

``console_secret`` is the HMAC key of the console cookie, and ``read_session`` checks only the HMAC
and the expiry the signer wrote: whoever could read the backup share signed an admin cookie with a
far expiry and walked in without a password and without MFA (security scan 04/11, F2).

Nadav's decision: the secret is not backed up; a server restored from a backup makes a new one,
so every cookie signed before it is void and whoever was logged in signs in again. Whole-backup
encryption was not chosen -- it needs a key kept off the server.

Checked here on all three writers of the database copy (the hourly job, the console download, and
``tools/backup-server.sh`` -- all three go through ``consistent_db_copy``), as bytes: the hex is
not in the file at all, not only not in the ``settings`` row (a deleted row stays in a free page).
And the restore: a new 32-byte secret, the old cookie refused, a new one accepted, the users there.
"""

from __future__ import annotations

import io
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server import db
from server.console import auth, users
from server.system import backup, backup_job

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def live(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(data_dir / "imagesrv.db")
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    db.set_setting(conn, "marker", "from-the-live-db")
    secret = db.get_setting(conn, auth.SECRET_KEY)
    assert secret and len(bytes.fromhex(secret)) == 32           # the live server has one
    return {"dir": data_dir, "conn": conn, "secret": secret, "dest": tmp_path / "nas"}


def _hourly(live) -> bytes:
    live["dest"].mkdir(exist_ok=True)
    status = backup_job.run(live["dir"], str(live["dest"]), NOW)
    assert status["ok"] is True, status
    return (live["dest"] / "db" / status["file"]).read_bytes()


def _download(live) -> bytes:
    payload, _name = backup.build_archive(live["conn"], live["dir"], "v0.82.0", NOW)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    assert all(live["secret"].encode() not in body for body in members.values()), "secret in the archive"
    return members["data/imagesrv.db"]


def _script_copy(live) -> bytes:
    """What tools/backup-server.sh runs: a raw sqlite3 connection, consistent_db_copy, write_bytes."""
    conn = sqlite3.connect(f"file:{(live['dir'] / 'imagesrv.db').as_posix()}?mode=ro", uri=True)
    try:
        return backup.consistent_db_copy(conn)
    finally:
        conn.close()


WRITERS = {"hourly": _hourly, "download": _download, "backup-server.sh": _script_copy}


def test_the_backup_knows_the_name_the_console_signs_with():
    assert backup.SECRET_SETTING == auth.SECRET_KEY


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_no_backup_carries_the_cookie_secret_and_the_live_db_keeps_it(live, writer):
    raw = WRITERS[writer](live)
    assert live["secret"].encode() not in raw, "the hex is still in the file (row or free page)"
    copy = sqlite3.connect(":memory:")
    copy.deserialize(raw)
    assert copy.execute("SELECT value FROM settings WHERE key = ?", (auth.SECRET_KEY,)).fetchone() is None
    assert copy.execute("SELECT value FROM settings WHERE key = 'marker'").fetchone()[0] == "from-the-live-db"
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copy.close()
    assert db.get_setting(live["conn"], auth.SECRET_KEY) == live["secret"], "the copy was cut, not the server"


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_server_restored_from_the_backup_makes_a_new_secret_and_old_cookies_are_void(live, writer, tmp_path):
    cookie = auth.issue(live["conn"], "noc", "admin")
    assert auth.read_session(live["conn"], cookie) is not None        # the cookie is good on the live server
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    (restored_dir / "imagesrv.db").write_bytes(WRITERS[writer](live))

    restored = db.connect(restored_dir / "imagesrv.db")               # what server.main does on start
    auth.assert_secret(restored)                                       # starts: no SecretUnusable, not empty
    new = db.get_setting(restored, auth.SECRET_KEY)
    assert new and len(bytes.fromhex(new)) == 32 and new != live["secret"]
    assert auth.read_session(restored, cookie) is None, "a cookie signed before the restore still logs in"
    fresh = auth.issue(restored, "noc", "admin")                      # signing in again works
    session = auth.read_session(restored, fresh)
    assert session is not None and (session.username, session.role) == ("noc", "admin")
    assert users.verify(restored, "noc", "admin-pass-123") == "admin"   # the password survived the restore
