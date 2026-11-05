"""#2423 -- the hourly backup runs as root and writes into a share other people write to.

``_atomic_write`` opened ``.<name>.tmp`` -- a name anyone could predict -- with
``O_WRONLY|O_CREAT|O_TRUNC``: a symlink planted under that name sent root's write to any file
on the server, and ``os.replace`` then moved the *link* to the final name, so the sha256 and the
``integrity_check`` read back through it and reported ``ok=true`` (security scan 04/11, F1).

What is checked here is the victim file and the status the operator sees -- not the open flags.
Symlinks need privileges on Windows: these run on the Linux testrunner and in CI.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server import db
from server.system import backup_job

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="symlinks: Linux testrunner / CI")

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
FINAL = "imagesrv-20261104-120000.db"
VICTIM_BYTES = b"-----BEGIN PRIVATE KEY-----\nproduction\n-----END PRIVATE KEY-----\n"


@pytest.fixture()
def box(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(data_dir / "imagesrv.db")
    db.set_setting(conn, "marker", "from-the-live-db")
    victim = tmp_path / "victim" / "console.key"            # a production file outside the share
    victim.parent.mkdir()
    victim.write_bytes(VICTIM_BYTES)
    share = tmp_path / "nas"
    (share / "db").mkdir(parents=True)
    return {"data": data_dir, "share": share, "db": share / "db", "victim": victim}


def _run(box):
    return backup_job.run(box["data"], str(box["share"]), NOW)


def test_a_link_planted_under_the_old_temp_name_does_not_redirect_the_write(box):
    (box["db"] / f".{FINAL}.tmp").symlink_to(box["victim"])
    status = _run(box)
    assert box["victim"].read_bytes() == VICTIM_BYTES, "root wrote the backup through the planted link"
    assert status["ok"] is True, status
    final = box["db"] / FINAL
    assert not final.is_symlink() and final.is_file()
    if os.name == "posix":
        assert (final.stat().st_mode & 0o777) == 0o600


def test_a_guessed_temp_name_is_refused_not_followed(box, monkeypatch):
    """Even with the name known in advance (O_EXCL|O_NOFOLLOW): no write through it, and a named failure."""
    planted = box["db"] / f".{FINAL}.tmp"
    planted.symlink_to(box["victim"])
    monkeypatch.setattr(backup_job, "_tmp_name", lambda target: target.with_name(f".{target.name}.tmp"),
                        raising=False)
    status = _run(box)
    assert box["victim"].read_bytes() == VICTIM_BYTES
    assert status["ok"] is False and status["error"], status
    assert planted.is_symlink() and not (box["db"] / FINAL).exists()


def test_a_final_name_that_is_a_link_is_refused_by_name(box):
    (box["db"] / FINAL).symlink_to(box["victim"])
    status = _run(box)
    assert status["ok"] is False and "קישור סמלי" in (status["error"] or ""), status
    assert box["victim"].read_bytes() == VICTIM_BYTES


def test_a_db_folder_that_is_a_link_is_refused_and_nothing_lands_where_it_points(box, tmp_path):
    elsewhere = tmp_path / "etc-like"
    elsewhere.mkdir()
    (box["db"]).rmdir()
    (box["share"] / "db").symlink_to(elsewhere, target_is_directory=True)
    status = _run(box)
    assert status["ok"] is False and "קישור סמלי" in (status["error"] or ""), status
    assert list(elsewhere.iterdir()) == []


def test_the_read_back_does_not_follow_a_link_swapped_in_after_the_write(box, tmp_path, monkeypatch):
    """The share's owner swaps the written file for a link to an identical copy: the old read-back
    followed it, matched the sha256 and said ok -- about a file that is not the one on the share."""
    real = os.replace
    copy = tmp_path / "copy.db"

    def swap(src, dst):
        real(src, dst)
        if Path(dst).parent.name == "db":
            copy.write_bytes(Path(dst).read_bytes())
            Path(dst).unlink()
            Path(dst).symlink_to(copy)
    monkeypatch.setattr(backup_job.os, "replace", swap)
    status = _run(box)
    assert status["ok"] is False and status["error"], status
