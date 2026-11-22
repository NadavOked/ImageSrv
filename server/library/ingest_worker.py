"""#1517: תהליך עובד של קליטה — מקבל מסגרות מהשרת, מגבב וכותב קובץ אחד.

**רץ כסקריפט** (`python -I ingest_worker.py <יעד> <sync_every>`), לא כמודול:
`-I` אינו טוען את תיקיית העבודה ולא את `PYTHONPATH`, וההרצה לא מייבאת את
FastAPI. מה שאינו כאן: DB, `Budget`, `Served.note`, `source_gate` — כולם
נשארים בתהליך הראשי. התהליך הזה לא מכיר משימה ולא מכיר אסימון.

**פרוטוקול (stdin):** מסגרות של `8 בייט אורך (big-endian) + נתונים`;
מסגרת באורך 0 היא **סוף הזרם** ואין אחרת. EOF בלי מסגרת הסיום הוא
`truncated` — השרת התרסק או הלקוח נפל באמצע, והבייטים אינם קובץ שלם
(עיקרון 5: אין "הזרם נסגר" = "הזרם הסתיים").
**פלט (stdout):** שורת JSON אחת — `{"ok": true, "bytes": N, "sha256": "..."}`
או `{"ok": false, "kind": "enospc|truncated|error", "error": "..."}`.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import struct
import sys

HEAD = struct.Struct(">Q")
#: the repo root, set by the main process from `server/paths.py` (`ingest_pool`): `-I` loads neither it nor
#: PYTHONPATH, and a module outside `server/paths.py` does not compute paths outside its own directory (#2239).
ROOT_ENV = "IMAGESRV_REPO_ROOT"
#: תקרה למסגרת אחת. השרת שולח אצוות של 1MiB; מעל זה הכותרת פגומה והזיכרון לא מוקצה.
MAX_FRAME = 16 * 1024 * 1024


def _lower_priority() -> list[str]:
    """אותה הנמכה כמו מאגר `CAPTURE_IO` (#1974). אינה תנאי לעבודה — מה שלא
    הונמך חוזר בתוצאה והשרת רושם אותו."""
    try:
        sys.path.insert(0, os.environ[ROOT_ENV])
        from server.system import priority
        return priority.lower_this_thread()
    except Exception as exc:   # noqa: BLE001 -- עדיפות, לא תנאי; נאמר בתוצאה
        return [f"priority module unavailable: {exc!r}"]


def _read_exact(stream, count: int) -> bytes | None:
    data = stream.read(count)
    return data if len(data) == count else None


def receive(stream, dest: str, sync_every: int) -> dict:
    digest = hashlib.sha256()
    total = unsynced = 0
    try:
        with open(dest, "wb") as handle:
            while True:
                head = _read_exact(stream, HEAD.size)
                if head is None:
                    return {"ok": False, "kind": "truncated",
                            "error": f"the stream ended without its closing frame after {total} bytes"}
                (size,) = HEAD.unpack(head)
                if size == 0:
                    break
                if size > MAX_FRAME:
                    return {"ok": False, "kind": "error", "error": f"frame of {size} bytes is over the limit"}
                data = _read_exact(stream, size)
                if data is None:
                    return {"ok": False, "kind": "truncated",
                            "error": f"the stream ended inside a frame after {total} bytes"}
                digest.update(data)
                handle.write(data)
                total += size
                unsynced += size
                if unsynced >= sync_every:   # #2742: ה-fsync הסופי חסום במרווח אחד
                    handle.flush()
                    getattr(os, "fdatasync", os.fsync)(handle.fileno())
                    unsynced = 0
            handle.flush()
            os.fsync(handle.fileno())   # #1951: לדיסק לפני "התקבל"
    except OSError as exc:
        kind = "enospc" if exc.errno == errno.ENOSPC else "error"
        return {"ok": False, "kind": kind, "error": f"{exc}"}
    return {"ok": True, "bytes": total, "sha256": digest.hexdigest()}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: ingest_worker.py <dest> <sync_every>\n")
        return 2
    lowered = _lower_priority()
    result = receive(sys.stdin.buffer, argv[1], int(argv[2]))
    if lowered:
        result["priority"] = lowered
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
