"""`POST /api/console/grub-password` — סיסמת ה-GRUB למנהל (#2450).

ה-GRUB של השרת ושל התחנות נעול באותה סיסמה (`server/system/grub_password.py`,
#2437): `e` ו-`c` בכל תפריט, וערך השחזור של השרת ("ImageSrv recovery") — הכרעת
נדב 04/11, כי בלעדיה מי שעומד מול השרת מקבל root. כשהשרת עצמו צריך שחזור, אין
SSH בייצור (#1266) ו-Alt+Fn אינו עובר (#2438) — והדרך של הטכנאי לסיסמה היא כאן,
מראש, בהגדרות ("סיסמת GRUB" → "הצג").

**admin בלבד** (deploy מקבל 403 עוד לפני כן מ-`console_only`), ו-**כל צפייה ביומן**
(`grub_password_viewed`, מי — בלי הערך). POST ולא GET: חשיפה של סוד היא פעולה,
לא קריאה שדפדפן או proxy שומרים — והתשובה `no-store`. אינו ברשימת ה-relay בין
השרתים (`interserver/api.py`): לכל שרת סיסמה משלו, ורואים אותה מהקונסולה שלו.

#2454: `POST /api/console/grub-password/saved` — "שמרתי אותה מחוץ לשרת". עד הסימון
שורת "סיסמת GRUB" בבריאות צהובה (`reminder`). הסימון זוכר את **הקובץ** (inode + mtime),
לא את הערך: סיסמה שנוצרה אחריו — התקנה חדשה, שדרוג שיצר אותה, החלפה ביד, גיבוי
ששוחזר לשרת שהותקן מחדש — אינה זו שנשמרה, והשורה צהובה שוב. הסימון נכנס לגיבוי
כמו כל הגדרה; הסיסמה — לא.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from boot.grub_lock import GRUB_USER
from server.agent.api import ServerContext
from server.console import auth
from server.db import get_setting, journal, set_setting
from server.system import grub_password

log = logging.getLogger("imagesrv.console_grubpw")
#: #2543: הקובץ לא נקרא — ה-strerror/הנתיב ליומן השרת, ללקוח המשפט הזה.
UNREAD = "סיסמת ה-GRUB לא נקראה — הפרטים ביומן השרת"

#: #2454: הגדרה ב-DB — טביעת הקובץ שאדמין סימן עליו "שמרתי" (לא הסיסמה).
SAVED_KEY = "grub_password_saved"


def fingerprint(password_file: str | Path) -> str:
    """הקובץ, לא התוכן: inode ו-mtime. ``write_atomic`` נותן לכל כתיבה inode חדש."""
    st = os.stat(password_file)
    return f"{st.st_ino}:{st.st_mtime_ns}"


def reminder(conn, password_file: str | Path | None) -> tuple[str, str] | None:
    """שורת הבריאות (#2454): (מצב, פירוט), או None כשאין לשרת קובץ סיסמה."""
    if password_file is None:
        return None
    try:
        current = fingerprint(password_file)
    except OSError as exc:
        log.warning("grub password file %s unreadable: %s", password_file, exc)   # #2543
        return "unknown", f"לא נבדק אם הסיסמה נשמרה — {password_file} לא נקרא; הפרטים ביומן השרת"
    if get_setting(conn, SAVED_KEY) == current:
        return "ok", "מנהל סימן שהסיסמה נשמרה מחוץ לשרת"
    return "warn", ("שמרו את סיסמת ה-GRUB מחוץ לשרת — כשהשרת לא עונה, ערך השחזור שלו מבקש "
                    "אותה, והקונסולה לא זמינה כדי להציג אותה. \"הצג סיסמה\", לשמור במקום בטוח, "
                    "ואז \"שמרתי\"")


def create_grub_password_router(ctx: ServerContext, password_file: str | Path | None) -> APIRouter:
    """``password_file`` — מה ש-`server.main` מעביר (``None`` בבדיקות ובתחנת פיתוח)."""
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)

    @router.post("/grub-password")
    def reveal(response: Response, user=Depends(admin_only)):
        if password_file is None:
            raise HTTPException(404, "לשרת הזה אין סיסמת GRUB (תחנת פיתוח או בדיקות)")
        try:
            password = grub_password.read(Path(password_file))
        except (OSError, ValueError) as exc:
            log.warning("grub password %s unreadable: %s", password_file, exc)
            raise HTTPException(503, UNREAD) from None
        journal(ctx.conn, "grub_password_viewed", f"{password_file}", user[0])
        response.headers["Cache-Control"] = "no-store"
        return {"user": GRUB_USER, "password": password}

    @router.post("/grub-password/saved")
    def mark_saved(user=Depends(admin_only)):
        """#2454: מנהל מסמן שהסיסמה הנוכחית נשמרה מחוץ לשרת. רק קובץ שהוא סיסמה
        שאפשר להקליד ב-GRUB — אחרת אין מה לשמור, ואין סימון (עיקרון 5)."""
        if password_file is None:
            raise HTTPException(404, "לשרת הזה אין סיסמת GRUB (תחנת פיתוח או בדיקות)")
        try:
            grub_password.read(Path(password_file))
            current = fingerprint(password_file)
        except (OSError, ValueError) as exc:
            log.warning("grub password %s unreadable: %s", password_file, exc)
            raise HTTPException(503, UNREAD) from None
        set_setting(ctx.conn, SAVED_KEY, current)
        journal(ctx.conn, "grub_password_saved", f"{password_file}", user[0])
        return {"saved": True}

    return router
