"""‏#1470 + ‏#1461: האימות של קליטה רץ ברקע, והשרת ממשיך לענות בזמנו.

הכרעת נדב 16/10 (עיקרון 6 נשאר): מחשב הבנייה משתחרר מיד אחרי ההעלאה —
‏`PUT /manifest` עונה 202 אחרי הבדיקות הזולות — והקריאה החוזרת של כל קובץ
מדיסק השרת רצה ברקע. האימג' **אינו** בספרייה עד שהיא עוברת; כישלון הוא
‏`failed` בשם; אתחול באמצע מריץ אותה מחדש ולעולם אינו מכניס בלי אימות.
בנוסף, ה-sha256 שהשרת חישב **בזמן הקבלה** מושווה למניפסט בתוך ה-PUT.

‏#1461: עד כאן `finish_capture` גיבב את כל הקבצים על לולאת האירועים, וכל
השרת — hello, דיווחים, קונסולה — לא ענה למשך האימות (17.5 מתוך 45 דקות
ב-17/09). הבדיקה הראשונה מריצה את ה-PUT ואת ה-hello **על לולאה אחת**,
כמו uvicorn בייצור (‏`server/main.py`), ולא דרך TestClient — ‏TestClient
בלי `with` פותח לולאה חדשה לכל בקשה, ושם הקיפאון אינו נראה.

הגיבוב מוחלף בשער (`Gate`) שמחזיק אותו עד שהבדיקה משחררת — כך "אימות
ארוך" הוא מצב דטרמיניסטי ולא תלוי בגודל קובץ או במהירות דיסק.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
import time

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from server import capture  # noqa: E402

try:
    from server.reconcile import RESTARTED_VERIFY  # noqa: E402
except ImportError:   # בקרה שלילית: בלי התיקון הטסטים רצים ונופלים על ההתנהגות, לא על ImportError
    RESTARTED_VERIFY = "השרת עלה מחדש — קבצי הקליטה לא נמצאו לאימות; האימג' לא נכנס לספרייה"
from server.tasks import TOKEN_HEADER, staging_dir  # noqa: E402

from conftest import hello_body  # noqa: E402
from test_capture import (PART_A, PART_B, make_task, manifest_for, setup_build_machine,  # noqa: E402
                          sha, task_token, wait_verified)

FILES = {"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B}
REAL_SHA256 = capture._sha256
#: ‏`getattr` ולא `capture.UPLOAD_SHA`: בבקרה השלילית הקבוע אינו קיים, והטסט
#: חייב ליפול על ההתנהגות ולא על AttributeError.
UPLOAD_SHA = getattr(capture, "UPLOAD_SHA", ".upload-sha256")


class Gate:
    """תחליף ל-`capture._sha256` שמחזיק את הגיבוב עד `release`.

    מדווח את גודל הקובץ ל-`on_bytes` **לפני** ההמתנה, כדי שההתקדמות תיראה
    בזמן שהאימות "רץ". ‏`order` — רישום סדר האירועים (שעון ווינדוס גס מכדי
    להכריע "לפני" בין שני אירועים באותו טיק של 15ms)."""

    def __init__(self, wait_s: float = 8.0) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.order: list[str] = []
        self.wait_s = wait_s

    def __call__(self, path, on_bytes=None):
        # #2099: progress first, then `entered` -- the test reads the row as soon as
        # `entered` fires; set the other way round, a busy host let it read before
        # on_bytes wrote (verify_bytes 0 != 19, in a parallel run).
        if on_bytes is not None:
            on_bytes(path.stat().st_size)
        self.entered.set()
        self.release.wait(self.wait_s)
        self.order.append("released")
        return REAL_SHA256(path)


def _upload(server, task_id: str, files=None) -> dict:
    head = {TOKEN_HEADER: task_token(server, task_id)}
    for name, data in (files or FILES).items():
        r = server["anon"].put(f"/api/v1/capture/{task_id}/files/{name}",
                               content=data, headers=head)
        assert r.status_code == 200, r.text
    return head


def _put_manifest(server, task_id: str, head: dict, manifest=None):
    return server["anon"].put(f"/api/v1/capture/{task_id}/manifest",
                              content=json.dumps(manifest or manifest_for()).encode(),
                              headers=head)


def _task_row(server, task_id: str) -> dict:
    return next(t for t in server["admin"].get("/api/console/tasks").json()
                if t["id"] == task_id)


def _journal(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ?", (event,))]


# --- #1461: השרת עונה בזמן האימות ---------------------------------------------


def test_hello_is_answered_on_the_same_event_loop_while_a_capture_verifies(server, monkeypatch):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid = created["id"]
    head = _upload(server, tid)
    gate = Gate(wait_s=6.0)
    monkeypatch.setattr(capture, "_sha256", gate)
    body = json.dumps(manifest_for()).encode()

    async def scenario():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            put = asyncio.create_task(
                client.put(f"/api/v1/capture/{tid}/manifest", content=body, headers=head))
            # האימות התחיל (בקוד הישן — על הלולאה עצמה, ואז ההמתנה הזו
            # חוזרת רק אחרי שהגיבוב השתחרר).
            assert await asyncio.to_thread(gate.entered.wait, 10)
            hello = await client.post("/api/v1/agent/hello", json=hello_body(mac))
            gate.order.append("hello")
            gate.release.set()
            return hello, await put

    hello, put = asyncio.run(scenario())
    # #2099: the PUT answered 202 when the verify began; "released" is appended by the
    # verify thread after `release` -- on a busy host the order was read before it ran
    # (['hello']). Wait for it, bounded; the old bug (#1461) still reads released-first.
    for _ in range(200):
        if "released" in gate.order:
            break
        time.sleep(0.05)
    assert hello.status_code == 200, hello.text
    assert gate.order[:2] == ["hello", "released"], (gate.order,
        "ה-hello נענה רק אחרי שהגיבוב השתחרר — השרת קפא לכל משך האימות (#1461)")
    assert put.status_code == 202, put.text
    # מחשב הבנייה שוחרר: ה-hello כבר אינו מוסר לו את המשימה.
    assert hello.json().get("task") is None, hello.json()
    assert wait_verified(server, tid) == "done"


# --- #1470: האימג' בחוץ עד שהאימות עובר ---------------------------------------


def test_while_verifying_the_image_is_out_of_the_library_and_the_console_shows_progress(
        server, images_root, monkeypatch):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid, image_id = created["id"], created["image_id"]
    head = _upload(server, tid)
    gate = Gate()
    monkeypatch.setattr(capture, "_sha256", gate)
    monkeypatch.setattr(capture, "VERIFY_PROGRESS_EVERY_S", 0.0, raising=False)
    try:
        r = _put_manifest(server, tid, head)
        assert r.status_code == 202, r.text
        assert r.json() == {"ok": True, "image_id": image_id, "state": "verifying"}
        assert gate.entered.wait(10)

        row = _task_row(server, tid)
        assert row["state"] == "verifying" and row["stage"] == "verify", row
        assert row["verify_total"] == len(PART_A) + len(PART_B), row
        assert row["verify_bytes"] == len(PART_A), row
        # לא בספרייה בשום צורה: לא בדיסק, לא ברשימה, לא נמשך.
        assert not (images_root / image_id).exists()
        listed = server["admin"].get("/api/console/images").json()
        assert all(i["id"] != image_id for i in listed), listed
        assert server["anon"].get(f"/api/v1/images/{image_id}/manifest").status_code == 404
        assert any(tid in d for d in _journal(server["ctx"], "capture_verifying"))
    finally:
        gate.release.set()

    assert wait_verified(server, tid) == "done"
    assert (images_root / image_id / "manifest.json").is_file()
    done = _task_row(server, tid)
    assert done["verify_bytes"] == done["verify_total"] == len(PART_A) + len(PART_B)
    # רשומות ה-sha מזמן הקבלה אינן נכנסות לספרייה.
    assert not list((images_root / image_id).glob("*" + UPLOAD_SHA))
    assert any("verified" in d for d in _journal(server["ctx"], "capture_done"))


def test_a_file_that_reads_back_wrong_from_disk_keeps_the_image_out_by_name(
        server, images_root, monkeypatch):
    """הקריאה החוזרת מהדיסק היא מה שתופס כתיבה פגומה לדיסק השרת — הבדיקה
    בזמן הקבלה לא רואה אותה. כישלון בשם, ביומן, ואזור הביניים נמחק."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid, image_id = created["id"], created["image_id"]
    head = _upload(server, tid)

    def bad_disk(path, on_bytes=None):
        if path.name == "p3.windows.pcl.zst":
            return "0" * 64
        return REAL_SHA256(path) if on_bytes is None else REAL_SHA256(path, on_bytes)

    monkeypatch.setattr(capture, "_sha256", bad_disk)
    assert _put_manifest(server, tid, head).status_code == 202
    assert wait_verified(server, tid) == "failed"
    row = _task_row(server, tid)
    assert "sha256 mismatch on p3.windows.pcl.zst" in row["error"], row
    assert "server disk" in row["error"], row
    assert not (images_root / image_id).exists()
    assert not staging_dir(images_root, tid).exists()
    assert any(tid in d for d in _journal(server["ctx"], "capture_failed"))


