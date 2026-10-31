"""סחיפת אזורי עבודה יתומים בשורש ספריית האימג'ים (‏#88).

קליטה מזרימה את קבצי המחיצות ל-`.capture-<task>` (‏tasks.staging_dir),
וייבוא מחלץ ארכיון ל-`.import-<token>` (‏archive.STAGING_PREFIX). שניהם
אמורים להתפנות בסוף: הקליטה ב-rename לספרייה, בביטול, ובאימות שנכשל;
הייבוא ב-`finally`. מה שנשאר על הדיסק הוא בדיוק המקרה שאין לו ניקוי —
השרת מת באמצע, ואיש לא הריץ אותו. נמדד במעבדה (‏2026-08-29): חמישה
`.capture-*` בני 1.4GB יחד, מול ארבעה אימג'ים אמיתיים.

‏#71 כבר הפך אותם לבלתי נראים (‏`scan` מדלגת על נתיב שרכיב שלו מתחיל
בנקודה), ולכן מה שנשאר אינו סכנת הצגה אלא מקום דיסק. הדיסק הזה הוא
אותו דיסק שמשרת שידור לכיתה, ושרת שנגמר לו המקום באמצע סבב נכשל
כמשהו שנראה אחרת לגמרי.

**למה סחיפה אוטומטית ולא דיווח בלבד (עיקרון 7).**
עיקרון 7 מעמיד הקלדת שם לפני פעולה הרסנית — מחיקת אימג', עצירת סבב.
ההקלדה שם שומרת על **שיקול דעת אנושי**: רק אדם יודע אם האימג' הזה עוד
נחוץ לקורס. ל-`.capture-tsk_0930` אין שיקול דעת כזה להוסיף: הטכנאי
אינו יכול לדעת אם המשימה חיה, וגם אינו אמור — היחיד שיודע הוא טבלת
המשימות, והשרת קורא אותה טוב ממנו. הקלדת שם כזה היא טקס בלי בטיחות.

ושתי עובדות נוספות:

1. אזור עבודה אינו נתונים של אדם אלא שטח עבודה של השרת עצמו, והשרת
   כבר מוחק אותו בלי לשאול איש בשלושה מסלולים (ביטול קליטה, אימות
   שנכשל, ‏`finally` של ייבוא). כאן לא נולדת יכולת הרסנית חדשה — אותו
   ניקוי בדיוק מגיע למסלול היחיד שבו התהליך מת לפני ה-`finally` שלו.
2. דיווח שאיש לא קרא אינו תיקון. הכשל ב-#88 הוא שקט מטבעו; אם התיקון
   מותנה בכך שמפעיל יפתח מסך וילחץ לפני הסבב הבא, הדיסק נשאר מלא.

מה ש**כן** נשאר לאדם הוא כל מה שאי אפשר להוכיח (ראו `plan`): שם לא
מוחקים כלום, ורושמים ביומן מה נשאר ולמה. סוחפים את מה שהוכח, מדווחים
על מה שלא — כל מנגנון במקום שבו הוא באמת עוזר.

**‏#1737 — מה שננטש, ולא רק מה שמת (הכרעת נדב 18/10: מחיקה אוטומטית).**
עד כאן הסחיפה רצה רק בעליית השרת, ושלושה סוגים נשארו לנצח: קליטה שאין לה
שורה, ‏`.import-*` שהתהליך שלו מת, ו-`.incoming/<אימג'>` של משיכה שנכשלה
(נשמר בכוונה, להמשך ב-Range). מעכשיו היא רצה **גם פעם ביום** מתוך שעון
החדר (‏`housekeeping`), ולשלושתם יש ראיה חיובית אחת משותפת: **אף בייט לא
נכתב תחתם `STALE_AFTER_S` (‏24 שעות)** — הזמן החדש ביותר בכל העץ, ולא של
התיקייה לבדה (קובץ שגדל אינו משנה את ה-mtime של התיקייה). זה עונה על שתי
ההסתייגויות למטה: שרת שני מול אותו שורש, או בסיס משוחזר מגיבוי — אם הם
חיים, הם כותבים. וכל משימה **פתוחה** נשארת גם כשהיא שקטה (‏"רק אחרי שאומת
שהמשימה סגורה"); ‏`stat` שנכשל באמצע העץ = לא נמדד = נשאר (עיקרון 5).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

from server.db import journal
from server.library.images import ImageLibrary
from server.core.tasks import OPEN_STATES

log = logging.getLogger("imagesrv.work_areas")

#: אזור עבודה של קליטה. חייב להתאים ל-`tasks.staging_dir`; יש בדיקה.
CAPTURE_AREA = re.compile(r"^\.capture-([A-Za-z0-9_-]+)$")
#: ייבוא tar (‏`archive.STAGING_PREFIX` + אסימון הקס); יש בדיקה.
IMPORT_AREA = re.compile(r"^\.import-([0-9a-f]+)$")
#: הקובץ הזמני של העלאה מהקונסולה (‏`console_library.UPLOAD_PREFIX`); יש בדיקה.
UPLOAD_AREA = re.compile(r"^\.upload-([A-Za-z0-9_]+)\.upload$")
#: אזור הביניים של משיכה מהמשני (‏`storage_transfer.INCOMING`); יש בדיקה.
INCOMING = ".incoming"
#: מצבי העברה שבהם משיכה עוד כותבת (‏`storage_transfer.ACTIVE_STATES`); יש בדיקה.
PULL_ACTIVE = ("queued", "sending", "verifying")

#: מצבי משימה סופיים. לא רק "המשימה נגמרה" אלא **אף אחד לא יכתוב לשם
#: יותר**: ‏`capture.task_for` מחזיר 404 לכל העלאה למשימה שאינה
#: ‏pending/running, ולכן אזור העבודה של משימה במצב כזה סגור לכתיבה
#: בקוד, לא בתקווה.
DEAD_STATES = ("done", "failed", "cancelled")

#: ‏#1737: כמה זמן בלי שום כתיבה עד שאזור עבודה בלי בעלים מוכח נחשב נטוש.
#: ‏24 שעות — ברירת המחדל שנדב קבע. הזרם האיטי ביותר שלנו כותב כל שניות
#: (‏`--speed-limit` של curl, תקרות הזרם של #1457), ולכן יממה של שקט אינה
#: "איטי" אלא "מת".
STALE_AFTER_S = 24 * 3600


def _size(path: Path) -> int:
    """כמה בייטים תופס אזור העבודה. קובץ שלא נמדד — לא נספר, לא מנחשים."""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError as exc:
            log.warning("could not size %s: %s", path, exc)
            return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError as exc:                       # קובץ שנעלם תוך כדי
            log.warning("could not size %s: %s", item, exc)
    return total


def human_bytes(size: int) -> str:
    """‏1478000000 → "1.4GB". ליומן, שנקרא בעיניים ולא בסקריפט."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    raise AssertionError                             # pragma: no cover


