"""#3949 (CodeQL, public gate): the storage-location log lines carry no secret and no raw line break.

A command's stderr is kept out of the console reply and goes to the server log, so what is written
there is masked (``password=...``, a CHAP secret) and one line (py/clear-text-logging-sensitive-data,
py/log-injection). A single flat file of unit calls: no app, no fixtures from the console tests.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from server.storage import locations as sl
from server.storage.locations import FAILED, OK, UNCHECKED, HookResult


def _storage_records(caplog):
    return [r for r in caplog.records if r.name == "imagesrv.storage_locations"]


@pytest.mark.parametrize("raw,secret", [
    ("mount error(13): password=hunter2 rejected", "hunter2"),
    ("login failed: chap_secret: s3cr3t-chap", "s3cr3t-chap"),
    ("PASSWORD = Upper9 refused", "Upper9"),
])
def test_operator_reason_masks_a_secret_the_tool_echoed(caplog, raw, secret):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")
    shown = sl.operator_reason(HookResult(FAILED, raw), "העיגון נכשל")
    assert shown == "העיגון נכשל — הפרטים ביומן השרת"
    records = _storage_records(caplog)
    assert len(records) == 1
    assert secret not in records[0].getMessage()
    assert "[redacted]" in records[0].getMessage()


def test_operator_reason_logs_one_line_for_a_reason_with_a_forged_record(caplog):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")
    sl.operator_reason(HookResult(FAILED, "boom\r\nWARNING forged: admin logged in"), "הבדיקה נכשלה")
    message = _storage_records(caplog)[0].getMessage()
    assert "\n" not in message and "\r" not in message
    assert "forged: admin logged in" in message, "the text is kept, only the break is gone"


def test_a_huge_stderr_is_capped_in_the_log(caplog):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")
    sl.operator_reason(HookResult(FAILED, "x" * 5000), "הבדיקה נכשלה")
    assert len(_storage_records(caplog)[0].getMessage()) < 700


def _nfs_row(target):
    return {"id": "loc1", "type": "nfs", "mount_point": target,
            "params_json": json.dumps({"server": "10.0.0.5", "export": "/images"})}


def test_images_dir_failure_logs_the_mount_point_on_one_line(caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")

    def _boom(self, *args, **kwargs):
        raise OSError("read-only\nWARNING forged")
    monkeypatch.setattr(Path, "mkdir", _boom)
    hooks = {"mount": lambda *a: HookResult(OK)}
    result = sl.apply_mount(_nfs_row("/mnt/loc\nINJECTED"), hooks)
    assert result.status == FAILED
    assert result.reason == "העיגון הצליח אבל לא נוצרה תיקיית images/ — הפרטים ביומן השרת"
    message = _storage_records(caplog)[0].getMessage()
    assert "\n" not in message
    assert "INJECTED" in message and "forged" in message


def test_unreadable_smb_creds_file_is_logged_without_the_secret_and_shown_without_the_stderr(caplog):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")
    row = {"id": "loc2", "type": "smb", "mount_point": "/mnt/smb",
           "params_json": json.dumps({"server": "10.0.0.6", "share": "images", "username": "svc"})}
    hooks = {"creds_password_present":
             lambda path: HookResult(UNCHECKED, "denied password=hunter2\nforged"),
             "mount": lambda *a: pytest.fail("a mount without the password must not run")}
    result = sl.apply_mount(row, hooks)
    assert result.status == UNCHECKED
    assert result.reason == f"{sl.SECRET_OF['smb'][2]} — הפרטים ביומן השרת"
    message = _storage_records(caplog)[0].getMessage()
    assert "hunter2" not in message and "\n" not in message
    assert "denied" in message