# --- #1470: ה-sha256 מזמן הקבלה ------------------------------------------------


def test_bytes_that_changed_on_disk_after_receipt_are_refused_in_the_put(server, images_root):
    """הקובץ בדיסק תואם את המניפסט — אבל אינו מה שהתקבל. הקריאה החוזרת
    לבדה הייתה מקבלת אותו; ה-sha מזמן הקבלה תופס אותו, בתוך ה-PUT."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid, image_id = created["id"], created["image_id"]
    head = _upload(server, tid)
    other = b"not-what-was-received"
    (staging_dir(images_root, tid) / "p3.windows.pcl.zst").write_bytes(other)
    manifest = manifest_for()
    manifest["partitions"][1]["sha256"] = sha(other)

    r = _put_manifest(server, tid, head, manifest)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "sha256 mismatch on p3.windows.pcl.zst (the bytes the server received)"
    assert _task_row(server, tid)["state"] == "failed"
    assert not (images_root / image_id).exists()


def test_a_missing_receipt_record_fails_the_put_and_is_not_skipped(server, images_root):
    """"לא הצלחנו לבדוק" אינו "בדקנו" (עיקרון 5)."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid = created["id"]
    head = _upload(server, tid)
    (staging_dir(images_root, tid) / ("p1.esp.pcl.zst" + UPLOAD_SHA)).unlink()

    r = _put_manifest(server, tid, head)
    assert r.status_code == 400, r.text
    assert "p1.esp.pcl.zst was not recorded" in r.json()["detail"]
    assert _task_row(server, tid)["state"] == "failed"


