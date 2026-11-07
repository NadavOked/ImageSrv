"""גיבוי ההגדרות של השרת (#1128, R41) — מה שאין לו ייצוג כתיקיית אימג'.

הדיסק הוא מקור האמת לאימג'ים (תיקייה + manifest) — הם **אינם** כאן: הם
מועתקים ב-rsync כתיקיות, ומאומתים ב-`tools/verify-images.py`. כאן: ה-DB
(מכונות, משתמשים, סודות TOTP, מיקומי אחסון) דרך
‏`sqlite3.Connection.backup()` — עותק עקבי גם תחת WAL, בניגוד ל-`cp` בזמן
ריצה (R40 #8) — ושאר `data_dir` (תעודת הקונסולה, netcfg,
known_hosts). ‏`MANIFEST.txt` אומר איזו גרסה ומתי; ‏`SHA256SUMS` על כל
קובץ. הארכיון **מכיל סודות** — מי שמוריד אותו מקבל אזהרה בקונסולה.

‏**‏`console_secret` אינו בגיבוי** (#2424, הכרעת נדב 04/11): מי שקורא את שיתוף
הגיבוי היה חותם בו עוגיית admin בלי סיסמה ובלי MFA. העותק נמחק ממנו ו-`VACUUM`
(כדי שהבייטים לא יישארו בדף פנוי); שרת שעולה על DB משוחזר מקבל סוד חדש
מ-`db._initialize` (‏`INSERT OR IGNORE`), וכל מי שהיה מחובר נכנס מחדש.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

#: קבצים שאינם נכנסים לארכיון: **כל** קובץ ששמו מתחיל ב-``imagesrv.db`` — ה-DB
#: עצמו (נכנס רק דרך `consistent_db_copy`, בלי ``console_secret``), קובצי ה-WAL שלו,
#: ועותק ה-DB שלפני עדכון (``imagesrv.db.pre-upgrade`` ו-``.tmp`` שלו, ‏#1894) שנושא
#: את הסוד כמות שהוא (#2504). תבנית ולא רשימת שמות: עותק DB שיתווסף מחר ל-data_dir
#: לא ייכנס לגיבוי בלי שאיש החליט על כך. ‏`tools/backup-server.sh` מדלג על אותה תבנית.
#: ומה שאינו הגדרה (בדיקות אחסון זמניות; ‏`branding/` — לוגו מוסד שהועלה לפני
#: שהיכולת הוסרה, #1302 — שארית שאיש אינו קורא). ‏`backups/` (#1956) — תיקיית גיבויי המסד
#: שעל כונן השרת: עותקי DB, ולכן לא בארכיון, מאותה סיבה כמו ``imagesrv.db*``.
DB_PREFIX = "imagesrv.db"
SKIP_DIRS = {"storage-test", "storage", "branding", "backups"}
#: ‏#2424: ``server.console.auth.SECRET_KEY`` — חותם העוגיות, שאינו נכנס לשום גיבוי.
SECRET_SETTING = "console_secret"
BACKUP_WARNING = "מכיל סודות: סיסמאות מגובבות, סודות MFA, מפתח התעודה. לשמור כמו סיסמה."


def _integrity(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "no answer"


def _drop_console_secret(copy: sqlite3.Connection) -> None:
    """‏#2424: הסוד יוצא מהעותק — לא מהמקור — ו-`VACUUM` בונה את הקובץ מחדש, כך
    שה-hex אינו נשאר בדף שהשורה התפנתה ממנו. DB בלי טבלת `settings` (בדיקות) — כמות שהוא.

    ‏#2460 (הכרעת נדב 06/11): באותו מעבר יוצאים גם הסודות של מיקומי האחסון — סיסמת SMB
    וסוד CHAP (‏`storage.locations.SECRET_KEYS`) — מ-`params_json` של כל מיקום. אחרי שחזור
    המיקום מבקש אותם מחדש (‏`secret_missing`). ‏`params_json` שאינו JSON נקרא — אין דרך לדעת
    מה יש בו, ולכן הוא אינו נשמר (`{}`); מיקום כזה ממילא לא עבד (`params_of` מחזיר `{}`)."""
    import json   # noqa: PLC0415
    from server.storage.locations import SECRET_KEYS   # noqa: PLC0415 — מקור אחד לרשימת הסודות

    tables = {name for (name,) in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables & {"settings", "storage_locations"}:
        return
    with copy:   # עותק פרטי; ה-context manager עושה commit, או rollback בחריגה (#54)
        if "settings" in tables:
            copy.execute("DELETE FROM settings WHERE key = ?", (SECRET_SETTING,))
        if "storage_locations" in tables:
            for loc_id, raw in copy.execute("SELECT id, params_json FROM storage_locations").fetchall():
                try:
                    params = json.loads(raw or "{}")
                except ValueError:
                    params = None
                if not isinstance(params, dict):
                    kept = {}
                elif any(key in params for key in SECRET_KEYS):
                    kept = {k: v for k, v in params.items() if k not in SECRET_KEYS}
                else:
                    continue
                copy.execute("UPDATE storage_locations SET params_json = ? WHERE id = ?",
                             (json.dumps(kept, ensure_ascii=False), loc_id))
    copy.execute("VACUUM")


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
            _drop_console_secret(target)
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
        # ‏#2426: ‏symlink אינו נארז — הוא היה מכניס לארכיון תוכן מחוץ ל-data_dir.
        for path in sorted(p for p in data_dir.rglob("*") if p.is_file() and not p.is_symlink()):
            rel = path.relative_to(data_dir)
            if rel.name.startswith(DB_PREFIX) or rel.parts[0] in SKIP_DIRS:
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
