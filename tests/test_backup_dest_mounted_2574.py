"""#2574 -- a backup to a folder chosen on another drive must not fall back to the system drive.

``check_dest`` and ``write_probe`` look at the folder once, when the operator picks it. If that second drive
later fails, or is not mounted after a reboot, ``/mnt/<name>`` is an empty folder on the system drive (or is
gone, and the old ``mkdir(parents=True)`` made it again) and the backup "succeeded" -- onto the same drive as
the database, with no sign anywhere.

The rule: a folder stored with ``same_disk: false`` is checked again at **every** backup. Not on another
drive any more, or not checkable (principle 5) -- the backup fails by name, writes nothing, and the health
row is red. A plain folder on the system drive stays a legitimate destination (orange warning, not a failure).

``same_disk`` is replaced by a fake: the test box is one filesystem, and the sysfs walk has its own tests
(``test_backup_target_1956``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.system import backup_job, backup_target


@pytest.fixture()
def box(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = db.connect(data / "imagesrv.db")
    db.set_setting(conn, "marker", "from-the-live-db")
    return {"data": data, "conn": conn, "tmp": tmp_path}


def choose(box, same_disk, name="usb") -> Path:
    """The operator picked this folder; the setting says whether it was on another drive then."""
    folder = box["tmp"] / name
    folder.mkdir()
    db.set_settings(box["conn"], {backup_job.SETTING: folder.as_posix(),
                                  "backup_target": json.dumps({"type": "local", "path": folder.as_posix(),
                                                               "same_disk": same_disk})})
    return folder


def drive_is(monkeypatch, verdict):
    """What ``same_disk`` answers at backup time: True / False / None (not read)."""
    monkeypatch.setattr(backup_target, "same_disk", lambda path, data_dir: verdict)


def tick(box) -> int:
    return backup_job.main(["--data-dir", str(box["data"]), "--now"])


def status(box) -> dict:
    return json.loads((box["data"] / "last-backup.json").read_text(encoding="utf-8"))


def copies(folder: Path) -> list[str]:
    return sorted(p.name for p in (folder / "db").glob("imagesrv-[0-9]*.db")) if (folder / "db").is_dir() else []


def health(box) -> dict:
    return backup_job.health_check(box["conn"], box["data"], "active")


# --- the drive is gone -----------------------------------------------------------------------

def test_a_folder_that_fell_back_to_the_system_drive_fails_and_writes_nothing(box, monkeypatch):
    folder = choose(box, same_disk=False)
    drive_is(monkeypatch, True)                    # unmounted: the empty folder is on the database's drive
    assert tick(box) == 1
    assert copies(folder) == [] and not (folder / "db").exists()
    assert status(box)["ok"] is False and "אותו כונן כמו המסד" in status(box)["error"], status(box)


def test_the_failure_is_red_in_the_health_row(box, monkeypatch):
    folder = choose(box, same_disk=False)
    drive_is(monkeypatch, True)
    assert tick(box) == 1
    row = health(box)
    assert row["state"] == "bad" and "אותו כונן כמו המסד" in row["detail"], row
    assert copies(folder) == []


def test_a_folder_that_was_removed_is_not_created_again(box, monkeypatch):
    folder = choose(box, same_disk=False)
    shutil.rmtree(folder)
    drive_is(monkeypatch, False)                   # even a "different drive" answer: the folder is not there
    assert tick(box) == 1
    assert not folder.exists(), "the destination folder itself must not be created -- only db/ inside it"
    assert status(box)["ok"] is False and "אינה קיימת" in status(box)["error"], status(box)


def test_a_drive_that_cannot_be_checked_is_a_failure_not_a_pass(box, monkeypatch):
    folder = choose(box, same_disk=False)
    drive_is(monkeypatch, None)                    # sysfs not read: "not checked" is not "another drive"
    assert tick(box) == 1
    assert copies(folder) == []
    assert status(box)["ok"] is False and "לא הצלחנו לבדוק" in status(box)["error"], status(box)


def test_mounted_again_the_next_backup_goes_through(box, monkeypatch):
    folder = choose(box, same_disk=False)
    drive_is(monkeypatch, True)
    assert tick(box) == 1
    drive_is(monkeypatch, False)
    assert tick(box) == 0
    assert len(copies(folder)) == 1 and status(box)["ok"] is True


# --- what must keep working ------------------------------------------------------------------

def test_a_folder_on_another_drive_that_is_there_backs_up(box, monkeypatch):
    folder = choose(box, same_disk=False)
    drive_is(monkeypatch, False)
    assert tick(box) == 0
    assert len(copies(folder)) == 1 and status(box)["ok"] is True


@pytest.mark.parametrize("stored", [True, None])
def test_a_plain_folder_on_the_system_drive_is_still_a_legitimate_destination(box, monkeypatch, stored):
    """``same_disk: true`` (orange warning at setup) and ``null`` (never checked) never required mounting."""
    folder = choose(box, same_disk=stored)
    drive_is(monkeypatch, True)
    assert tick(box) == 0
    assert len(copies(folder)) == 1 and status(box)["ok"] is True


def test_the_servers_own_drive_default_is_not_checked(box, monkeypatch):
    drive_is(monkeypatch, True)
    assert tick(box) == 0
    assert len(copies(box["data"] / "backups")) == 1 and status(box)["ok"] is True


def test_expects_other_disk_only_for_a_stored_local_folder_that_was_on_another_drive():
    stored = "/mnt/usb"
    local = lambda same: json.dumps({"type": "local", "path": stored, "same_disk": same})    # noqa: E731
    assert backup_job.expects_other_disk(stored, local(False)) is True
    assert backup_job.expects_other_disk(stored, local(True)) is False
    assert backup_job.expects_other_disk(stored, local(None)) is False
    assert backup_job.expects_other_disk("", local(False)) is False                 # no folder chosen: the server drive
    assert backup_job.expects_other_disk(stored, json.dumps({"type": "default"})) is False
    assert backup_job.expects_other_disk(stored, "{not json") is False
    assert backup_job.expects_other_disk(stored, None) is False
    assert backup_job.expects_other_disk(stored, json.dumps({"type": "smb", "same_disk": False})) is False