def test_the_upload_answer_still_carries_the_received_sha_and_records_it(server, images_root):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    r = server["anon"].put(f"/api/v1/capture/{tid}/files/p1.esp.pcl.zst", content=PART_A,
                           headers={TOKEN_HEADER: task_token(server, tid)})
    assert r.json() == {"ok": True, "sha256": sha(PART_A)}
    record = staging_dir(images_root, tid) / ("p1.esp.pcl.zst" + UPLOAD_SHA)
    assert record.read_text(encoding="ascii") == hashlib.sha256(PART_A).hexdigest()


# --- הסוכן משוחרר: `verifying` אינו פתוח --------------------------------------


def test_the_agents_final_report_after_the_manifest_gets_not_open(server, monkeypatch):
    """‏`report_final` של הסוכן מקבל `not_open` אחרי שהמניפסט סגר את המשימה
    (#127) — וזה חייב להישאר כך גם כשהמשימה `verifying`, אחרת הסוכן
    ממתין או נכשל על משימה שהשרת כבר לקח ממנו."""
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    head = _upload(server, tid)
    gate = Gate()
    monkeypatch.setattr(capture, "_sha256", gate)
    try:
        assert _put_manifest(server, tid, head).status_code == 202
        assert gate.entered.wait(10)
        r = server["anon"].post("/api/v1/agent/progress", headers=head, json={
            "task_id": tid, "mac": mac, "state": "done",
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 0, "state": "done"}]})
        assert r.json()["code"] == "not_open", r.text
        assert _task_row(server, tid)["state"] == "verifying"
        # ואין לו משימה פתוחה: קליטה חדשה מאותה מכונה מותרת.
        assert make_task(server, mac, name="הבאה").status_code == 200
    finally:
        gate.release.set()
    assert wait_verified(server, tid) == "done"


