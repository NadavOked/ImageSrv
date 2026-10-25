"""כתיבה אטומית ועמידה לנפילת חשמל (‏#1908, ‏#1813).

‏``Path.write_text`` פותח את הקובץ הקיים ב-``O_TRUNC`` וכותב לתוכו: כתיבה
שנקטעה — חריגה, דיסק מלא, נפילת חשמל — משאירה קובץ ריק או חתוך במקום
הישן. ב-ext4 (‏delayed allocation) גם כתיבה ש"הסתיימה" יכולה להיעלם
בנפילת חשמל שניות אחריה, אם איש לא קרא ל-``fsync``.

הצורה כאן היא זו של ``interserver_auth.write_credential_0600``: קובץ זמני
באותה תיקייה → ``fsync`` → ‏``os.replace`` → ‏``fsync`` לתיקייה. הקורא רואה
את הישן או את החדש בשלמותו, ולעולם לא חצי.

‏``fsync`` לתיקייה — רק ב-POSIX (בווינדוס אי-אפשר לפתוח תיקייה לכתיבה).
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def fsync_file(path: str | Path) -> None:
    """הבייטים של קובץ שכבר נכתב — לדיסק. כשל זורק (עיקרון 5)."""
    flags = os.O_RDONLY if os.name == "posix" else os.O_RDWR
    fd = os.open(os.fspath(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(directory: str | Path) -> None:
    """רשומות התיקייה (‏rename, יצירה) — לדיסק. ב-POSIX בלבד."""
    if os.name != "posix":
        return
    fd = os.open(os.fspath(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: str | Path, data: bytes | str, encoding: str = "utf-8",
                 mode: int | None = None) -> None:
    """כותב את ``data`` ל-``path`` כך שקורא רואה את הישן או את החדש, שלמים.

    קישור סמלי נפתר קודם (‏``/etc/resolv.conf`` הוא לעתים קישור): הכתיבה
    נוחתת על היעד, כמו ``write_text``, ולא מחליפה את הקישור בקובץ. ההרשאות
    של קובץ קיים נשמרות; קובץ חדש — ‏0644. ‏``mode`` קובע אותן במפורש (מפתח
    פרטי: ‏0600 — הקובץ הזמני נוצר 0600, ולכן אין רגע שבו הוא קריא). מחרוזת נכתבת עם ``\\n`` בלבד."""
    if isinstance(data, str):
        data = data.encode(encoding)
    target = Path(os.path.realpath(os.fspath(path)))
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is None:
            try:
                mode = stat.S_IMODE(os.stat(target).st_mode)
            except FileNotFoundError:
                mode = 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    fsync_dir(target.parent)