def idle_seconds(path: Path, now: float) -> float | None:
    """כמה שניות עברו מהכתיבה האחרונה **בכל העץ**, או `None` כשמשהו בו לא
    נמדד — ואז הקורא משאיר (עיקרון 5: "לא נמדד" אינו "ישן")."""
    def fail(exc: OSError) -> None:
        raise exc
    try:
        newest = path.lstat().st_mtime
        if path.is_dir() and not path.is_symlink():
            for top, dirs, files in os.walk(path, onerror=fail):
                for name in dirs + files:
                    newest = max(newest, os.lstat(os.path.join(top, name)).st_mtime)
    except OSError as exc:
        log.warning("could not read write times under %s: %s", path, exc)
        return None
    return now - newest


def _stale(path: Path, now: float) -> bool:
    idle = idle_seconds(path, now)
    return idle is not None and idle >= STALE_AFTER_S


def _active_pulls(conn) -> set[str] | None:
    """מזהי האימג'ים שמשיכה עוד כותבת אליהם, או `None` כשלא נקרא."""
    try:
        rows = conn.execute(
            "SELECT image_id FROM storage_transfers WHERE direction = 'pull' AND state IN "
            f"({', '.join('?' * len(PULL_ACTIVE))})", PULL_ACTIVE).fetchall()
    except Exception as exc:                                 # noqa: BLE001
        log.warning("could not read active pulls: %s", exc)
        return None
    return {row[0] for row in rows}


