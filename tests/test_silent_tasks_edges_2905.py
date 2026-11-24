"""#2905 / #3695: הקצוות של סגירת משימה שקטה -- חותמת שאי-אפשר לקרוא, אזור ביניים שאי-אפשר לקרוא או למחוק, ולולאת הרקע.

`test_silent_task_2905.py` בודק את הנתיב המרכזי מול שרת אמיתי. כאן הקצוות שבהם **המדידה עצמה** נכשלה, ובכולם התשובה היא
"לא נמדד" ולא "שקט" (עיקרון 5):

- חותמת `updated_at` שאינה תאריך, או בלי אזור זמן, אינה ראיה: `None`, ואין סגירה.
- אזור ביניים שאי-אפשר לקרוא (הרשאה, מזהה לא תקין) -- `None`. קובץ שנעלם בין הרשימה ל-`stat` אינו שגיאה ואינו שקט.
- מחיקת אזור ביניים שנכשלה נרשמת ביומן (`capture_staging_delete_failed`), ואזור שאינו קיים אינו כשל.
- `background_sweep` אינו זורק, אבל כשל נרשם ביומן (`task_sweep_failed`) ובלוג, וגם כשל ביומן עצמו אינו מפיל את הלולאה.

**בקרה שלילית:** מוטציה אחת בקוד הייצור לכל קבוצה (ראו את גוף ה-PR) מפילה את הטסטים על התוצאה, לא על ייבוא.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.system import silent_tasks

STAMP = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
MAC = "aa:bb:cc:00:00:10"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _row(conn, *, task_id="tsk_edge", task_type="capture", updated_at=STAMP.isoformat()):
    return conn.execute("SELECT ? AS id, ? AS mac, ? AS type, ? AS updated_at",
                        (task_id, MAC, task_type, updated_at)).fetchone()


def _events(conn, event):
    return [(r["detail"], r["machine"]) for r in conn.execute(
        "SELECT detail, machine FROM journal WHERE event = ? ORDER BY rowid", (event,))]


# --- חותמת ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("stamp", ["not a date", "2026-13-45T99:00:00+00:00", "2026-11-24T08:00:00", "", None, 1763971200])
def test_a_stamp_that_is_not_an_aware_time_is_not_a_sign_of_life(stamp):
    assert silent_tasks._parse(stamp) is None


def test_an_aware_stamp_is_read_as_it_is():
    assert silent_tasks._parse(STAMP.isoformat()) == STAMP


def test_a_task_whose_stamp_cannot_be_read_has_no_last_sign_of_life(conn, tmp_path):
    for stamp in ("garbage", None):
        assert silent_tasks._last_sign_of_life(_row(conn, updated_at=stamp), tmp_path) is None
    assert silent_tasks._last_sign_of_life(_row(conn, task_type="direct"), tmp_path) == STAMP


# --- אזור הביניים בקליטה ------------------------------------------------------------------------------------------------


class _Entry:
    def __init__(self, mtime=None):
        self.mtime = mtime

    def stat(self):
        if self.mtime is None:
            raise FileNotFoundError("removed between the listing and the stat")
        return type("Stat", (), {"st_mtime": self.mtime})()


class _Listing:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False


class _OsWith:
    """`os` של המודול בלבד, עם `scandir` מוחלף: החלפה של `os.scandir` הגלובלי הייתה שוברת גם את ניקוי תיקיות הטסט של pytest."""

    def __init__(self, scandir):
        self.scandir = scandir

    def __getattr__(self, name):
        return getattr(os, name)


def _staging(conn, tmp_path, task_id="tsk_edge"):
    folder = tmp_path / f".capture-{task_id}"
    folder.mkdir()
    return folder


def test_a_file_that_vanished_between_the_listing_and_the_stat_is_neither_silence_nor_an_error(conn, tmp_path, monkeypatch):
    folder = _staging(conn, tmp_path)
    old = (STAMP - timedelta(hours=2)).timestamp()
    os.utime(folder, (old, old))
    fresh = (STAMP + timedelta(minutes=5)).timestamp()
    monkeypatch.setattr(silent_tasks, "os", _OsWith(lambda path: _Listing([_Entry(None), _Entry(fresh), _Entry(None)])))

    last = silent_tasks._last_sign_of_life(_row(conn), tmp_path)

    assert last == datetime.fromtimestamp(fresh, timezone.utc), "הקובץ שנשאר הוא הראיה לחיים, והקובץ שנעלם אינו מבטל אותה"


def test_a_staging_area_that_cannot_be_read_is_not_measured_and_not_silence(conn, tmp_path, monkeypatch):
    _staging(conn, tmp_path)

    def denied(path):
        raise PermissionError(13, "Permission denied", str(path))
    monkeypatch.setattr(silent_tasks, "os", _OsWith(denied))

    assert silent_tasks._last_sign_of_life(_row(conn), tmp_path) is None


def test_a_task_id_that_is_not_a_single_path_component_is_not_measured(conn, tmp_path):
    assert silent_tasks._last_sign_of_life(_row(conn, task_id="../escape"), tmp_path) is None


def test_a_capture_that_uploaded_nothing_yet_is_measured_by_its_stamp_alone(conn, tmp_path):
    assert silent_tasks._last_sign_of_life(_row(conn), tmp_path) == STAMP


# --- מחיקת אזור הביניים של קליטה שקטה -----------------------------------------------------------------------------------


def test_a_staging_area_that_cannot_be_deleted_is_journaled_by_name_and_logged(conn, tmp_path, monkeypatch, caplog):
    folder = _staging(conn, tmp_path)

    def locked(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied  \n locked", str(path))
    monkeypatch.setattr(silent_tasks.shutil, "rmtree", locked)

    with caplog.at_level(logging.WARNING, logger="imagesrv.silent_tasks"):
        silent_tasks._drop_staging(conn, tmp_path, _row(conn))

    ((detail, machine),) = _events(conn, "capture_staging_delete_failed")
    assert machine == MAC
    assert "tsk_edge" in detail and str(folder) in detail and "PermissionError" in detail
    assert "\n" not in detail and "  " not in detail, "השגיאה נרשמת בשורה אחת"
    assert folder.exists(), "הכשל אמיתי: התיקייה נשארה"
    assert "tsk_edge" in caplog.text and "was not deleted" in caplog.text


def test_a_staging_area_that_does_not_exist_is_not_a_delete_failure(conn, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.silent_tasks"):
        silent_tasks._drop_staging(conn, tmp_path, _row(conn))
    assert _events(conn, "capture_staging_delete_failed") == []
    assert "was not deleted" not in caplog.text


def test_a_staging_area_that_can_be_deleted_is_deleted_without_a_failure_row(conn, tmp_path):
    folder = _staging(conn, tmp_path)
    (folder / "p1.pcl.zst").write_bytes(b"x" * 10)
    silent_tasks._drop_staging(conn, tmp_path, _row(conn))
    assert not folder.exists()
    assert _events(conn, "capture_staging_delete_failed") == []


# --- לולאת הרקע ---------------------------------------------------------------------------------------------------------


def test_the_background_sweep_reports_success_only_when_the_sweep_ran(conn, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(silent_tasks, "sweep", lambda *args: calls.append(args) or [])
    assert silent_tasks.background_sweep(conn, "store", tmp_path, 12.5) is True
    assert calls == [(conn, "store", tmp_path, 12.5)]
    assert _events(conn, "task_sweep_failed") == []


def test_a_sweep_that_fails_is_journaled_logged_and_does_not_stop_the_loop(conn, tmp_path, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("the tasks table\nis unreadable " + "x" * 400)
    monkeypatch.setattr(silent_tasks, "sweep", broken)

    with caplog.at_level(logging.ERROR, logger="imagesrv.silent_tasks"):
        assert silent_tasks.background_sweep(conn, "store", tmp_path, 0.0) is False

    ((detail, _machine),) = _events(conn, "task_sweep_failed")
    assert detail.startswith("RuntimeError: the tasks table is unreadable"), detail
    assert len(detail) == 200 and "\n" not in detail
    assert "silent task sweep failed" in caplog.text


def test_a_journal_that_also_fails_does_not_stop_the_loop_either(conn, tmp_path, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("sweep")

    def no_journal(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(silent_tasks, "sweep", broken)
    monkeypatch.setattr(silent_tasks, "journal", no_journal)

    with caplog.at_level(logging.ERROR, logger="imagesrv.silent_tasks"):
        assert silent_tasks.background_sweep(conn, "store", tmp_path, 0.0) is False

    assert "silent task sweep failed" in caplog.text
    assert "was not journaled" in caplog.text, "גם כשל ביומן נרשם, ולא נבלע"
