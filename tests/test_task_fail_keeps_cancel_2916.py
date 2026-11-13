"""#2916 §2 — ביטול שהקדים את הכשל נשאר ביטול.

**התרחיש:** הסוכן שולח את המניפסט, ובאותו רגע המפעיל מבטל. הביטול מוחק את תיקיית
ה-staging, ובדיקת המניפסט שכבר רצה מוצאת "קובץ חסר" → `_fail`. `_fail` היה
`UPDATE ... WHERE id = ?` בלי תנאי מצב, ולכן `cancelled` הפך ל-`failed` עם סיבה מטעה.
אותו `_fail` בלי תנאי יושב גם ב-`server/rounds/direct.py` (המניפסט החי).

**התיקון:** `WHERE state IN ('pending','running')`, כמו ב-#535 — ורק כשהמעבר קרה נרשמים
האזור שנמחק והיומן. המרוץ מדומה בנקודה היחידה שבה הוא קורה: בדיקת המניפסט שרצה אחרי
שהשער עבר מבצעת את הביטול (מה שה-handler של `/cancel` עושה: מצב + מחיקת התיקייה) ורק אז
ממשיכה.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

import server.library.capture as capmod
import server.rounds.direct as directmod
from server.core.tasks import TOKEN_HEADER, staging_dir
from test_capture import PART_A, make_task, manifest_for, setup_build_machine, task_token
from test_direct_server import (  # noqa: F401 -- fixture
    _live_manifest, _put_manifest as _put_live_manifest, _setup, room_server)


def _state(conn, task_id) -> str:
    return conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()["state"]


def _events(conn, event) -> list[str]:
    return [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


# --- קליטה ---------------------------------------------------------------------------


def _capture_cancelled_in_the_gate(server, images_root, monkeypatch):
    """הקליטה: מחיצה אחת הגיעה, המניפסט נשלח — והביטול נכנס אחרי השער, לפני הבדיקה."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    token = task_token(server, task_id)
    assert server["anon"].put(f"/api/v1/capture/{task_id}/files/p1.esp.pcl.zst", content=PART_A,
                              headers={TOKEN_HEADER: token}).status_code == 200
    conn = server["ctx"].conn
    real = capmod.manifest_problem

    def cancel_then_check(manifest, folder):
        # מה ש-`POST /tasks/{id}/cancel` עושה: מצב `cancelled` ומחיקת ה-staging
        conn.execute("UPDATE tasks SET state = 'cancelled' WHERE id = ?"
                     " AND state IN ('pending', 'running')", (task_id,))
        conn.commit()
        capmod.shutil.rmtree(staging_dir(images_root, task_id), ignore_errors=True)
        return real(manifest, folder)

    monkeypatch.setattr(capmod, "manifest_problem", cancel_then_check)
    r = server["anon"].put(f"/api/v1/capture/{task_id}/manifest",
                           content=json.dumps(manifest_for()).encode(),
                           headers={TOKEN_HEADER: token})
    return task_id, r


def test_a_cancel_that_beat_the_manifest_check_stays_cancelled(server, images_root, monkeypatch):
    task_id, r = _capture_cancelled_in_the_gate(server, images_root, monkeypatch)
    conn = server["ctx"].conn
    assert r.status_code == 400, r.text          # המניפסט עדיין מסורב — אין מה לקלוט
    assert _state(conn, task_id) == "cancelled", "הביטול נדרס ל-failed"
    assert conn.execute("SELECT error FROM tasks WHERE id = ?", (task_id,)).fetchone()["error"] is None
    assert not any(task_id in d for d in _events(conn, "capture_failed")), \
        "נרשם capture_failed למשימה שבוטלה"


def test_a_real_failure_of_an_open_capture_is_still_failed(server, images_root):
    """השומר מפני תיקון-יתר: משימה פתוחה שהמניפסט שלה פגום נכשלת, והאזור נמחק."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    token = task_token(server, task_id)
    assert server["anon"].put(f"/api/v1/capture/{task_id}/files/p1.esp.pcl.zst", content=PART_A,
                              headers={TOKEN_HEADER: token}).status_code == 200
    r = server["anon"].put(f"/api/v1/capture/{task_id}/manifest", content=b"{not json",
                           headers={TOKEN_HEADER: token})
    conn = server["ctx"].conn
    assert r.status_code == 400
    assert _state(conn, task_id) == "failed"
    assert not staging_dir(images_root, task_id).exists()
    assert any(task_id in d for d in _events(conn, "capture_failed"))


# --- שידור ישיר ----------------------------------------------------------------------


def test_a_cancel_that_beat_the_live_manifest_check_stays_cancelled(room_server, monkeypatch):
    opened = _setup(room_server)
    task_id = opened["task_id"]
    conn = room_server["ctx"].conn
    real = directmod.validate_live_manifest

    def cancel_then_check(manifest):
        conn.execute("UPDATE tasks SET state = 'cancelled' WHERE id = ?"
                     " AND state IN ('pending', 'running')", (task_id,))
        conn.commit()
        return real(manifest)

    monkeypatch.setattr(directmod, "validate_live_manifest", cancel_then_check)
    r = _put_live_manifest(room_server, task_id, manifest=_live_manifest(sha="not-a-sha"))
    assert r.status_code == 400 and "sha256" in r.json()["detail"]
    assert _state(conn, task_id) == "cancelled", "הביטול נדרס ל-failed"
    assert _events(conn, "direct_failed") == [], "נרשם direct_failed למשימה שבוטלה"
