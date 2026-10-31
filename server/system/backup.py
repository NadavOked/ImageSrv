"""גיבוי ההגדרות של השרת (#1128, R41) — מה שאין לו ייצוג כתיקיית אימג'.

הדיסק הוא מקור האמת לאימג'ים (תיקייה + manifest) — הם **אינם** כאן: הם
מועתקים ב-rsync כתיקיות, ומאומתים ב-`tools/verify-images.py`. כאן: ה-DB
(מכונות, משתמשים, סודות TOTP, ‏`console_secret`, מיקומי אחסון) דרך
‏`sqlite3.Connection.backup()` — עותק עקבי גם תחת WAL, בניגוד ל-`cp` בזמן
ריצה (R40 #8) — ושאר `data_dir` (תעודת הקונסולה, netcfg,
known_hosts). ‏`MANIFEST.txt` אומר איזו גרסה ומתי; ‏`SHA256SUMS` על כל
קובץ. הארכיון **מכיל סודות** — מי שמוריד אותו מקבל אזהרה בקונסולה.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

#: קבצים שאינם נכנסים לארכיון: ה-DB עצמו (נכנס דרך `.backup`), קובצי ה-WAL
#: שלו, ומה שאינו הגדרה (בדיקות אחסון זמניות; ‏`branding/` — לוגו מוסד
#: שהועלה לפני שהיכולת הוסרה, #1302 — שארית שאיש אינו קורא).
SKIP_NAMES = {"imagesrv.db", "imagesrv.db-wal", "imagesrv.db-shm", "imagesrv.db-journal"}
SKIP_DIRS = {"storage-test", "storage", "branding"}
BACKUP_WARNING = "מכיל סודות: סיסמאות מגובבות, סודות MFA, מפתח התעודה. לשמור כמו סיסמה."


def _integrity(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "no answer"


def consistent_db_copy(conn: sqlite3.Connection) -> bytes:
    """עותק עקבי של ה-DB כבייטים, דרך ה-backup API של sqlite (לא cp).

    ‏`db.Database` הוא עטיפה לכל-תהליכון — הגיבוי צריך את החיבור הגולמי.
    היעד הוא קובץ זמני ולא `:memory:`: ‏backup מעתיק גם את כותרת ה-WAL של
    המקור, ועותק כזה בזיכרון אינו נפתח; על קובץ ‏`journal_mode=DELETE`
    מיישר את הכותרת כך שהארכיון נפתח בכל מקום, גם ב-`sqlite3` מהשורה."""
    raw = getattr(conn, "connection", conn)
    with tempfile.TemporaryDirectory(prefix="imagesrv-backup-") as tmp:
        path = Path(tmp) / "imagesrv.db"
        target = sqlite3.connect(path)
        try:
            raw.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            # ‏integrity_check על העותק הוא הראיה החיובית שהגיבוי קריא,
            # לא רק שהוא נכתב (עיקרון 5).
            verdict = _integrity(target)
            if verdict != "ok":
                raise RuntimeError(f"בדיקת integrity_check על עותק הגיבוי נכשלה: {verdict}")
        finally:
            target.close()
        return path.read_bytes()


def build_archive(conn: sqlite3.Connection, data_dir: Path, version: str | None,
                  now: datetime | None = None) -> tuple[bytes, str]:
    """‏tar.gz של ההגדרות: ‏`imagesrv.db` (עותק עקבי), שאר `data_dir`,
    ‏`MANIFEST.txt`, ‏`SHA256SUMS`. מחזיר (bytes, שם קובץ)."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    name = f"imagesrv-settings-{stamp}.tar.gz"
    db_bytes = consistent_db_copy(conn)
    sums: list[str] = []
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add_bytes(arcname: str, payload: bytes, mode: int = 0o600) -> None:
            info = tarfile.TarInfo(arcname)
            info.size = len(payload)
            info.mtime = int(now.timestamp())
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))
            sums.append(f"{hashlib.sha256(payload).hexdigest()}  {arcname}")

        add_bytes("data/imagesrv.db", db_bytes)
        for path in sorted(p for p in data_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(data_dir)
            if rel.name in SKIP_NAMES or rel.parts[0] in SKIP_DIRS:
                continue
            add_bytes(f"data/{rel.as_posix()}", path.read_bytes())
        manifest = (
            f"imagesrv settings backup\nversion={version or 'unknown'}\n"
            f"created={now.isoformat(timespec='seconds')}\n"
            f"contents=data/ (imagesrv.db via sqlite backup API, data_dir without images)\n"
            f"warning={BACKUP_WARNING}\n"
        ).encode("utf-8")
        add_bytes("MANIFEST.txt", manifest, 0o644)
        add_bytes("SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8"), 0o644)
    return buf.getvalue(), name
