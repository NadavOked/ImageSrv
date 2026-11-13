"""#1830: קובץ מחיצה ריק עם גיבוב "תואם" אינו נכנס לספרייה.

הסוכן מגבב את מה שהוא שלח. זרם שנקטע לפני הבייט הראשון יוצא קובץ של אפס
בייט, וה-sha256 שלו (של מחרוזת ריקה) תואם בדיוק את מה שהשרת קיבל — כל
בדיקת גיבוב עוברת. עד #1830 `manifest_problem` בדק רק שהקובץ **קיים**.

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


# --- ראיה חיובית לנפח מול used_bytes (הכרעת נדב 01/11) ------------------------
#
# קובץ שאינו ריק עדיין יכול להיות זרם שנקטע: כותרת של partclone ומעט בלוקים,
# עם גיבוב "תואם" כי הסוכן גיבב את מה שנשלח. הסף (`MIN_STREAM_TO_USED`, 1%)
# נגזר ממדידה — ההערה בקוד נוקבת במקור.


def _with_used(used):
    manifest = manifest_for()
    manifest["partitions"][1]["used_bytes"] = used
    return manifest


def test_a_stream_far_smaller_than_what_the_partition_holds_is_refused_by_name(
        server, images_root):
    """p3 נושא 21 בייט, ובמחיצה תפוסים 10,000: 0.21% — מתחת ל-1%.

    **בקרה שלילית:** על הקוד הישן (רק אפס בייט נדחה) — 202, והאימג' נכנס."""
    tid, image_id, r = _capture(server, {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B},
                                _with_used(10_000))

    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail.startswith("partition file is too small for what the partition holds:"
                             " p3.windows.pcl.zst"), detail
    assert f"is {len(PART_B)} bytes" in detail and "10000 used bytes" in detail, detail
    # גלוי בקונסולה: המשימה `failed` והסיבה בשמה, כמו בכל סירוב של הקליטה.
    row = _state(server, tid)
    assert row["state"] == "failed"
    assert "too small" in row["error"] and "p3.windows.pcl.zst" in row["error"]
    assert not (images_root / image_id).exists()


def test_a_stream_at_one_percent_of_the_used_space_is_accepted(server, images_root):
    """הגבול: 21 בייט מול 2,100 תפוסים = 1% בדיוק — עובר. הסף אינו דוחה
    כל קליטה שהדחיסה שלה טובה."""
    tid, _image_id, r = _capture(
        server, {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B},
        _with_used(len(PART_B) * 100))
    assert r.status_code == 202, r.text
    assert wait_verified(server, tid) == "done"


@pytest.mark.parametrize("used", [None, 0])
def test_without_a_measured_used_space_only_the_empty_file_rule_applies(
        server, images_root, used):
    """`null` = לא נמדד (partclone.dd, מחיצה שלא נעגנה); 0 = נמדד וריק. בשניהם
    אין מכנה ליחס — הקליטה אינה נדחית בגללו (וקובץ ריק עדיין נדחה, למעלה)."""
    tid, _image_id, r = _capture(
        server, {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B}, _with_used(used))
    assert r.status_code == 202, r.text
    assert wait_verified(server, tid) == "done"
