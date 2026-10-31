"""‏#737: ה-uploader של phase_capture שולח X-Imagesrv-Task-Token.

בלי הכותרת `PUT /api/v1/capture/.../files` מחזיר 401, וה-e2e נעצר
לפני שמסלול הקליטה באמת נבדק. הסוכן האמיתי שולח אותה מ-hello;
הסימולציה צריכה את אותו דבר.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

from tools.e2e.harness import Client, FILES_A, GB256
from tools.e2e.phase_capture import _capture


class _Resp:
    """תשובת urlopen מינימלית — status + body, בלי רשת."""

    def __init__(self, body: bytes = b"{}", status: int = 200):
        self.status = status
        self.headers = {}
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _token_from(req) -> str | None:
    value = req.get_header("X-imagesrv-task-token")
    if value is not None:
        return value
    for key, val in req.header_items():
        if key.lower() == "x-imagesrv-task-token":
            return val
    return None


def test_phase_capture_uploader_sends_the_task_token(monkeypatch, tmp_path):
    """הכותרת מגיעה ל-HTTP של PUT /files ו-PUT /manifest, בערך מ-hello."""
    token = "ab" * 24
    task = {"id": "ab12", "image_id": "img-737"}
    images = tmp_path / "images"
    images.mkdir()
    staging = images / ".capture-ab12"
    seen: list[dict] = []
    #: ‏#1470: אחרי המניפסט המשימה עוברת ל-verifying ואז done (האימות ברקע).
    state = {"task": "running"}

    builder = SimpleNamespace(
        mac="aa:bb:cc:00:00:10",
        answer={"task": {"id": task["id"], "token": token}, "session": None},
    )

    def console_json(method, path, body=None):
        if method == "POST" and path.endswith("/capture"):
            return 200, task
        if method == "GET" and path.endswith("/tasks"):
            return 200, [{"id": task["id"], "state": state["task"],
                          "bytes_written": 1}]
        return 200, {}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        seen.append({"method": req.get_method(), "url": url,
                     "token": _token_from(req)})
        if "/files/" in url:
            staging.mkdir(exist_ok=True)
            return _Resp(b"")
        if "/manifest" in url:
            dest = images / task["image_id"]
            dest.mkdir(exist_ok=True)
            (dest / "manifest.json").write_text("{}", encoding="utf-8")
            if staging.exists():
                shutil.rmtree(staging)
            builder.answer = {"task": None, "session": None}
            state["task"] = "done"
            # ‏#1470: ‏202 — המניפסט התקבל והאימות רץ ברקע
            return _Resp(json.dumps({"ok": True, "state": "verifying"}).encode(), status=202)
        return _Resp(json.dumps({"ok": True}).encode())

    monkeypatch.setattr("tools.e2e.harness.urllib.request.urlopen", fake_urlopen)

    ctx = SimpleNamespace(
        console=SimpleNamespace(json=console_json),
        builder=builder,
        images_dir=images,
    )
    _capture(ctx, "Windows 11 Base", "Office", FILES_A, GB256)

    uploads = [c for c in seen
               if "/capture/" in c["url"]
               and ("/files/" in c["url"] or "/manifest" in c["url"])]
    assert uploads, f"לא נרשמה העלאה: {seen}"
    missing = [c for c in uploads if c["token"] != token]
    assert not missing, missing
    assert any("/files/" in c["url"] for c in uploads), uploads
    assert any("/manifest" in c["url"] for c in uploads), uploads


def test_client_does_not_invent_a_task_token(monkeypatch):
    """רדיוס הפגיעה: לקוח בלי אסימון אינו ממציא את הכותרת מעצמו."""
    seen: list[dict] = []

    def fake_urlopen(req, timeout=None):
        seen.append({"token": _token_from(req)})
        return _Resp(b"")

    monkeypatch.setattr("tools.e2e.harness.urllib.request.urlopen", fake_urlopen)
    status, _ = Client().request("GET", "/api/v1/agent/hello")
    assert status == 200
    assert seen, "Client.request לא פנה ל-urlopen"
    assert seen[0]["token"] is None, seen[0]
