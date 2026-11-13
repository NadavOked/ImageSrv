"""#2742: קליטה נכשלה 'העלאה=124' אחרי שכל הבייטים עברו.

ברזל 08/11 19:03, v0.91.3, tsk_6909 מ-HP-BUILD-2: partclone, zstd ו-fanout
יצאו 0, וה-curl עוד חיכה לתשובת השרת. השרת עושה fsync לכל הקובץ לפני
התשובה (#1951), והסוכן נתן לזה WAIT_HELPER_S (30ש') — בזמן שה-curl עצמו
מוכן לשקט של HTTP_STALL_TIMEOUT (120ש').

שני הצדדים נבדקים כאן:
* הסוכן — ההמתנה להעלאה תחומה ב-HTTP_STALL_TIMEOUT + WAIT_HELPER_S. שרת
  שעונה באיחור אחרי הבייט האחרון אינו מכשיל, ושרת שלא עונה גם בתוך הסכום
  עדיין נכשל בגלוי (עיקרון 5). הזמנים מקוצרים ב-env; היחס הוא שנבדק.
* השרת — fdatasync תקופתי תוך כדי הזרם, כך שה-fsync הסופי חסום במרווח.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from native import requires_native
from test_agent import BASH

#: curl מזויף: מרוקן את ה-fifo של `-T` כמו CURL_SINK, ואז "השרת" מתעכב
#: UP_SLEEP שניות לפני התשובה — ה-fsync שאחרי הבייט האחרון.
CURL_SLOW_ANSWER = (
    '#!/bin/sh\n'
    'case "$*" in */preflight*) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;; esac\n'
    'f=""\n'
    'while [ $# -gt 0 ]; do\n'
    '  [ "$1" = "-T" ] && { f="$2"; shift; }\n'
    '  shift\n'
    'done\n'
    '[ -n "$f" ] || exit 0\n'
    'cat "$f" > /dev/null\n'
    'sleep "$UP_SLEEP"\n'
    'exit 0\n'
)

requires_bash = requires_native(("bash", BASH))


def _capture(tmp_path, helper, stall, sleep):
    from test_capture_refusals import ONE_PARTITION, capture_run
    return capture_run(tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SLOW_ANSWER},
                       env={"WAIT_HELPER_S": str(helper), "HTTP_STALL_TIMEOUT": str(stall),
                            "UP_SLEEP": str(sleep)})


@requires_bash
def test_a_server_answering_after_the_helper_ceiling_does_not_fail_the_capture(tmp_path):
    """תשובה 3ש' אחרי הבייט האחרון, WAIT_HELPER_S=1: לפני התיקון — 124."""
    box, _run, out = _capture(tmp_path, helper=1, stall=6, sleep=3)
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")


@requires_bash
def test_the_upload_wait_is_still_capped_at_the_sum(tmp_path):
    """אותו איחור של 3ש', אבל הסכום 1+1: עדיין נכשל, בשם — התקרה נשארה."""
    from test_capture_refusals import refusal_reason
    box, run, out = _capture(tmp_path, helper=1, stall=1, sleep=3)
    assert "העלאה=124" in refusal_reason(box, run, out)


# --- השרת: fdatasync תוך כדי הזרם ---------------------------------------------


def test_a_stream_larger_than_the_interval_is_synced_along_the_way(server, images_root, monkeypatch):
    """ארבעה chunks של 1MiB, מרווח של 1MiB: fdatasync באמצע, לא רק בסוף.

    httpx ASGITransport ולא TestClient: TestClient מאחד את הגוף להודעה אחת,
    ואז הזרם הוא אצווה אחת — בלי "תוך כדי" שאפשר לבדוק."""
    httpx = pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from server.library import capture
    from server.core.tasks import TOKEN_HEADER, staging_dir
    from test_capture import make_task, setup_build_machine, task_token

    synced: list[int] = []

    def recording_fdatasync(fd):
        synced.append(os.fstat(fd).st_size)

    # raising=False: בקוד שלפני התיקון אין SYNC_EVERY — הבקרה נכשלת על ההתנהגות.
    monkeypatch.setattr(capture, "SYNC_EVERY", capture.CHUNK, raising=False)
    monkeypatch.setattr(capture.os, "fdatasync", recording_fdatasync, raising=False)
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    head = {TOKEN_HEADER: task_token(server, tid)}

    async def blocks():
        for i in range(4):
            yield bytes([i]) * capture.CHUNK

    async def upload():
        transport = httpx.ASGITransport(app=server["app"])
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.put(f"/api/v1/capture/{tid}/files/p3.windows.pcl.zst",
                                    content=blocks(), headers=head)

    r = asyncio.run(upload())
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    staged = staging_dir(images_root, tid) / "p3.windows.pcl.zst"
    assert staged.stat().st_size == 4 * capture.CHUNK
    assert len(synced) > 1, f"fdatasync לא רץ תוך כדי הזרם: {synced}"
    assert min(synced) < 4 * capture.CHUNK, f"הסנכרון היה רק בסוף: {synced}"