# --- #1470: אתחול באמצע האימות ------------------------------------------------


def _reboot(tmp_path, images_root, clock):
    """שרת שני על אותו `--data-dir` ואותה ספרייה — "עלייה מחדש"."""
    from server.app import create_app
    from test_sender import Recorder
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, sender_runner=Recorder(block=True),
                     health_hooks={"interfaces": lambda: []})
    return app.state.ctx


def _state_after_restart(ctx, tid: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        row = ctx.conn.execute("SELECT state, error FROM tasks WHERE id = ?", (tid,)).fetchone()
        if row["state"] != "verifying":
            return dict(row)
        assert time.monotonic() < deadline, f"{tid} עדיין verifying אחרי האתחול"
        time.sleep(0.02)


def _died_while_verifying(server, monkeypatch) -> tuple[str, str]:
    """קליטה שה-PUT שלה ענה 202 והשרת מת לפני שהאימות סיים: המשימה
    ‏`verifying`, הקבצים באזור הביניים, ואף אחד לא מאמת."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    head = _upload(server, created["id"])
    real = getattr(capture, "start_verification", None)
    monkeypatch.setattr(capture, "start_verification", lambda *a: None, raising=False)
    assert _put_manifest(server, created["id"], head).status_code == 202
    monkeypatch.setattr(capture, "start_verification", real, raising=False)
    server["ctx"].sender.stop()
    return created["id"], created["image_id"]


def test_a_restart_mid_verification_verifies_again_and_only_then_admits(
        server, tmp_path, images_root, clock, monkeypatch):
    tid, image_id = _died_while_verifying(server, monkeypatch)
    assert not (images_root / image_id).exists()
    gate = Gate()
    monkeypatch.setattr(capture, "_sha256", gate)
    try:
        ctx = _reboot(tmp_path, images_root, clock)
        # האימות רץ מחדש — ובזמן שהוא רץ האימג' עדיין בחוץ.
        assert gate.entered.wait(10), "האימות לא הופעל מחדש בעלייה"
        assert not (images_root / image_id).exists()
    finally:
        gate.release.set()
    assert _state_after_restart(ctx, tid)["state"] == "done"
    assert (images_root / image_id / "manifest.json").is_file()
    assert any("verifying again" in d for d in _journal(ctx, "capture_verifying"))
    ctx.sender.stop()


def test_a_restart_never_admits_a_staged_image_that_does_not_verify(
        server, tmp_path, images_root, clock, monkeypatch):
    tid, image_id = _died_while_verifying(server, monkeypatch)
    (staging_dir(images_root, tid) / "p3.windows.pcl.zst").write_bytes(b"rotted on disk")
    ctx = _reboot(tmp_path, images_root, clock)
    row = _state_after_restart(ctx, tid)
    assert row["state"] == "failed" and "sha256 mismatch on p3.windows.pcl.zst" in row["error"]
    assert not (images_root / image_id).exists()
    ctx.sender.stop()


def test_a_restart_with_nothing_left_to_verify_fails_by_name(
        server, tmp_path, images_root, clock, monkeypatch):
    tid, image_id = _died_while_verifying(server, monkeypatch)
    shutil.rmtree(staging_dir(images_root, tid))
    ctx = _reboot(tmp_path, images_root, clock)
    row = _state_after_restart(ctx, tid)
    assert row == {"state": "failed", "error": RESTARTED_VERIFY}
    assert not (images_root / image_id).exists()
    ctx.sender.stop()


def test_an_image_that_landed_before_the_restart_is_done(
        server, tmp_path, images_root, clock, monkeypatch):
    """‏rename קורה רק אחרי שכל sha256 תאם; נפילה בינו לבין ה-UPDATE
    משאירה אימג' מאומת בספרייה עם משימה `verifying` — ‏`done`, לא כישלון."""
    tid, image_id = _died_while_verifying(server, monkeypatch)
    staging_dir(images_root, tid).rename(images_root / image_id)
    ctx = _reboot(tmp_path, images_root, clock)
    assert _state_after_restart(ctx, tid) == {"state": "done", "error": None}
    ctx.sender.stop()
