"""קליטה שנכשלה אינה משאירה את אזור העבודה שלה — ‏#1915.

**מה נמצא (BM-F04z, 20/10):** ‏zstd נהרג ב-4% ממחיצה 3. המשימה נרשמה
‏`failed`, אבל `.capture-tsk_310a` נשאר על הדיסק עם 5.2GB. הסחיפה
(‏`work_areas.sweep`) רצה רק בעליית השרת ואחר כך כל 24 שעות. כשהכשל הוא
**דיסק מלא** (507), השרת שומר בדיוק את הזבל שגרם לו.

**שתי דלתות מובילות ל-`failed`, ושתיהן נבדקות כאן:**

1. ‏`capture._fail` — כל סירוב שהשרת מכריע בעצמו: אין מקום לפני הבייט
   הראשון (507), ‏ENOSPC באמצע הזרם (507), אזור שנעלם, מזהה אימג' פגום,
   מניפסט שאינו JSON, יעד מחוץ לשורש, אימג' שכבר קיים.
2. **דיווח `failed` של הסוכן** (‏`/agent/progress`) — זו הדלת שבה BM-F04z
   עבר בפועל: ההעלאה של p3 הסתיימה כרגיל כשה-fifo נסגר, והסוכן דיווח
   ‏`failed` על `zstd rc=137`. ‏`_fail` אינו במסלול הזה בכלל, ולכן תיקון
   של `_fail` לבדו לא היה סוגר את מה שנמצא על ברזל.

כל טסט מעלה קודם מחיצה אמיתית, כדי שיהיה מה למחוק — אזור ריק שלא נמחק
נראה בדיוק כמו אזור שנמחק.
"""

from __future__ import annotations

import errno
import json
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.core.tasks import TOKEN_HEADER, staging_dir
from test_capture import (
    PART_A, PART_B, make_task, manifest_for, setup_build_machine, task_token,
)


def _put(server, task_id, name, data):
    return server["anon"].put(
        f"/api/v1/capture/{task_id}/files/{name}", content=data,
        headers={TOKEN_HEADER: task_token(server, task_id)})


def _put_manifest(server, task_id, raw: bytes):
    return server["anon"].put(
        f"/api/v1/capture/{task_id}/manifest", content=raw,
        headers={TOKEN_HEADER: task_token(server, task_id)})


