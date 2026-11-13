"""#1895 -- the database backup and its health row (Nadav 22/10; policy #1956, 06/11).

Until #1895 the cron line was only documented and the health page had no backup
row. ``server/system/backup_job.py`` is what ``imagesrv-backup.timer`` runs: a
consistent copy (``sqlite3.Connection.backup``), written atomically and read back,
and ``last-backup.json`` for the health row. When a backup is due, how much space
it keeps and where it goes are in test_backup_policy_1956.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server.system import backup_job
from server import db
from test_server_health import health_server  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def data(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(data_dir / "imagesrv.db")
    db.set_setting(conn, "marker", "from-the-live-db")
    return {"dir": data_dir, "conn": conn, "dest": tmp_path / "nas"}


# --- the destination ----------------------------------------------------------------

def test_the_destination_must_be_an_absolute_existing_folder(tmp_path):
    assert backup_job.check_dest("") == ("", None)                       # cleared: the server's drive again
    for bad in ("backup", "/", "/mnt/../etc", "/a\nb", 5):
        dest, why = backup_job.check_dest(bad)
        assert dest is None and why, bad
    dest, why = backup_job.check_dest(str(tmp_path / "missing"))
    assert dest is None and "אינה קיימת" in why
    folder = tmp_path / "nas"
    folder.mkdir()
    assert backup_job.check_dest(folder.as_posix() + "//") == (folder.as_posix(), None)


# --- one backup -----------------------------------------------------------------------

def test_a_backup_is_a_consistent_verified_copy_and_says_so(data):
    data["dest"].mkdir()
    status = backup_job.run(data["dir"], str(data["dest"]), NOW)
    assert status["ok"] is True and status["error"] is None, status
    target = data["dest"] / "db" / "imagesrv-20261022-120000.db"
    assert status["file"] == target.name and target.is_file()
    copy = sqlite3.connect(target)
    assert copy.execute("SELECT value FROM settings WHERE key = 'marker'").fetchone()[0] == "from-the-live-db"
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copy.close()
    assert not list((data["dest"] / "db").glob(".*.tmp"))               # atomic: no leftover
    saved = json.loads((data["dir"] / "last-backup.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True and saved["last_ok"] == NOW.isoformat(timespec="seconds")
    if os.name != "nt":
        assert (target.stat().st_mode & 0o777) == 0o600                # it holds password hashes and MFA secrets


def test_a_write_that_did_not_land_leaves_no_file_and_a_failed_status(data, monkeypatch, caplog):
    data["dest"].mkdir()
    caplog.set_level("WARNING", logger="imagesrv.backup")

    real = os.replace

    def refuse(src, dst):                    # the copy on the NAS does not land; the status file still does
        if Path(dst).parent.name == "db":
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(backup_job.os, "replace", refuse)
    status = backup_job.run(data["dir"], str(data["dest"]), NOW)
    # #2543: ה-strerror ביומן השרת; בקובץ הסטטוס (שהקונסולה מציגה) — המשפט שלנו.
    assert status["ok"] is False and status["error"] == "הגיבוי נכשל — הפרטים ביומן השרת", status
    assert "No space left" in caplog.text
    assert list((data["dest"] / "db").iterdir()) == []                  # neither the copy nor its temp file


def test_a_copy_the_destination_did_not_keep_is_a_failure(data, monkeypatch):
    """Read back from the destination: a NAS that kept something else is not a backup."""
    data["dest"].mkdir()
    real = backup_job._atomic_write

    def lossy(target, payload, mode=0o600):
        real(target, payload[:-4096] if target.parent.name == "db" else payload, mode)
    monkeypatch.setattr(backup_job, "_atomic_write", lossy)
    status = backup_job.run(data["dir"], str(data["dest"]), NOW)
    assert status["ok"] is False and "אינו מה שנכתב" in status["error"], status


def test_a_destination_that_is_not_a_folder_fails_by_name_and_exits_1(data):
    data["dest"].write_text("a file, not a folder", encoding="utf-8")
    db.set_setting(data["conn"], backup_job.SETTING, str(data["dest"]))
    assert backup_job.main(["--data-dir", str(data["dir"])]) == 1
    saved = json.loads((data["dir"] / "last-backup.json").read_text(encoding="utf-8"))
    assert saved["ok"] is False and saved["error"] and saved["last_ok"] is None


def test_last_ok_survives_a_failure_to_the_same_destination(data, monkeypatch):
    data["dest"].mkdir()
    assert backup_job.run(data["dir"], str(data["dest"]), NOW)["ok"]
    monkeypatch.setattr(backup_job, "backup_once", lambda *a: (_ for _ in ()).throw(RuntimeError("NAS gone")))
    status = backup_job.run(data["dir"], str(data["dest"]), NOW + timedelta(hours=1))
    assert status["ok"] is False and status["last_ok"] == NOW.isoformat(timespec="seconds")


# --- the health row -------------------------------------------------------------------

def _health(data, timer="active", now=NOW):
    return backup_job.health_check(data["conn"], data["dir"], timer, now)


def _status(data, **kw):
    value = {"at": NOW.isoformat(timespec="seconds"), "dest": "/mnt/nas", "ok": True, "file": "imagesrv-x.db",
             "error": None, "last_ok": NOW.isoformat(timespec="seconds"), **kw}
    (data["dir"] / "last-backup.json").write_text(json.dumps(value), encoding="utf-8")


def test_health_after_the_destination_is_chosen(data):
    db.set_setting(data["conn"], backup_job.SETTING, "/mnt/nas")
    # a folder on another disk: no "same disk" orange (that one is in test_backup_target_1956)
    db.set_setting(data["conn"], "backup_target", json.dumps({"type": "local", "path": "/mnt/nas", "same_disk": False}))
    assert _health(data)["state"] == "warn" and "עוד לא רץ גיבוי" in _health(data)["detail"]
    assert _health(data, timer="inactive")["state"] == "bad"            # nothing asks whether a backup is due
    _status(data)
    assert _health(data, now=NOW + timedelta(minutes=40))["state"] == "ok"
    _status(data, ok=False, error="OSError: disk gone", last_ok=None)
    failed = _health(data, now=NOW + timedelta(minutes=5))
    assert failed["state"] == "bad" and "disk gone" in failed["detail"]
    _status(data, dest="/mnt/old")                                       # the destination changed since
    assert "עוד לא רץ גיבוי" in _health(data)["detail"]


def test_health_reads_a_broken_status_as_not_checked_never_fine(data):
    db.set_setting(data["conn"], backup_job.SETTING, "/mnt/nas")
    (data["dir"] / "last-backup.json").write_text("{not json", encoding="utf-8")
    assert _health(data)["state"] == "bad"


def test_the_health_page_has_the_backup_row(health_server):
    """/health carries the row: orange until the first backup; the timer reported inactive -- red."""
    rows = {r["id"]: r for r in health_server["admin"].get("/api/console/health").json()}
    assert "backup" in rows and rows["backup"]["state"] == "warn", rows.get("backup")
    assert rows["backup"]["label"] == "גיבוי המסד"
    health_server["fake"]["active"] = "inactive"
    rows = {r["id"]: r for r in health_server["admin"].get("/api/console/health").json()}
    assert rows["backup"]["state"] == "bad" and backup_job.TIMER_UNIT in rows["backup"]["detail"]


# --- the console setting ----------------------------------------------------------------
# #1956: the destination is chosen through POST /api/console/backup-target, which writes and reads back a
# test file; ``backup_dest`` left WRITE_SETTINGS so nothing can skip that. Its checks, the 403 for deploy and
# the console row are in test_backup_target_1956.py.
