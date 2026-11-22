"""#1517 (פרוסה 1): קליטת קובץ מחיצה בתהליך עובד, מאחורי מתג — כבוי כברירת מחדל.

**מה עובר לתהליך אחר:** הגיבוב (sha256), הכתיבה, ה-`fdatasync` התקופתי וה-`fsync`
הסופי של קובץ אחד. תהליך לכל העלאה, מוגבל ל-`IMAGESRV_INGEST_WORKERS` במקביל;
מעבר לתקרה ההעלאה ממשיכה בתהליך הראשי כמו עד היום (לא נדחית — הסוכן נותן
לחיבור `--speed-time`).

**מה נשאר בתהליך הראשי:** קריאת הגוף מהשקע (event loop), ה-DB, `task_for`,
`identity_gate`, `StreamGuard` (הרזרבה) ורשומת ה-sha256. (`Budget` ו-`Served.note`
שייכים להגשה, לא לקליטה, ונשארים שם.) **שום החלטה אינה עוברת לעובד.**

**עיקרון 6:** העובד מדווח sha256, והשרת **אינו סומך עליו לבדו**: (א) מספר
הבייטים שהעובד דיווח חייב להיות מה ששלחנו לו, (ב) גודל הקובץ בדיסק נקרא
בחזרה ושווה, (ג) ה-sha256 נכתב לרשומה כמו קודם ו-`upload_sha_problem` משווה
אותו למניפסט, (ד) הקריאה החוזרת מהדיסק ב-`_verify` נשארת כמות שהיא.
עובד שנפל, נתקע או שיקר בגודל — `IngestFailed`, כשל בשם. לא "הזרם הסתיים".
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator, Callable

from server import paths
from server.library import ingest_worker

log = logging.getLogger("imagesrv.capture")

ENV = "IMAGESRV_INGEST_WORKERS"
MAX_WORKERS = 8
#: כמה זמן מחכים לתוצאה אחרי סוף הזרם (ה-fsync הסופי של מחיצה גדולה).
FINISH_TIMEOUT_S = 1800.0
#: הפקודה — נקודת הזרקה לבדיקות (עובד שנופל, נתקע או משקר).
WORKER_COMMAND = [sys.executable, "-I", str(Path(__file__).with_name("ingest_worker.py"))]

_HEAD = struct.Struct(">Q")
_busy = 0   # נוגעים בו רק בלולאת האירועים — בלי await בין הבדיקה לעדכון
_warned_bad_setting = False


class IngestFailed(Exception):
    """העובד לא קלט את הקובץ. `kind`: enospc (דיסק מלא) או error (כל השאר)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def configured() -> int:
    """כמה עובדים מותרים. 0 = כבוי. ערך שאינו מספר שלם 0..8 = כבוי **ובקול**:
    המסלול שהוכח הוא ברירת המחדל, והמפעיל שטעה בהקלדה צריך לראות את זה."""
    global _warned_bad_setting
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return 0
    if raw.isdigit() and int(raw) <= MAX_WORKERS:
        return int(raw)
    if not _warned_bad_setting:
        _warned_bad_setting = True
        log.warning("%s=%r is not an integer 0..%d -- ingest workers stay off", ENV, raw, MAX_WORKERS)
    return 0


def take(limit: int) -> bool:
    """תופס מקום לעובד. `False` = התקרה מלאה והקורא ממשיך בתהליך הראשי."""
    global _busy
    if _busy >= limit:
        log.info("ingest workers: %d of %d busy -- this upload runs in the main process", _busy, limit)
        return False
    _busy += 1
    return True


def give() -> None:
    global _busy
    _busy = max(0, _busy - 1)


def busy() -> int:
    return _busy


async def stream(chunks: AsyncIterator[bytes], path: Path, *, batch: int, sync_every: int,
                 wrote: Callable[[int], None]) -> tuple[str, int]:
    """מזרים `chunks` לעובד; מחזיר (sha256, בייטים). **המקום כבר נתפס** (`take`)
    והוא משתחרר כאן. `wrote(n)` נקרא אחרי כל אצווה שנשלחה (רזרבת הדיסק) וחריגה
    ממנו מפילה את העובד. כל כשל של העובד — `IngestFailed`; חריגה של הקורא
    (ניתוק הלקוח, רזרבה) מועברת הלאה אחרי שהעובד נהרג."""
    err = tempfile.TemporaryFile()   # stderr לקובץ, לא ל-PIPE שאיש לא קורא
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *WORKER_COMMAND, str(path), str(sync_every),
            env={**os.environ, ingest_worker.ROOT_ENV: str(paths.REPO_ROOT)},   # #2239: the worker's lower_priority
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=err)
        sent, dead = await _feed(proc, chunks, batch, wrote)
        result = await _result(proc, dead, err)
        return _checked(result, sent, path)
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        err.close()
        give()


async def _feed(proc, chunks, batch: int, wrote) -> tuple[int, bool]:
    """שולח מסגרות עד סוף הזרם. `dead` = העובד סגר את הצינור (התוצאה אומרת למה)."""
    sent = 0
    pending = bytearray()

    async def send(data: bytes) -> None:
        proc.stdin.write(_HEAD.pack(len(data)) + data)
        await proc.stdin.drain()   # הלחץ-החוזר: הזרם אינו נקרא הלאה לפני שהעובד קיבל

    try:
        async for chunk in chunks:
            pending += chunk
            if len(pending) >= batch:
                await send(bytes(pending))
                sent += len(pending)
                wrote(len(pending))
                pending.clear()
        if pending:
            await send(bytes(pending))
            sent += len(pending)
        await send(b"")   # מסגרת הסיום — בלעדיה העובד רואה `truncated`
        proc.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        return sent, True
    return sent, False


async def _result(proc, dead: bool, err) -> dict:
    try:
        out = await asyncio.wait_for(proc.stdout.read(), FINISH_TIMEOUT_S)
        await asyncio.wait_for(proc.wait(), 30)
    except asyncio.TimeoutError:
        raise IngestFailed("error", f"the ingest worker gave no result within {FINISH_TIMEOUT_S:.0f}s") from None
    lines = out.decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(lines[-1]) if lines else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        err.seek(0)
        tail = err.read()[-400:].decode("utf-8", "replace").strip()
        why = "closed its input early" if dead else "ended"
        raise IngestFailed("error", f"the ingest worker {why} without a result"
                                    f" (exit code {proc.returncode}){': ' + tail if tail else ''}")
    for problem in result.get("priority") or []:
        log.warning("ingest worker priority: %s", problem)
    return result


def _checked(result: dict, sent: int, path: Path) -> tuple[str, int]:
    if not result.get("ok"):
        kind = "enospc" if result.get("kind") == "enospc" else "error"
        raise IngestFailed(kind, f"the ingest worker failed ({result.get('kind')}): {result.get('error')}")
    digest, got = result.get("sha256"), result.get("bytes")
    if not (isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)):
        raise IngestFailed("error", "the ingest worker returned no valid sha256")
    if got != sent:
        raise IngestFailed("error", f"the ingest worker counted {got} bytes of the {sent} it was sent")
    try:
        on_disk = path.stat().st_size
    except OSError as exc:
        raise IngestFailed("error", f"the received file could not be read back: {exc}") from exc
    if on_disk != sent:
        raise IngestFailed("error", f"the file on the server disk is {on_disk} bytes, {sent} were sent")
    return digest, sent
