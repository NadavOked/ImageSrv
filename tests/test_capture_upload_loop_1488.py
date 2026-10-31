"""‏#1488: ההעלאה של קובץ מחיצה אינה חוסמת את לולאת האירועים.

‏`stream_to` כתב וגיבב כל chunk ישירות בתוך `async for` — על הלולאה
שמשותפת לסוכנים ולקונסולה (‏`server/main.py`). דיסק שרת איטי הפך כל
`write` להמתנה של כל השרת. אותה משפחה כמו #1461, בשלב ההעלאה.

הבדיקה מריצה את ההעלאה, hello ובקשת קונסולה **על לולאה אחת** (httpx
ASGITransport, כמו `test_capture_verify_1470.py`). הגיבוב של ההעלאה מוחזק
בשער, ולכן "כתיבה ארוכה" היא מצב דטרמיניסטי ולא תלוי במהירות דיסק. השער
יושב על `hashlib.sha256` של המודול — שני הקודים (הישן והחדש) קוראים
ל-`update`, ולכן הבקרה השלילית נכשלת על ההתנהגות ולא על שם שאינו קיים.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import types

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from server.library import capture  # noqa: E402
from server.core.tasks import TOKEN_HEADER, staging_dir  # noqa: E402

from conftest import hello_body  # noqa: E402
from test_capture import make_task, setup_build_machine, task_token  # noqa: E402

#: יותר מחוצץ אחד של 1MiB, ולא כפולה שלו — גם הזנב נכתב.
BODY = bytes(range(256)) * (5 * 4096 + 7)


class GatedSha:
    """‏`hashlib` מזויף: ה-`update` הראשון ממתין לשער, השאר עוברים."""

    def __init__(self, wait_s: float = 6.0) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.order: list[str] = []
        self.wait_s = wait_s
        self.module = types.SimpleNamespace(sha256=self._new)

    def _new(self):
        gate, real = self, hashlib.sha256()

        class Digest:
            def update(self, data):
                if not gate.release.is_set():
                    gate.entered.set()
                    gate.release.wait(gate.wait_s)
                    gate.order.append("released")
                    gate.release.set()
                real.update(data)

            def hexdigest(self):
                return real.hexdigest()
        return Digest()


def test_hello_and_the_console_are_answered_while_an_upload_is_writing(server, monkeypatch):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    head = {TOKEN_HEADER: task_token(server, tid)}
    gate = GatedSha()
    monkeypatch.setattr(capture, "hashlib", gate.module)

    async def scenario():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver",
                                     cookies=dict(server["admin"].cookies)) as client:
            upload = asyncio.create_task(client.put(
                f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst", content=BODY, headers=head))
            # הגיבוב של ההעלאה התחיל (בקוד הישן — על הלולאה עצמה, ואז
            # ההמתנה הזו חוזרת רק אחרי שהשער נפתח מעצמו).
            assert await asyncio.to_thread(gate.entered.wait, 10)
            hello = await client.post("/api/v1/agent/hello", json=hello_body(mac))
            tasks = await client.get("/api/console/tasks")
            gate.order.append("answered")
            gate.release.set()
            return hello, tasks, await upload

    hello, tasks, upload = asyncio.run(scenario())
    assert hello.status_code == 200, hello.text
    assert tasks.status_code == 200, tasks.text
    assert gate.order[:2] == ["answered", "released"], (
        gate.order, "השרת ענה רק אחרי שהכתיבה של ההעלאה השתחררה — הלולאה נחסמה (#1488)")
    # ההעלאה עצמה שלמה: הבייטים, וה-sha256 מזמן הקבלה (#1470) — בתשובה ובקובץ.
    assert upload.status_code == 200, upload.text
    expected = hashlib.sha256(BODY).hexdigest()
    assert upload.json() == {"ok": True, "sha256": expected}
    folder = staging_dir(server["ctx"].library.root, tid)
    assert (folder / "p3.windows.pcl.zst").read_bytes() == BODY
    record = folder / ("p3.windows.pcl.zst" + getattr(capture, "UPLOAD_SHA", ".upload-sha256"))
    assert record.read_text(encoding="ascii") == expected


def test_a_disk_that_fills_mid_upload_is_still_a_named_507(server, monkeypatch):
    """‏#411 נשמר: ‏ENOSPC מתוך הכתיבה — גם כשהיא בתהליכון — הוא 507 בשם,
    והקובץ החלקי ורשומת ה-sha נמחקים."""
    import errno
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    head = {TOKEN_HEADER: task_token(server, tid)}
    real_open = capture.Path.open

    def full_disk_open(self, mode="r", *a, **k):
        handle = real_open(self, mode, *a, **k)
        if "w" not in mode or not self.name.endswith(".pcl.zst"):
            return handle

        class Full:
            def write(self, _data):
                raise OSError(errno.ENOSPC, "No space left on device")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                handle.close()
        return Full()

    monkeypatch.setattr(capture.Path, "open", full_disk_open)
    r = server["anon"].put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst",
                           content=BODY, headers=head)
    assert r.status_code == 507, r.text
    assert "p3.windows.pcl.zst" in r.json()["detail"]
    folder = staging_dir(server["ctx"].library.root, tid)
    assert not (folder / "p3.windows.pcl.zst").exists()
    assert not list(folder.glob("*.upload-sha256"))