def plan(conn, images_root: str | Path, now: float | None = None,
         ) -> tuple[list[tuple[Path, str]], list[tuple[Path, str]]]:
    """‏([(מה נמחק, התווית ביומן)], [(מה נשאר, למה)]) — ההחלטה, בלי לגעת בדיסק.

    הכללים, ואף אחד מהם אינו "לא ראינו סימן שהוא חי" (עיקרון 5):

    * **לא מתחיל בנקודה — לא נוגעים.** אלה האימג'ים עצמם, והדיסק הוא
      מקור האמת (עיקרון 3). סחיפה שטועה כאן בלתי הפיכה. אימג'ים אינם
      נמחקים אוטומטית לעולם (#1737).
    * **‏`.capture-<task>` שיש לו שורה במצב סופי — נמחק.** זו הראיה
      החיובית: השורה קיימת, ומצבה אומר שהמשימה נגמרה ושהעלאה אליה כבר
      נדחית.
    * **משימה פתוחה (pending/running) או ממתינה לאימות — נשארת**, גם כשהיא
      שקטה: היא עשויה עוד לחזור אחרי אתחול.
    * **‏`.capture-<task>` בלי שורה, ‏`.import-*`, ‏`.upload-*.upload`,
      ‏`.incoming/<אימג'>` בלי משיכה פעילה — נמחקים רק אחרי `STALE_AFTER_S`
      בלי שום כתיבה** (#1737). לפני כן — נשארים, בנימוק הקודם: שורה שאינה
      קיימת אינה ראיה שהמשימה מתה (‏--data-dir אחר, בסיס משוחזר מגיבוי),
      ולייבוא אין רשומה בשום מקום. שרת חי כותב; יממה של שקט היא הראיה.
    * **כל השאר נשאר** — קישור, קובץ שאינו העלאה, מצב משימה שאינו מוכר,
      אזור עבודה שאין לו כלל (‏`.vmupdate` מנוהל בעצמו, ‏#1334).
    """
    root = Path(images_root)
    now = time.time() if now is None else now
    doomed: list[tuple[Path, str]] = []
    kept: list[tuple[Path, str]] = []
    if not root.is_dir():
        return doomed, kept
    for path in sorted(root.iterdir()):
        if not path.name.startswith(ImageLibrary.WORK_AREA_PREFIX):
            continue                    # אימג' אמיתי, או קובץ של המפעיל
        upload = UPLOAD_AREA.match(path.name)
        if upload is not None and path.is_file() and not path.is_symlink():
            if _stale(path, now):
                doomed.append((path, f"upload:{upload.group(1)}"))
            else:
                kept.append((path, "העלאה שנכתבה ביממה האחרונה"))
            continue
        if path.is_symlink() or not path.is_dir():
            kept.append((path, "אינו תיקייה רגילה"))
            continue
        if path.name == INCOMING:
            _plan_incoming(conn, path, now, doomed, kept)
            continue
        imported = IMPORT_AREA.match(path.name)
        if imported is not None:
            if _stale(path, now):
                doomed.append((path, f"import:{imported.group(1)}"))
            else:
                kept.append((path, "אזור עבודה שאין לו רשומת משימה"))
            continue
        match = CAPTURE_AREA.match(path.name)
        if match is None:
            kept.append((path, "אזור עבודה שאין לו רשומת משימה"))
            continue
        row = conn.execute(
            "SELECT state FROM tasks WHERE id = ?", (match.group(1),)
        ).fetchone()
        if row is None:
            if _stale(path, now):
                doomed.append((path, match.group(1)))
            else:
                kept.append((path, "אין שורה למשימה הזאת בבסיס הנתונים"))
        elif row[0] in OPEN_STATES:
            kept.append((path, f"המשימה עדיין {row[0]}"))
        elif row[0] == "verifying":
            # ‏#1470: האימג' ממתין לאימות ברקע — ‏`reconcile_on_start` מריץ אותו מחדש.
            kept.append((path, "האימג' ממתין לאימות"))
        elif row[0] in DEAD_STATES:
            doomed.append((path, match.group(1)))
        else:
            kept.append((path, f"מצב משימה לא מוכר: {row[0]}"))
    return doomed, kept


def _plan_incoming(conn, incoming: Path, now: float,
                   doomed: list[tuple[Path, str]], kept: list[tuple[Path, str]]) -> None:
    """‏`.incoming/<אימג'>`: משיכה שנכשלה משאירה אותו להמשך (‏Range, ‏#1456).
    משיכה פעילה — נשאר תמיד; אחרת — נמחק אחרי יממה בלי כתיבה. ‏`.incoming`
    עצמו נשאר: הוא התיקייה של המנגנון, לא שארית."""
    active = _active_pulls(conn)
    for path in sorted(incoming.iterdir()):
        if path.is_symlink() or not path.is_dir():
            kept.append((path, "אינו תיקייה רגילה"))
        elif active is None:
            kept.append((path, "לא נקרא אם משיכה עוד כותבת לכאן"))
        elif path.name in active:
            kept.append((path, "משיכה פעילה"))
        elif _stale(path, now):
            doomed.append((path, f"incoming:{path.name}"))
        else:
            kept.append((path, "משיכה שנכשלה ביממה האחרונה — נשמר להמשך"))


def sweep(conn, images_root: str | Path, now: float | None = None) -> dict:
    """מוחק את מה ש-`plan` הוכיח שהוא יתום, ורושם ביומן. חוזר לצורך בדיקה."""
    root = Path(images_root)
    doomed, kept = plan(conn, root, now)
    swept: list[str] = []
    freed = 0
    for path, label in doomed:
        size = _size(path)
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            log.warning("could not sweep %s: %s", path, exc)
        # ‏`rmtree` יכול גם להצליח חלקית. "נמחק" נקבע בקריאה חוזרת
        # מהדיסק ולא בהיעדר חריגה — אחרת נספור מקום שלא התפנה (עיקרון 5).
        if path.exists():
            kept.append((path, "המחיקה לא הושלמה"))
            continue
        swept.append(label)
        freed += size

    def shown(path: Path) -> str:
        return path.relative_to(root).as_posix()

    if swept:
        journal(conn, "work_area_swept",
                f"swept={len(swept)} freed={human_bytes(freed)} "
                f"ids={','.join(swept)}")
        log.info("swept %d orphan work areas, freed %d bytes", len(swept), freed)
    if kept:
        journal(conn, "work_area_kept",
                "; ".join(f"{shown(path)}: {why}" for path, why in kept))
    return {"swept": swept, "freed_bytes": freed,
            "kept": [(shown(p), why) for p, why in kept]}
