"""‏#1632 סעיף 2: ‏imagesrv.db מחזיק את ``console_secret`` ואת גיבובי הסיסמאות,
ו-umask ברירת המחדל (022) יצר אותו 0644 — קריא לכל משתמש מקומי.

הבדיקה רצה ב-umask 022 בכוונה: זה ה-umask של יחידת systemd בלי ``UMask=``,
והוא מה שיצר את הבאג. בווינדוס אין הרשאות POSIX — הטסט רץ על ה-Testrunner.
"""

from __future__ import annotations

import os
import stat

import pytest

from server import db

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes (runs on the Linux testrunner)")


@pytest.fixture()
def umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_new_database_is_0600_under_the_default_umask(tmp_path, umask_022):
    path = tmp_path / "imagesrv.db"
    conn = db.connect(path)
    conn.execute("SELECT 1").fetchone()
    assert _mode(path) == 0o600, oct(_mode(path))
    for suffix in ("-wal", "-shm"):
        side = tmp_path / f"imagesrv.db{suffix}"
        if side.exists():
            assert _mode(side) == 0o600, f"{side.name}: {oct(_mode(side))}"
    conn.close()


def test_an_existing_0644_database_and_its_wal_are_tightened(tmp_path, umask_022):
    """שרת ותיק: הקובץ כבר קיים 0644 מהגרסה הקודמת, וגם ה-WAL שנשאר ממנה."""
    path = tmp_path / "imagesrv.db"
    first = db.connect(path)
    first.execute("SELECT 1").fetchone()
    first.close()
    wal = tmp_path / "imagesrv.db-wal"
    wal.touch()
    for target in (path, wal):
        os.chmod(target, 0o644)

    db.connect(path).execute("SELECT 1").fetchone()
    assert _mode(path) == 0o600, oct(_mode(path))
    assert _mode(wal) == 0o600, oct(_mode(wal))
