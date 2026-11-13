"""#1895/#1956 — גיבוי המסד: יומי בשעה שנבחרה, ומיד אחרי שינוי אימג'; גיזום לפי גודל.

הכרעת נדב, 06/11 (מוקאפ `1956-installer-backup`): הגיבויים נשמרים על כונן השרת, במקום ששמור להם
מראש — SMB ו-NFS הם לאחסון האימג'ים בלבד. גיבוי תמיד קיים, ואין "לא נבחר יעד". עותק על כונן אחר
הוא תיקייה מעוגנת שנבחרת בקונסולה.

`python3 -m server.system.backup_job --data-dir /var/lib/imagesrv` — מה ש-`imagesrv-backup.timer`
מריץ **כל דקה** (`deploy/imagesrv-backup.service`, כ-root ובלי `ProtectSystem=strict`: תיקייה
מעוגנת יכולה להיות בכל מקום; השרת עצמו כותב ליעד רק תחת `/mnt` ו-`/media`, #2396). דקה ולא שעה
קבועה: השעה נבחרת בקונסולה, והריצה עצמה קובעת אם הגיע הזמן (`due`). בלי תור ובלי קובץ מצב:

1. **צריך גיבוי עכשיו?** (`due`) יעד שעוד לא גובה אליו; עברה שעת הגיבוי היומית מאז הניסיון
   האחרון; שורת יומן של העלאה, קליטה או מחיקת אימג' (`IMAGE_EVENTS`) חדשה מהניסיון האחרון; או
   ניסיון שנכשל לפני יותר משעה. לא צריך — יציאה 0 בלי לכתוב דבר. שרת שהיה כבוי בשעה שנבחרה מגבה
   כשהוא עולה. אירוע אימג' אחרי שקט מגבה מיד; אירועים בתוך `COALESCE_SECONDS` מהניסיון האחרון הם
   גיבוי אחד בסוף החלון — סדרת מחיקות אינה סדרת גיבויים.
2. **סבב רץ?** (`busy_db.reason`) הגיבוי מחכה: `waiting` בקובץ הסטטוס, והדקה הבאה שואלת שוב — אין
   דילוג על יום, כי התשובה ל-1 לא משתנה עד שגיבוי רץ.
3. עותק עקבי דרך `sqlite3.Connection.backup` (`server.system.backup.consistent_db_copy` —
   `integrity_check` = ok על העותק), **כתיבה אטומית** ל-`<dest>/db/imagesrv-<ts>.db`: קובץ
   זמני באותה תיקייה (שם אקראי, `O_EXCL|O_NOFOLLOW` — מי שכותב ליעד אינו מפנה את
   ה-root דרך symlink, #2423), `fsync`, `os.replace`, `fsync` לתיקייה — ואז קריאה חוזרת:
   sha256 של מה שעל היעד = של מה שנכתב, ו-`integrity_check` על הקובץ שביעד (עיקרון 5).
   תיקייה שנבחרה כשהיא על **כונן אחר** (`same_disk: false`, #2574) נבדקת שוב **בכל גיבוי**: אם כבר
   אינה על כונן אחר מהמסד — לא עוגנה אחרי אתחול או שנמחקה — או שלא הצלחנו לבדוק, הגיבוי נכשל בשמו
   ולא נכתב דבר, במקום לכתוב בשקט לכונן המערכת. תיקייה רגילה על כונן המסד היא יעד לגיטימי: לא נבדקת.
4. גיזום לפי גודל (`prune`): המקום השמור (ברירת מחדל 5GB, תקרה 20% מהכונן) — הישן ביותר יוצא
   **אחרי** שהחדש אומת, ורק `imagesrv-<ts>.db` ב-`<dest>/db`. לעולם לא אימג'ים. **מוגנים תמיד**
   (`_protected`): החדש, הגיבוי התקין האחרון, והראשון של כל אחד מ-`KEEP_DAILY` הימים האחרונים —
   אחרת סדרת אירועים דוחקת את כל הגיבויים הטובים, ומה שנשאר הוא רק המצב שאחרי התקלה. אם המוגנים
   והחדש לא נכנסים — נבדק **לפני** הכתיבה — לא נכתב ושום דבר לא נמחק: `ReserveTooSmall`, כתום עם
   הסיבה. לא "נכתב ולא נמחק": כך כל אירוע היה מוסיף קובץ, והמקום השמור היה נפרץ בלי גבול.
5. `<data-dir>/last-backup.json` (אטומי): מתי, הצליח או לא, היעד, הקובץ, השגיאה,
   ומתי ההצלחה האחרונה. כשל = יציאה 1 והשגיאה בקובץ — לא בשקט.

`tools/backup-server.sh` (ידני/לילי, כולל אימג'ים וקובצי מערכת) כותב לאותו `<dest>/db`
באותם שמות, ולכן הגיזום כאן חל גם עליו.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.system import busy_db

#: ההגדרה בקונסולה (`POST /api/console/backup-target`). ריק / חסר = התיקייה הקבועה על כונן השרת.
SETTING = "backup_dest"
TARGET_SETTING = "backup_target"
TIME_SETTING = "backup_time"
RESERVE_SETTING = "backup_reserve_gb"
#: #2865 (נדב 11/11): המתג "גיבוי אוטומטי". רק "false" מכבה; חסר — וכל ערך אחר — דלוק, כי גיבוי הוא
#: ברירת המחדל: ערך משובש לא יכבה גיבוי בשקט.
ENABLED_SETTING = "backup:enabled"
STATUS_NAME = "last-backup.json"
#: התיקייה הקבועה, תחת `data_dir`: כתיבה מותרת בשתי היחידות (`ReadWritePaths` של השרת ו-
#: `ProtectSystem=full` של הגיבוי). `backup.SKIP_DIRS`, `backup-server.sh` ו-`DATA_DIR_RESERVED`
#: מדלגים עליה: עותק DB אינו נכנס לארכיון ההגדרות.
DEFAULT_DIR = "backups"
DEFAULT_TIME = "02:00"
DEFAULT_RESERVE_GB = 5
MIN_RESERVE_GB = 1
#: "לא יותר מ-20% מהכונן, כדי שיישאר מקום לאימג'ים" (נדב 06/11).
CAP_PERCENT = 20
GB = 10 ** 9
#: גיבוי יומי + שעתיים: יום בלי גיבוי הוא כבר "ישן", ואיחור של סבב ערב או שעון קיץ עוד לא.
STALE_SECONDS = 26 * 3600
RETRY_SECONDS = 3600
#: חלון קיבוץ של אירועי אימג'. נבחר, לא נמדד: קצר מספיק ל"מיד", וארוך מסדרת מחיקות של מפעיל.
COALESCE_SECONDS = 15 * 60
#: כמה ימים אחרונים שומרים מהם גיבוי בכל מצב. נבחר, לא נמדד: לא נמדד גודל מסד בשרת חי, וכל יום
#: כאן הוא עותק נוסף שהמקום השמור חייב להכיל.
KEEP_DAILY = 3
#: יעדי רשת שהוסרו (#1956). הגדרה שמורה כזו נשארת במקומה, והגיבוי עובר לכונן השרת (`effective_dest`).
LEGACY_KINDS = ("smb", "nfs")
#: מה ש"העלאה או מחיקה של אימג'" כותבות ליומן. היומן הוא האות — שורה אחת, אחרי שהפעולה הצליחה,
#: בלי נקודת חיבור בכל מסלול שמכניס אימג' לספרייה.
IMAGE_EVENTS = ("image_upload", "image_delete", "capture_done", "storage_image_received", "vmupdate_saved")
TIMER_UNIT = "imagesrv-backup.timer"
_NAME = re.compile(r"^imagesrv-(\d{8}-\d{6})\.db$")
_STAMP = "%Y%m%d-%H%M%S"
LABEL = "גיבוי המסד"
log = logging.getLogger("imagesrv.backup")


def check_dest(value: object) -> tuple[str | None, str | None]:
    """(היעד המנורמל, None) או (None, סיבה בעברית). "" = ללא תיקייה (חוזר לכונן השרת).

    נתיב מוחלט, בלי `..`, לא `/`, ותיקייה **קיימת** — ראיה חיובית שהיעד שם עכשיו
    (דיסק שלא עוגן נדחה כאן, לא בגיבוי הראשון)."""
    if not isinstance(value, str):
        return None, "יעד הגיבוי: נתיב תיקייה (מחרוזת)"
    path = value.strip()
    if path == "":
        return "", None
    if len(path) > 255 or any(ord(c) < 32 for c in path) or ".." in path.split("/"):
        return None, "יעד הגיבוי: נתיב מוחלט לתיקייה, למשל /mnt/backup"
    # CodeQL #616-618: נרמול ואז בדיקת prefix — הצורה שהניתוח מזהה כבדיקת נתיב.
    path = posixpath.normpath(path)
    if not path.startswith("/"):
        return None, "יעד הגיבוי: נתיב מוחלט לתיקייה, למשל /mnt/backup"
    path = "/" + path.lstrip("/")
    if path == "/":
        return None, "יעד הגיבוי: לא שורש מערכת הקבצים — תיקייה על כונן מעוגן"
    if not os.path.isdir(path):
        return None, f"יעד הגיבוי: התיקייה {path} אינה קיימת (הכונן לא מעוגן?)"
    return path, None


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":                      # Windows (תחנת פיתוח): אין fsync לתיקייה
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


#: #2423: ל-Windows (תחנת הפיתוח) אין O_NOFOLLOW; שם O_EXCL לבדו.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _tmp_name(target: Path) -> Path:
    return target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")


def _atomic_write(target: Path, data: bytes, mode: int = 0o600) -> None:
    """#2423: היעד הוא NAS שאחרים כותבים אליו, והגיבוי רץ כ-root. שם זמני קבוע
    עם `O_TRUNC` עקב אחרי symlink שנשתל בשמו ודרס קובץ ייצור — ו-`os.replace`
    העביר את הקישור עצמו לשם הסופי, כך שהקריאה החוזרת "אימתה" את הקובץ שנדרס.
    עכשיו: שם אקראי, `O_EXCL|O_NOFOLLOW` (קובץ חדש תמיד — ולכן גם ה-`mode` חל
    תמיד), ותיקייה או יעד שהם symlink — סירוב בשם."""
    for path in (target.parent, target):
        if path.is_symlink():
            raise BackupError(f"{path} הוא קישור סמלי — הגיבוי אינו כותב דרכו (#2423)")
    tmp = _tmp_name(target)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)


def _read_back(target: Path) -> bytes:
    """#2423: הקובץ ביעד, ב-`O_NOFOLLOW` — קישור שהוחלף לתוך השם אחרי הכתיבה אינו נקרא."""
    fd = os.open(target, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _integrity(payload: bytes) -> str:
    """integrity_check על הבייטים שנקראו מהיעד (בזיכרון — לא פתיחה נוספת של נתיב)."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(payload)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "no answer"
    finally:
        conn.close()


class BackupError(RuntimeError):
    """#2543: כישלון שאנחנו מנסחים — הטקסט שלו נכתב ל-`last-backup.json` ומוצג כמו שהוא.
    כל חריגה אחרת (OSError, sqlite3) נכתבת ליומן השרת, ובקובץ — `FAILED`."""


#: #2543: מה שנכתב לקובץ הסטטוס (ומוצג בבריאות) כשהכישלון אינו שלנו.
FAILED_TAIL = "הפרטים ביומן השרת"
FAILED = f"הגיבוי נכשל — {FAILED_TAIL}"


class ReserveTooSmall(Exception):
    """עותק אחד של המסד גדול מהמקום השמור: לא נכתב דבר ולא נמחק דבר. כתום, לא אדום."""


def _size(value: int) -> str:
    return f"{value / GB:.1f} GB" if value >= GB else f"{value / 10 ** 6:.1f} MB"


def _backups(db_dir: Path) -> list[tuple[Path, int]]:
    """הגיבויים ביעד, מהישן לחדש (השם הוא החותמת). קישור סמלי אינו גיבוי ואינו נספר (#2423)."""
    return [(path, path.stat().st_size) for path in sorted(db_dir.iterdir())
            if _NAME.match(path.name) and path.is_file() and not path.is_symlink()]


def _protected(names: list[str], new: str, last_good: str | None) -> set[str]:
    """מה שלא נמחק גם כשהמקום נגמר: החדש, התקין האחרון, והראשון של כל אחד מ-`KEEP_DAILY` הימים
    האחרונים. הראשון ולא האחרון: הוא הרחוק ביותר מסדרת אירועים של אותו יום."""
    first_of_day: dict[str, str] = {}
    for name in sorted(names):
        first_of_day.setdefault(name[len("imagesrv-"):][:8], name)
    days = sorted(first_of_day)[-KEEP_DAILY:]
    kept = {new, *(first_of_day[day] for day in days)}
    return kept | {last_good} if last_good in names else kept


def prune(db_dir: Path, protected: set[str], reserve: int) -> list[str]:
    """מוחק את `imagesrv-<ts>.db` הישנים שאינם מוגנים עד שהכול נכנס ב-`reserve` בתים. מחזיר את
    השמות שנמחקו."""
    found = _backups(db_dir)
    total = sum(size for _, size in found)
    gone = []
    for path, size in found:
        if total <= reserve:
            break
        if path.name not in protected:
            path.unlink()
            total -= size
            gone.append(path.name)
    return gone


def drive_cap_bytes(path: Path) -> int:
    """20% מהכונן שעליו `path` (או התיקייה הקיימת הקרובה: התיקייה הקבועה נוצרת בגיבוי הראשון)."""
    existing = next(p for p in (path, *path.parents) if p.exists())
    return shutil.disk_usage(existing).total * CAP_PERCENT // 100


def expects_other_disk(stored: str | None, raw_target: str | None) -> bool:
    """האם המפעיל בחר תיקייה שנבדקה בהגדרה והייתה על כונן אחר מהמסד (`same_disk: false`). רק כאן העיגון
    נדרש (#2574): `same_disk: true` / `null` (תיקייה רגילה, או יעד שלא נבדק) ויעד ברירת המחדל — לא."""
    try:
        target = json.loads(raw_target or "null")
    except ValueError:
        return False
    return bool((stored or "").strip()) and isinstance(target, dict) and target.get("type") == "local"         and target.get("same_disk") is False


def _require_other_disk(dest: Path, data_dir: Path) -> None:
    """#2574: יעד שנבחר כשהוא על כונן אחר חייב להיות עליו גם עכשיו. כונן שנפל או לא עוגן משאיר את
    `dest` תיקייה ריקה על כונן המערכת, והגיבוי היה "מצליח" אליו. לא נוצרת כאן תיקייה: היעד קיים או שנכשלים.
    "לא הצלחנו לבדוק" אינו "כונן אחר" (עיקרון 5)."""
    from server.system.backup_target import same_disk   # noqa: PLC0415 — backup_target מייבא את המודול הזה

    if not dest.is_dir():
        raise BackupError(f"התיקייה {dest} אינה קיימת (הכונן לא מעוגן?) — הגיבוי לא נכתב")
    verdict = same_disk(dest, data_dir)
    if verdict is True:
        raise BackupError(f"{dest} נבחרה כשהיא על כונן אחר, ועכשיו היא על אותו כונן כמו המסד (הכונן לא "
                           "מעוגן?) — הגיבוי לא נכתב")
    if verdict is None:
        raise BackupError(f"לא הצלחנו לבדוק אם {dest} על כונן אחר מהמסד — הגיבוי לא נכתב")


def backup_once(db_path: Path, dest: Path, now: datetime, reserve_gb: int = DEFAULT_RESERVE_GB,
                last_good: str | None = None, other_disk: bool = False) -> dict:
    """גיבוי אחד ל-`dest/db`, מאומת. מחזיר {file, bytes, reserve_bytes, pruned}; כל כשל — חריגה.
    `other_disk`: היעד נבחר כשהוא על כונן אחר, ולכן נבדק כאן שהוא עדיין שם (#2574)."""
    from server.system.backup import consistent_db_copy   # noqa: PLC0415 — כמו ב-backup-server.sh

    if other_disk:
        _require_other_disk(dest, db_path.parent)
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        data = consistent_db_copy(src)        # integrity_check == ok על העותק, אחרת חריגה
    finally:
        src.close()
    db_dir = dest / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    # התקרה חלה גם כאן: ערך ישן, או יעד שעבר לכונן קטן יותר, אינם אוכלים את מקום האימג'ים.
    reserve = min(reserve_gb * GB, drive_cap_bytes(db_dir))
    if len(data) > reserve:
        raise ReserveTooSmall(f"המקום השמור ({_size(reserve)}) קטן מגיבוי אחד ({_size(len(data))}) — הגיבוי לא "
                              "נכתב ושום דבר לא נמחק. מגדילים את המקום השמור בהגדרות")
    target = db_dir / f"imagesrv-{now.strftime(_STAMP)}.db"
    sizes = {path.name: size for path, size in _backups(db_dir)}
    protected = _protected([*sizes, target.name], target.name, last_good)
    needed = len(data) + sum(sizes.get(name, 0) for name in protected - {target.name})
    if needed > reserve:
        raise ReserveTooSmall(f"המקום השמור ({_size(reserve)}) קטן ממה שחייב להישמר: הגיבוי החדש, הגיבוי התקין "
                              f"האחרון וגיבוי מכל אחד מ-{KEEP_DAILY} הימים האחרונים ({_size(needed)}) — הגיבוי לא "
                              "נכתב ושום דבר לא נמחק. מגדילים את המקום השמור בהגדרות")
    _atomic_write(target, data)
    # ראיה חיובית מהיעד עצמו: מה שנקרא בחזרה הוא מה שנכתב, וה-DB שביעד נפתח ותקין.
    back = _read_back(target)
    if hashlib.sha256(back).hexdigest() != hashlib.sha256(data).hexdigest():
        raise BackupError(f"הקובץ ביעד ({target}) אינו מה שנכתב")
    verdict = _integrity(back)
    if verdict != "ok":
        log.warning("backup integrity_check on %s: %s", target, verdict)   # #2543: פלט sqlite ליומן
        raise BackupError(f"integrity_check על {target} לא החזיר ok — {FAILED_TAIL}")
    return {"file": target.name, "bytes": len(data), "reserve_bytes": reserve,
            "pruned": prune(db_dir, protected, reserve)}


def read_status(data_dir: Path) -> dict | None:
    """הסטטוס האחרון, או None כשאין קובץ. קובץ שאינו JSON — חריגה (לא "אין גיבוי")."""
    path = data_dir / STATUS_NAME
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("הסטטוס אינו אובייקט")
    return value


def _write_status(data_dir: Path, status: dict) -> None:
    # CodeQL #860: 0600 כמו עותק המסד — הקורא היחיד (`read_status` בשרת) רץ כ-root.
    _atomic_write(data_dir / STATUS_NAME,
                  (json.dumps(status, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def run(data_dir: Path, dest: str, now: datetime | None = None, reserve_gb: int = DEFAULT_RESERVE_GB,
        other_disk: bool = False) -> dict:
    """גיבוי אחד + קובץ הסטטוס. מחזיר את הסטטוס שנכתב (`ok` אומר אם הצליח; `too_small` — אם
    הכישלון הוא מקום שמור קטן מעותק אחד, הכתום)."""
    now = now or datetime.now(timezone.utc)
    try:
        previous = read_status(data_dir) or {}
    except (OSError, ValueError):
        previous = {}
    same_dest = previous.get("dest") == dest
    # `file` של הצלחה לפני שהיה `last_good` (עדכון גרסה): גם הוא אומת בזמנו.
    last_good = (previous.get("last_good") or (previous.get("file") if previous.get("ok") else None)) if same_dest else None
    status = {"at": now.isoformat(timespec="seconds"), "dest": dest, "ok": False, "too_small": False,
              "file": None, "bytes": None, "error": None, "pruned": [], "last_good": last_good,
              "last_ok": previous.get("last_ok") if same_dest else None}
    try:
        status.update(backup_once(data_dir / "imagesrv.db", Path(dest), now, reserve_gb, last_good, other_disk))
        status["ok"] = True
        status["last_ok"] = status["at"]
        status["last_good"] = status["file"]
    except ReserveTooSmall as exc:
        status.update(too_small=True, error=str(exc))
    except BackupError as exc:
        status["error"] = str(exc)
    except Exception as exc:                  # noqa: BLE001 — כל כשל נכתב, ויציאה 1
        # #2543: הטקסט (נתיב, strerror, sqlite) ליומן השרת; בקובץ שהקונסולה קוראת — משפט קבוע.
        log.warning("backup to %s failed: %s: %s", dest, type(exc).__name__, exc)
        status["error"] = FAILED
    _write_status(data_dir, status)
    return status


def parse_time(value: object) -> str | None:
    """"HH:MM" תקין, או None."""
    return value if isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) else None


def parse_reserve(value: object) -> int | None:
    """מספר שלם של GB, לפחות `MIN_RESERVE_GB`, או None. התקרה תלויה בכונן — אצל הקורא."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        value = int(value)
    return value if isinstance(value, int) and MIN_RESERVE_GB <= value < 10 ** 6 else None


def effective_dest(stored: str | None, raw_target: str | None, data_dir: Path) -> str:
    """לאן הגיבוי כותב. בלי תיקייה שנבחרה, ועם יעד SMB/NFS שנשמר לפני שהוסרו — התיקייה הקבועה."""
    stored = (stored or "").strip()
    try:
        kind = (json.loads(raw_target or "null") or {}).get("type")
    except (ValueError, AttributeError):
        kind = None
    if not stored or kind in LEGACY_KINDS:
        return (Path(data_dir) / DEFAULT_DIR).as_posix()
    return stored


def parse_enabled(value: object) -> bool:
    """#2865: כבוי רק כשנשמר "false" במפורש."""
    return value != "false"


def _config(data_dir: Path) -> dict:
    """מה שהריצה צריכה מהמסד, בקריאה בלבד. מסד שלא נקרא — חריגה, לא "אין מה לעשות"."""
    conn = sqlite3.connect(f"file:{(data_dir / 'imagesrv.db').as_posix()}?mode=ro", uri=True)
    try:
        keys = (SETTING, TARGET_SETTING, TIME_SETTING, RESERVE_SETTING, ENABLED_SETTING)
        rows = dict(conn.execute(f"SELECT key, value FROM settings WHERE key IN ({','.join('?' * len(keys))})",
                                 keys).fetchall())
        changed = conn.execute(f"SELECT max(ts) FROM journal WHERE event IN ({','.join('?' * len(IMAGE_EVENTS))})",
                               IMAGE_EVENTS).fetchone()[0]
        busy = busy_db.reason(conn)
    finally:
        conn.close()
    return {"dest": effective_dest(rows.get(SETTING), rows.get(TARGET_SETTING), data_dir),
            "other_disk": expects_other_disk(rows.get(SETTING), rows.get(TARGET_SETTING)),
            "time": parse_time(rows.get(TIME_SETTING)) or DEFAULT_TIME,
            "reserve_gb": parse_reserve(rows.get(RESERVE_SETTING)) or DEFAULT_RESERVE_GB,
            "enabled": parse_enabled(rows.get(ENABLED_SETTING)),
            "changed": changed, "busy": busy}


def _moment(value: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def last_slot(at: str, now: datetime) -> datetime:
    """המופע האחרון של "HH:MM" בשעון המקומי של השרת שכבר הגיע."""
    local = now.astimezone()
    hour, minute = (int(part) for part in at.split(":"))
    slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot <= local else slot - timedelta(days=1)


def due(status: dict | None, config: dict, now: datetime) -> str | None:
    """למה צריך גיבוי עכשיו, או None. "הניסיון האחרון" נקבע לפי הקובץ שהריצה עצמה כותבת."""
    status = status or {}
    last = _moment(status.get("at"))
    if last is None or status.get("dest") != config["dest"]:
        return "first"
    changed = _moment(config.get("changed"))
    # `>=`: `at` נלקח לפני העותק, ואירוע באותה שנייה אולי לא נכנס אליו. עדיף גיבוי אחד מיותר.
    if changed is not None and changed >= last and (now - last).total_seconds() >= COALESCE_SECONDS:
        return "image"
    if last < last_slot(config["time"], now):
        return "daily"
    if not status.get("ok") and (now - last).total_seconds() >= RETRY_SECONDS:
        return "retry"
    return None


def _wait(data_dir: Path, status: dict | None, reason: str, now: datetime) -> None:
    """סבב רץ: נכתב רק כשהסיבה השתנתה, לא כל דקה. שאר הקובץ — וה-`at` שקובע שהגיבוי עוד חייב — נשאר."""
    status = status or {}
    if status.get("waiting") != reason:
        _write_status(data_dir, {**status, "waiting": reason,
                                 "waiting_since": now.isoformat(timespec="seconds")})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ImageSrv: database backup -- daily, and after an image change (#1956)")
    parser.add_argument("--data-dir", default="/var/lib/imagesrv")
    parser.add_argument("--now", action="store_true", help="back up now: ignore the schedule and a running round")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    now = datetime.now(timezone.utc)
    try:
        previous = read_status(data_dir)
    except (OSError, ValueError):
        previous = None                       # הריצה כותבת קובץ תקין מחדש
    try:
        config = _config(data_dir)
    except sqlite3.Error as exc:
        _write_status(data_dir, {**(previous or {}), "at": now.isoformat(timespec="seconds"), "ok": False,
                                 "error": f"המסד לא נקרא — {FAILED_TAIL}"})   # #2543: הטקסט ב-stderr
        print(f"backup_job: FAILED to read the database: {exc}", file=sys.stderr)
        return 1
    if not args.now and not config["enabled"]:
        # #2865: כבוי בקונסולה — לא יומי ולא בשינוי אימג'. נאמר פעם אחת (`disabled` בקובץ הסטטוס),
        # לא כל דקה; `at` נשאר, ולכן בהדלקה `due` מחליט כרגיל (יומי שהוחמץ = גיבוי מיד).
        if not (previous or {}).get("disabled"):
            _write_status(data_dir, {**(previous or {}), "disabled": True,
                                     "disabled_since": now.isoformat(timespec="seconds")})
            print(f"backup_job: backups are off (setting {ENABLED_SETTING}=false): "
                  "no daily backup and no backup on an image change, until it is turned on")
        return 0
    if previous and "disabled" in previous:     # הודלק שוב: הסימון יורד, והריצה ממשיכה כרגיל
        previous = {k: v for k, v in previous.items() if not k.startswith("disabled")}
        _write_status(data_dir, previous)
    if not args.now:
        if due(previous, config, now) is None:
            if previous and "waiting" in previous:      # השעה הוזזה בזמן ההמתנה: אין למה לחכות, והבריאות לא תאמר ש"ממתין"
                _write_status(data_dir, {k: v for k, v in previous.items() if not k.startswith("waiting")})
            return 0
        if config["busy"]:
            _wait(data_dir, previous, config["busy"], now)
            return 0
    dest = config["dest"]
    status = run(data_dir, dest, now, config["reserve_gb"], config["other_disk"])
    if not status["ok"]:
        print(f"backup_job: FAILED to {dest}: {status['error']}", file=sys.stderr)
        return 1
    print(f"backup_job: {status['file']} ({status['bytes']} bytes) -> {dest}/db, pruned {len(status['pruned'])}")
    return 0


# --- הבריאות -------------------------------------------------------------------


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "לפני פחות מדקה"
    if minutes < 120:
        return f"לפני {minutes} דק'"
    return f"לפני {minutes // 60} שעות"


def health_check(conn: sqlite3.Connection, data_dir: Path | None, timer_state: str,
                 now: datetime | None = None) -> dict:
    """שורת "גיבוי המסד" ב-`/health`. `timer_state` — `systemctl is-active` של
    `imagesrv-backup.timer` ("" = לא ניתן לבדוק). {"id","label","state","detail"}."""
    from server.system.backup_target import disk_warning, load   # noqa: PLC0415 — backup_target מייבא את המודול הזה
    from server.db import get_setting   # noqa: PLC0415

    def row(state: str, detail: str) -> dict:
        return {"id": "backup", "label": LABEL, "state": state, "detail": detail}

    now = now or datetime.now(timezone.utc)
    if data_dir is None:
        return row("unknown", "תיקיית הנתונים אינה ידועה — לא נבדק")
    if not parse_enabled(get_setting(conn, ENABLED_SETTING)):
        # #2865: בחירת המפעיל, לא תקלה — "off" כמו שאר המתגים; הפס הכתום בבית וב-DCUI אומר את זה.
        return row("off", "כבוי בהגדרות — אין גיבוי יומי ואין גיבוי בשינוי אימג'. הגיבויים הקיימים נשארים")
    dest = effective_dest(get_setting(conn, SETTING), get_setting(conn, TARGET_SETTING), data_dir)
    if timer_state and timer_state != "active":
        return row("bad", f"הגיבוי אינו מתוזמן ({TIMER_UNIT}: {timer_state}) — "
                          f"systemctl enable --now {TIMER_UNIT}")
    try:
        status = read_status(data_dir)
    except (OSError, ValueError) as exc:
        # #2510: טקסט החריגה ליומן השרת; בשורת הבריאות — משפט קבוע.
        log.warning("backup status %s unreadable: %s", data_dir / STATUS_NAME, exc)
        return row("bad", "קובץ הסטטוס של הגיבוי לא נקרא — הפרטים ביומן השרת")
    waiting = f" · ממתין: {status['waiting']}" if status and status.get("waiting") else ""
    # תיקייה על כונן המסד (או שלא נבדק), או יעד רשת שהוסר — כתום גם כשהגיבוי מצליח.
    warning = disk_warning(load(conn))
    prefix = f"{warning} · " if warning else ""
    if status is None or status.get("dest") != dest:
        return row("warn", f"{prefix}עוד לא רץ גיבוי אל {dest} — הגיבוי הראשון רץ תוך דקה{waiting}")
    last_ok = status.get("last_ok")
    if not status.get("ok"):
        tail = f" · ההצלחה האחרונה: {last_ok}" if last_ok else " · אין גיבוי מוצלח ליעד הזה"
        if status.get("too_small"):
            return row("warn", f"{status.get('error')}{tail}")
        return row("bad", f"הגיבוי האחרון ({status.get('at')}) נכשל: {status.get('error')}{tail}{waiting}")
    try:
        age = (now - datetime.fromisoformat(str(last_ok))).total_seconds()
    except (TypeError, ValueError):
        return row("bad", f"זמן הגיבוי האחרון אינו קריא ({last_ok!r}) — לא נבדק")
    if age < 0:
        return row("warn", f"זמן הגיבוי האחרון ({last_ok}) בעתיד — השעון זז? לא נבדק")
    if age > STALE_SECONDS:
        return row("warn", f"ישן — הגיבוי האחרון הצליח {_ago(age)} ({status.get('file')}, {dest}){waiting}")
    done = f"גובה {_ago(age)} — {status.get('file')} ב-{dest}{waiting}"
    return row("warn", f"{prefix}{done}") if warning else row("ok", done)


if __name__ == "__main__":
    sys.exit(main())
