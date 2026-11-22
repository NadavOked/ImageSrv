"""#1517: קליטה בתהליכי עבודה — מאחורי `IMAGESRV_INGEST_WORKERS`, כבוי כברירת מחדל.

ארבע שאלות, כל אחת עם בדיקה שנכשלת כשהתשובה לא נכונה:

1. **במקביל:** שלוש מחיצות של משימה אחת נקלטות יחד, בשלושה תהליכים שונים
   מהשרת, וכל קובץ שלם עם ה-sha256 הנכון.
2. **עובד שנכשל נכשל בגלוי:** יציאה עם קוד, או דיווח על בייטים שלא נשלחו —
   500 בשם, המשימה `failed`, אין קובץ חלקי, והמקום בתקרה משתחרר.
3. **כשל באימות:** מחיצה שהתוכן שלה אינו מה שבמניפסט לא נכנסת לספרייה —
   גם כשהיא נקלטה בעובד. ועובד שמדווח sha256 שגוי נתפס ב-`upload_sha_problem`
   (עיקרון 6: השרת לא סומך על העובד לבדו).
4. **כבוי = כמו קודם:** בלי המשתנה לא נוצר שום תהליך.

התהליכים האמיתיים רצים: ה-stdin של העובד הוא הצינור מהשרת, וה-stderr שלו
קובץ ולא PIPE שאיש לא קורא. **הבדיקות לא הורצו במחשב הפיתוח** — רק על שרת
הבדיקות (`/work/q/submit`).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import textwrap

import pytest

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from server.library import ingest_pool  # noqa: E402
from server.core.tasks import TOKEN_HEADER, staging_dir  # noqa: E402

from test_capture import (PART_A, PART_B, do_capture, make_task,  # noqa: E402
                          setup_build_machine, task_token)

#: מעל חוצץ אחד של 1MiB ולא כפולה שלו — גם הזנב עובר בעובד; שלוש מחיצות שונות.
FILES = {
    "p1.esp.pcl.zst": bytes(range(256)) * (3 * 4096 + 5),
    "p2.linux.pcl.zst": bytes(reversed(range(256))) * (2 * 4096 + 11),
    "p3.windows.pcl.zst": bytes(range(0, 256, 3)) * (5 * 4096 + 7),
}


def _spy_command(tmp_path, monkeypatch):
    """עוטף את העובד האמיתי ורושם את ה-pid של כל תהליך שנוצר."""
    pids = tmp_path / "pids"
    pids.mkdir()
    real = ingest_pool.WORKER_COMMAND[-1]
    wrapper = tmp_path / "spy_worker.py"
    wrapper.write_text(textwrap.dedent(f"""
        import os, runpy, sys
        open(os.path.join({str(pids)!r}, str(os.getpid())), "w").close()
        sys.argv[0] = {real!r}
        runpy.run_path({real!r}, run_name="__main__")
    """), encoding="utf-8")
    monkeypatch.setattr(ingest_pool, "WORKER_COMMAND", [sys.executable, "-I", str(wrapper)])
    return pids


def _task(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    return tid, {TOKEN_HEADER: task_token(server, tid)}


def _state(server, tid):
    return server["ctx"].conn.execute(
        "SELECT state, error FROM tasks WHERE id = ?", (tid,)).fetchone()


def test_three_partitions_are_received_at_once_in_three_worker_processes(server, monkeypatch, tmp_path):
    tid, head = _task(server)
    monkeypatch.setenv(ingest_pool.ENV, "3")
    pids = _spy_command(tmp_path, monkeypatch)
    real_stream = ingest_pool.stream
    seen = {"busy": None}

    async def scenario():
        arrived, all_in = [], asyncio.Event()

        async def rendezvous(chunks, path, **kw):
            arrived.append(path.name)
            if len(arrived) == len(FILES):
                seen["busy"] = ingest_pool.busy()
                all_in.set()
            await asyncio.wait_for(all_in.wait(), 20)   # שלושתן בפנים לפני שאחת ממשיכה
            return await real_stream(chunks, path, **kw)

        monkeypatch.setattr(ingest_pool, "stream", rendezvous)
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await asyncio.gather(*[
                client.put(f"/api/v1/capture/{tid}/files/{name}", content=data, headers=head)
                for name, data in FILES.items()])

    answers = asyncio.run(scenario())
    assert seen["busy"] == 3, "שלוש העלאות היו אמורות לתפוס שלושה עובדים בו-זמנית"
    folder = staging_dir(server["ctx"].library.root, tid)
    for (name, data), answer in zip(FILES.items(), answers):
        expected = hashlib.sha256(data).hexdigest()
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"ok": True, "sha256": expected}
        assert (folder / name).read_bytes() == data
        assert (folder / (name + ".upload-sha256")).read_text(encoding="ascii") == expected
    workers = {int(p.name) for p in pids.iterdir()}
    assert len(workers) == 3 and os.getpid() not in workers, workers
    assert ingest_pool.busy() == 0, "התקרה לא שוחררה"


def test_without_the_setting_no_worker_is_ever_started(server, monkeypatch, tmp_path):
    tid, head = _task(server)
    monkeypatch.delenv(ingest_pool.ENV, raising=False)
    pids = _spy_command(tmp_path, monkeypatch)
    r = server["anon"].put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst",
                           content=FILES["p3.windows.pcl.zst"], headers=head)
    assert r.status_code == 200, r.text
    assert list(pids.iterdir()) == []


def test_an_upload_over_the_ceiling_runs_in_the_main_process_not_refused(server, monkeypatch, tmp_path):
    tid, head = _task(server)
    monkeypatch.setenv(ingest_pool.ENV, "1")
    pids = _spy_command(tmp_path, monkeypatch)
    assert ingest_pool.take(1)   # העובד היחיד תפוס
    try:
        r = server["anon"].put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst",
                               content=FILES["p3.windows.pcl.zst"], headers=head)
    finally:
        ingest_pool.give()
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == hashlib.sha256(FILES["p3.windows.pcl.zst"]).hexdigest()
    assert list(pids.iterdir()) == []


@pytest.mark.parametrize("script, why", [
    ("import sys; sys.stdin.buffer.read(10); sys.exit(3)", "exit code 3"),
    ("import sys, json; sys.stdin.buffer.read();"
     " print(json.dumps({'ok': True, 'bytes': 1, 'sha256': 'a' * 64}))", "counted 1 bytes"),
], ids=["worker-dies", "worker-counts-wrong"])
def test_a_failed_worker_fails_the_task_out_loud(server, monkeypatch, script, why):
    tid, head = _task(server)
    monkeypatch.setenv(ingest_pool.ENV, "2")
    monkeypatch.setattr(ingest_pool, "WORKER_COMMAND", [sys.executable, "-c", script, "--"])
    r = server["anon"].put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst",
                           content=FILES["p3.windows.pcl.zst"], headers=head)
    assert r.status_code == 500, r.text
    assert "ingest worker" in r.json()["detail"] and why in r.json()["detail"], r.json()
    row = _state(server, tid)
    assert row["state"] == "failed" and "ingest worker" in row["error"], dict(row)
    assert not staging_dir(server["ctx"].library.root, tid).exists(), "נשאר אזור עבודה"
    assert ingest_pool.busy() == 0, "התקרה לא שוחררה אחרי כשל"


def test_a_tampered_partition_received_by_a_worker_never_enters_the_library(server, images_root, monkeypatch, tmp_path):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    monkeypatch.setenv(ingest_pool.ENV, "2")
    pids = _spy_command(tmp_path, monkeypatch)
    response = do_capture(server, created["id"],
                          files={"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": b"corrupted-partition-3"})
    assert len(list(pids.iterdir())) == 2, "הקבצים לא נקלטו בעובדים"
    assert response.status_code == 400 and "sha256" in response.json()["detail"]
    assert not (images_root / created["image_id"]).exists()
    assert _state(server, created["id"])["state"] == "failed"


def test_a_worker_that_reports_the_wrong_sha256_is_caught_against_the_manifest(server, images_root, monkeypatch, tmp_path):
    """העובד כותב את הבייטים הנכונים אבל מדווח sha256 שגוי: השרת לא יכול לדעת
    ב-PUT, ו-`upload_sha_problem` תופס אותו מול המניפסט — לפני האימות ברקע."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    real = ingest_pool.WORKER_COMMAND[-1]
    liar = tmp_path / "liar_worker.py"
    liar.write_text(textwrap.dedent(f"""
        import json, runpy, sys
        g = runpy.run_path({real!r}, run_name="ingest_real")
        r = g["receive"](sys.stdin.buffer, sys.argv[1], int(sys.argv[2]))
        r["sha256"] = "0" * 64
        print(json.dumps(r))
    """), encoding="utf-8")
    monkeypatch.setenv(ingest_pool.ENV, "2")
    monkeypatch.setattr(ingest_pool, "WORKER_COMMAND", [sys.executable, "-I", str(liar)])
    response = do_capture(server, created["id"])
    assert response.status_code == 400, response.text
    assert "sha256" in response.json()["detail"]
    assert not (images_root / created["image_id"]).exists()
