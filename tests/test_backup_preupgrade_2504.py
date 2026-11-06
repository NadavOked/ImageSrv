"""#2504 -- no backup carries a copy of the database other than the cleaned one.

``tools/server-upgrade.sh`` (#1894) writes a full sqlite copy of the DB to
``<data_dir>/imagesrv.db.pre-upgrade`` before every update and leaves it there: it is the way
back. The two writers that pack ``data_dir`` skipped only the exact names ``imagesrv.db``,
``-wal``, ``-shm`` and ``-journal``, so that copy -- with ``console_secret`` (#2424) and the SMB
passwords -- went into the console download and into ``<dest>/data/`` of ``backup-server.sh``.

The fix is a pattern, not one more name: every ``imagesrv.db*`` in ``data_dir`` stays out, and
the DB enters only through ``consistent_db_copy``. Checked here as bytes, on every writer, the
same way as ``test_backup_console_secret_2424.py``: the hex of the secret is in no file the
backup wrote. The hourly job copies only the DB (never ``data_dir``) -- it is here as a guard.
"""

from __future__ import annotations

import io
import os
import sqlite3
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from native import requires_native
from server import db
from server.console import auth, users
from server.system import backup, backup_job
from test_installer_1131 import find_bash

REPO = Path(__file__).resolve().parent.parent
BACKUP_SH = REPO / "tools" / "backup-server.sh"
BASH = find_bash()
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
#: What a data_dir can hold besides the live DB: the copy server-upgrade.sh leaves, the .tmp of a
#: run cut mid-copy, the sqlite journal of that .tmp, and a copy someone may add tomorrow.
DB_COPIES = ("imagesrv.db.pre-upgrade", "imagesrv.db.pre-upgrade.tmp",
             "imagesrv.db.pre-upgrade.tmp-journal", "imagesrv.db.bak")
KEPT = ("console-tls/console.crt", "netcfg.json")


@pytest.fixture()
def server(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(data_dir / "imagesrv.db")
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    secret = db.get_setting(conn, auth.SECRET_KEY)
    assert secret and len(bytes.fromhex(secret)) == 32
    # What tools/server-upgrade.sh:237 does: sqlite backup of the live DB, as is.
    source, snap = sqlite3.connect(data_dir / "imagesrv.db"), sqlite3.connect(data_dir / "imagesrv.db.pre-upgrade")
    source.backup(snap)
    snap.close()
    source.close()
    full = (data_dir / "imagesrv.db.pre-upgrade").read_bytes()
    assert secret.encode() in full                                   # the copy really carries it
    for name in DB_COPIES[1:]:
        (data_dir / name).write_bytes(full)
    for rel in KEPT:
        (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / rel).write_bytes(f"kept {rel}\n".encode())
    yield {"dir": data_dir, "conn": conn, "secret": secret.encode(), "tmp": tmp_path}
    conn.close()


def test_the_download_carries_no_db_copy_and_keeps_the_rest_of_data_dir(server):
    payload, _name = backup.build_archive(server["conn"], server["dir"], "v0.82.0", NOW)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    leaked = sorted(name for name, body in members.items() if server["secret"] in body)
    assert leaked == [], f"console_secret in the archive: {leaked}"
    copies = sorted(n for n in members if Path(n).name.startswith("imagesrv.db") and n != "data/imagesrv.db")
    assert copies == [], copies
    for rel in KEPT:                                                 # the rest of data_dir is still backed up
        assert members[f"data/{rel}"] == f"kept {rel}\n".encode()


def test_the_hourly_job_reads_only_the_db(server):
    dest = server["tmp"] / "nas"
    dest.mkdir()
    status = backup_job.run(server["dir"], str(dest), NOW)
    assert status["ok"] is True, status
    leaked = [p for p in dest.rglob("*") if p.is_file() and server["secret"] in p.read_bytes()]
    assert leaked == [], leaked


posix_tools = requires_native(("bash", BASH), "sha256sum", posix=True,
                              why="backup-server.sh הוא bash+coreutils")


def _backup_server(server, dest: Path) -> subprocess.CompletedProcess:
    root = server["tmp"] / "sysroot"
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "fstab").write_text("UUID=1 / ext4 defaults 0 1\n")
    return subprocess.run(
        [BASH, str(BACKUP_SH), "--dest", str(dest), "--data-dir", str(server["dir"]),
         "--app-dir", str(REPO), "--system-root", str(root), "--no-images"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=180, cwd=str(REPO),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})


@posix_tools
def test_backup_server_carries_no_db_copy_and_keeps_the_rest_of_data_dir(server):
    dest = server["tmp"] / "dest"
    r = _backup_server(server, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    leaked = [p for p in dest.rglob("*") if p.is_file() and server["secret"] in p.read_bytes()]
    assert leaked == [], leaked
    assert sorted(p.name for p in (dest / "data").glob("imagesrv.db*")) == []
    for rel in KEPT:
        assert (dest / "data" / rel).read_bytes() == f"kept {rel}\n".encode()


@posix_tools
def test_backup_server_removes_the_copy_an_older_run_left_in_the_mirror(server):
    """``data/`` is a rolling mirror without ``--delete``: a copy an older version wrote there
    stays forever unless this run takes it out -- and says so."""
    dest = server["tmp"] / "dest"
    (dest / "data").mkdir(parents=True)
    (dest / "data" / "imagesrv.db.pre-upgrade").write_bytes(
        (server["dir"] / "imagesrv.db.pre-upgrade").read_bytes())
    r = _backup_server(server, dest)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (dest / "data" / "imagesrv.db.pre-upgrade").exists()
    assert "imagesrv.db.pre-upgrade" in r.stdout and "#2504" in r.stdout
    leaked = [p for p in dest.rglob("*") if p.is_file() and server["secret"] in p.read_bytes()]
    assert leaked == [], leaked