def _started(server, images_root) -> tuple[str, Path]:
    """משימה שמחיצה אחת שלה כבר הגיעה: יש אזור עבודה, ויש בו בייטים."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    assert _put(server, task_id, "p1.esp.pcl.zst", PART_A).status_code == 200
    folder = staging_dir(images_root, task_id)
    assert (folder / "p1.esp.pcl.zst").read_bytes() == PART_A, "אין מה למחוק — הטסט לא בודק כלום"
    return task_id, folder


def _state(server, task_id) -> str:
    return server["ctx"].conn.execute(
        "SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()["state"]


# --- דלת 1: ‏_fail, כל מסלול --------------------------------------------------


def test_no_room_before_the_first_byte_removes_the_work_area(
        server, images_root, monkeypatch):
    """‏507 לפני הבייט הראשון של מחיצה 3 — ומחיצה 1 כבר יושבת באזור."""
    import server.library.capture as capmod
    task_id, folder = _started(server, images_root)
    fake = types.SimpleNamespace(total=10 ** 12, used=10 ** 12 - 4, free=4)
    monkeypatch.setattr(capmod.shutil, "disk_usage", lambda path: fake)

    r = _put(server, task_id, "p3.windows.pcl.zst", PART_B)

    assert r.status_code == 507, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), f"דיסק מלא, והזבל שמילא אותו נשאר: {list(folder.iterdir())}"


def test_a_disk_that_fills_mid_stream_removes_the_work_area(
        server, images_root, monkeypatch):
    """‏507 מתוך הזרם (ENOSPC) — המקרה החמור של #1915: השרת נגמר לו המקום,
    והאזור שגרם לזה הוא בדיוק מה שנשאר."""
    task_id, folder = _started(server, images_root)
    orig_open = Path.open

    class _Full:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, chunk):
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "open", lambda self, *a, **k: _Full()
                        if self.name == "p3.windows.pcl.zst" else orig_open(self, *a, **k))

    r = _put(server, task_id, "p3.windows.pcl.zst", PART_B)

    assert r.status_code == 507, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())


def test_a_receive_record_that_cannot_be_written_removes_the_work_area(
        server, images_root, monkeypatch):
    """‏`_StagingGone` עם משימה פתוחה (‏500 בשם): רשומת ה-sha256 של מה
    שהתקבל לא נכתבה. כאן הנתיב שלה מזויף אל תת-תיקייה שאינה קיימת — כך
    המסלול נבחר בלי למחוק תיקייה שקובץ פתוח בתוכה (ווינדוס אינו מרשה)."""
    import server.library.capture as capmod
    task_id, folder = _started(server, images_root)
    monkeypatch.setattr(capmod, "upload_sha_path",
                        lambda path: path.parent / "missing" / (path.name + ".sha256"))

    r = _put(server, task_id, "p3.windows.pcl.zst", PART_B)

    assert r.status_code == 500, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())


def test_a_malformed_image_id_removes_the_work_area(server, images_root):
    task_id, folder = _started(server, images_root)
    conn = server["ctx"].conn
    conn.execute("UPDATE tasks SET image_id = ? WHERE id = ?", ("../evil", task_id))
    conn.commit()

    r = _put_manifest(server, task_id, json.dumps(manifest_for()).encode())

    assert r.status_code == 500, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())


def test_a_manifest_that_is_not_json_removes_the_work_area(server, images_root):
    task_id, folder = _started(server, images_root)

    r = _put_manifest(server, task_id, b"{not json")

    assert r.status_code == 400, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())


def test_a_target_outside_the_library_removes_the_work_area(
        server, images_root, monkeypatch):
    """‏`inside` מחזיר None — מצב שאינו אמור לקרות, ולכן מזויף."""
    import server.library.capture as capmod
    task_id, folder = _started(server, images_root)
    assert _put(server, task_id, "p3.windows.pcl.zst", PART_B).status_code == 200
    monkeypatch.setattr(capmod, "inside", lambda *a, **k: None)

    r = _put_manifest(server, task_id, json.dumps(manifest_for()).encode())

    assert r.status_code == 500, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())


def test_an_image_that_already_exists_removes_only_the_work_area(server, images_root):
    """‏409: האזור נמחק, **והאימג' הקיים לא נגעו בו**."""
    task_id, folder = _started(server, images_root)
    assert _put(server, task_id, "p3.windows.pcl.zst", PART_B).status_code == 200
    image_id = server["ctx"].conn.execute(
        "SELECT image_id FROM tasks WHERE id = ?", (task_id,)).fetchone()["image_id"]
    existing = images_root / image_id
    existing.mkdir()
    (existing / "keep.txt").write_text("not ours", encoding="utf-8")

    r = _put_manifest(server, task_id, json.dumps(manifest_for()).encode())

    assert r.status_code == 409, r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), list(folder.iterdir())
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "not ours"


# --- דלת 2: הסוכן מדווח failed — מה שקרה ב-BM-F04z ---------------------------


def _report(server, task_id, state):
    mac = server["ctx"].conn.execute(
        "SELECT mac FROM tasks WHERE id = ?", (task_id,)).fetchone()["mac"]
    return server["anon"].post("/api/v1/agent/progress", json={
        "task_id": task_id, "mac": mac, "state": state,
        "targets": [{"dev": "sda", "bytes_written": len(PART_A), "bytes_total": 0,
                     "state": state, "error": "מחיצה 3: הקליטה נכשלה (שלב=zstd rc=137)"}]},
        headers={TOKEN_HEADER: task_token(server, task_id)})


def test_an_agent_reported_failure_removes_the_work_area(server, images_root):
    """**לב #1915 כפי שקרה על ברזל:** ההעלאה הסתיימה כרגיל, והסוכן דיווח
    ‏`failed`. ‏`_fail` אינו במסלול הזה."""
    task_id, folder = _started(server, images_root)

    r = _report(server, task_id, "failed")

    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _state(server, task_id) == "failed"
    assert not folder.exists(), f"הקליטה נכשלה והאזור נשאר: {list(folder.iterdir())}"


def test_a_running_report_keeps_the_work_area(server, images_root):
    """השומר מפני תיקון-יתר: דיווח התקדמות רגיל אינו נוגע בבייטים
    שכבר הגיעו — הקליטה ממשיכה, והם חלק ממנה."""
    task_id, folder = _started(server, images_root)

    r = _report(server, task_id, "capturing")

    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _state(server, task_id) == "running"
    assert (folder / "p1.esp.pcl.zst").read_bytes() == PART_A
