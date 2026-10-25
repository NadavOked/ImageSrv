"""‏#1951: קובץ מחיצה שהתקבל עובר fsync לפני שהשרת עונה "התקבל".

‏QA-G49 (22/10 04:12): כיבוי קשיח של TA ‏33 שניות אחרי `capture_verifying`.
אחרי העלייה — ‏`work_area_kept`, ‏`sha256 mismatch` על p3, ‏`capture_failed`:
סוף הקובץ ישב במטמון הקרנל, כי fsync רץ רק בפרסום (#1813). הקליטה אבדה אף
שכל הבייטים הגיעו.

הטסט מקליט כל `os.fsync` (מזהה הקובץ וגודלו באותו רגע) ובודק שהקובץ שהתקבל
— בגודלו המלא — ורשומת ה-sha256 שלו עברו fsync עד שה-PUT ענה.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from server import capture  # noqa: E402
from server.tasks import TOKEN_HEADER, staging_dir  # noqa: E402

from test_capture import PART_B, make_task, setup_build_machine, task_token  # noqa: E402

UPLOAD_SHA = getattr(capture, "UPLOAD_SHA", ".upload-sha256")


def test_the_received_file_and_its_sha_record_are_on_disk_before_the_answer(
        server, images_root, monkeypatch):
    synced: list[tuple[int, int]] = []
    real = os.fsync

    def recording_fsync(fd):
        st = os.fstat(fd)
        synced.append((st.st_ino, st.st_size))
        return real(fd)

    monkeypatch.setattr(capture.os, "fsync", recording_fsync)
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    r = server["anon"].put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst", content=PART_B,
                           headers={TOKEN_HEADER: task_token(server, tid)})
    assert r.status_code == 200, r.text

    staged = staging_dir(images_root, tid) / "p3.windows.pcl.zst"
    record = staged.with_name(staged.name + UPLOAD_SHA)
    assert (staged.stat().st_ino, len(PART_B)) in synced, \
        f"הקובץ שהתקבל לא עבר fsync לפני התשובה: {synced}"
    assert record.stat().st_ino in {ino for ino, _ in synced}, \
        f"רשומת ה-sha256 לא עברה fsync לפני התשובה: {synced}"
