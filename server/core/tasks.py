"""משימות ישירות — שאילתות טהורות, בלי תלות ב-FastAPI.

המודול הזה קיים כדי לשבור מעגל: `hello` צריך לדעת אם יש משימה למכונה,
ו-`capture` צריך את אותה שאילתה אבל גם את ההקשר של האפליקציה. שני
הצדדים מייבאים מכאן.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sqlite3
from pathlib import Path

#: אורך האסימון בבתים. ‏`token_hex(24)` = 48 תווים = 192 סיביות.
#:
#: להשוואה, `task_id` הוא `token_hex(2)` — **65,536 ערכים**, וזה מספיק
#: כדי לנחש אותו (#535). המזהה הוא שם, לא סוד; האסימון הוא הסוד.
TOKEN_BYTES = 24


def new_token() -> str:
    return secrets.token_hex(TOKEN_BYTES)

#: ‏#1576: מאיפה הוזמנה המשימה (עמודת `tasks.origin`).
ORIGIN_CONSOLE = "console"
ORIGIN_BUILD_GUI = "build_gui"


def request_origin(request) -> str:
    """‏`build_gui` כשהבקשה נכנסה בערוץ הסוכן -- ראוטר הקיוסק על פורט הסוכן,
    שמסמן אותה (‏kiosk.mark_build_gui) -- אחרת `console`. **השרת קובע, לא
    הלקוח**: גואי ישן או נתיב עתידי בלי שדה עדיין מסומן לפי הערוץ (‏#1576,
    סקירת אבטחה)."""
    return getattr(request.state, "task_origin", ORIGIN_CONSOLE)


#: מצבים שבהם המשימה עדיין מחכה למכונה או רצה עליה.
OPEN_STATES = ("pending", "running")

#: ‏#980 §4: שלבי הקליטה שהסוכן מדווח (‏`stage` בדיווח ההתקדמות), לפי הסדר:
#: בדיקת NTFS → כיווץ → זרם + sha256 → החזרת הגודל → אימות. ‏"PXE" אינו שלב
#: של הסוכן — הוא `state: pending`, לפני שהסוכן לקח את המשימה.
CAPTURE_STAGES = ("ntfs_check", "shrink", "stream", "restore_size", "verify")

#: הכותרת שבה הסוכן מחזיר את האסימון. **כותרת ולא שדה בגוף**: העלאת
#: מחיצה היא זרם בייטים גולמי בלי גוף JSON, ולכן זה המקום היחיד
#: ששלושת המסלולים חולקים.
TOKEN_HEADER = "X-Imagesrv-Task-Token"


#: מזהה משימה כרכיב נתיב: אותה מחלקת תווים כמו `work_areas.CAPTURE_AREA`.
#: השרת מייצר `tsk_<hex>`; הכלל רחב מזה בכוונה (מזהי בדיקה) — מה שהוא
#: אוסר הוא מה שחשוב: נקודה, לוכסן, לוכסן הפוך, נקודתיים ובייט אפס.
TASK_ID = re.compile(r"[A-Za-z0-9_-]+")


def staging_dir(library_root: Path, task_id: str) -> Path:
    """איפה נאספים קבצי הקליטה עד שהמניפסט מאומת.

    ‏`task_id` מגיע בנתיב ה-URL. ‏`capture.task_for` כבר דורש שהוא קיים
    ב-DB, אבל הנתיב נבנה כאן — ולכן כאן הגבול: מזהה שאינו רכיב יחיד
    נדחה בשמו (‏ValueError), ולא "מתוקן". אחריו, הנתיב שנפתר (‏realpath)
    חייב להיות ילד ישיר של שורש הספרייה — הצורה ש-CodeQL מזהה כמנקה
    ‏(py/path-injection), והחגורה השנייה אם הכלל למעלה יורחב מחר.
    """
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise ValueError(f"task id is not a single path component: {task_id!r}")
    root = os.path.realpath(library_root)
    full = os.path.realpath(os.path.join(root, f".capture-{task_id}"))
    if not full.startswith(root + os.sep) or os.path.dirname(full) != root:
        raise ValueError(f"staging folder of {task_id!r} falls outside the library root")
    return Path(full)


def discard_failed_staging(conn: sqlite3.Connection, library_root: Path, task_id: object) -> None:
    """‏#1915: הסוכן דיווח `failed` (‏`/agent/progress`) — הדלת שבה BM-F04z
    עבר בפועל, ו-`capture._fail` אינו בה. האזור נמחק כמו בביטול ובאימות
    שנכשל — רק בקליטה, ורק כשהיא באמת `failed` עכשיו (השורה, לא הדיווח)."""
    row = conn.execute("SELECT id, type, state FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is not None and row["type"] == "capture" and row["state"] == "failed":
        shutil.rmtree(staging_dir(library_root, row["id"]), ignore_errors=True)


def active_task(conn: sqlite3.Connection, mac: str) -> dict | None:
    """המשימה הפתוחה של המכונה, במבנה של שדה `task` בממשק 3."""
    row = conn.execute(
        "SELECT * FROM tasks WHERE mac = ? AND state IN ('pending', 'running')"
        " ORDER BY created_at LIMIT 1",
        (mac,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "type": row["type"], "disk": row["disk"],
        # ‏#1901: ה-serial שנרשם בהזמנה; ``None`` = לא ידוע. הסוכן מסרב לקלוט
        # כשה-serial של `/dev/<disk>` שונה (אחרת: שם ההתקן לבדו, ‏G18).
        "disk_serial": row["disk_serial"],
        "image_id": row["image_id"], "name": row["name"],
        "description": row["description"], "folder": row["folder"],
        # ‏#530: ה-token נמסר **רק** למכונה שה-hello שלה תואם את ה-MAC
        # של המשימה, וכל כתיבה על המשימה דורשת אותו בחזרה. בלעדיו
        # כל פונה שמכיר `task_id` העלה אימג' משלו (#530), ביטל ביטול
        # (#532), או הרג קליטה חיה (#535).
        "token": row["token"],
    }


def claim_task(conn: sqlite3.Connection, task_id: str, token: str | None) -> dict | None:
    """המשימה **הפתוחה** ששייכת ל-``token`` הזה, או ``None``.

    שלוש שאלות שונות, ואסור לקפל אותן: משימה שאינה קיימת, משימה
    שנסגרה, ופונה שאינו בעליה. הקורא מבחין ביניהן לפי מה שמוחזר
    כאן ולפי מה שהוא כבר יודע — הפונקציה הזו עונה רק על **הצירוף**.

    ההשוואה היא ב-``secrets.compare_digest``: השוואת מחרוזות רגילה
    יוצאת מוקדם על התו הראשון שאינו תואם, וזה מדליף את האסימון
    ניחוש-אחר-ניחוש למי שמודד זמן.
    """
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND state IN ('pending', 'running')",
        (task_id,),
    ).fetchone()
    if row is None or not row["token"]:
        return None
    if not secrets.compare_digest(str(row["token"]), str(token)):
        return None
    return dict(row)
