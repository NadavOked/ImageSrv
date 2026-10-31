"""‏#1830: קובץ מחיצה ריק עם גיבוב "תואם" אינו נכנס לספרייה.

הסוכן מגבב את מה שהוא שלח. זרם שנקטע לפני הבייט הראשון יוצא קובץ של אפס
בייט, וה-sha256 שלו (של מחרוזת ריקה) תואם בדיוק את מה שהשרת קיבל — כל
בדיקת גיבוב עוברת. עד #1830 ‏`manifest_problem` בדק רק שהקובץ **קיים**.

זרם של partclone אינו ריק לעולם — גם מערכת קבצים ריקה נושאת כותרת ומפת
סיביות, ו-partclone.dd נושא את המחיצה כולה — ולכן אפס בייט הוא "לא הגיע
כלום", ולא "מחיצה קטנה".
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from server.core.tasks import TOKEN_HEADER  # noqa: E402

from test_capture import (PART_A, PART_B, make_task, manifest_for, setup_build_machine,  # noqa: E402
                          sha, task_token, wait_verified)


def _capture(server, files: dict[str, bytes], manifest: dict):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid = created["id"]
    head = {TOKEN_HEADER: task_token(server, tid)}
    for name, data in files.items():
        r = server["anon"].put(f"/api/v1/capture/{tid}/files/{name}", content=data, headers=head)
        assert r.status_code == 200, r.text
    r = server["anon"].put(f"/api/v1/capture/{tid}/manifest",
                           content=json.dumps(manifest).encode(), headers=head)
    return tid, created["image_id"], r


def _state(server, tid: str) -> dict:
    return next(t for t in server["admin"].get("/api/console/tasks").json() if t["id"] == tid)


def test_an_empty_partition_file_with_a_matching_sha_is_refused_by_name(server, images_root):
    manifest = manifest_for()
    manifest["partitions"][1]["sha256"] = sha(b"")
    tid, image_id, r = _capture(server, {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": b""},
                                manifest)

    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "partition file is empty: p3.windows.pcl.zst -- no bytes arrived"
    row = _state(server, tid)
    assert row["state"] == "failed"
    assert "p3.windows.pcl.zst" in row["error"]
    assert not (images_root / image_id).exists()


def test_a_capture_whose_files_carry_bytes_is_still_accepted(server, images_root):
    """הצד החיובי: התיקון אינו דוחה כל קליטה."""
    tid, image_id, r = _capture(server, {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B},
                                manifest_for())

    assert r.status_code == 202, r.text
    assert wait_verified(server, tid) == "done"
    assert (images_root / image_id / "manifest.json").is_file()
