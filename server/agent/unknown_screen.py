"""מסך "המחשב הזה לא רשום" ברשת ההפצה — נשאר על המסך (‏#1802).

הכרעת נדב (19/10): מחשב שאינו רשום, **בווילן ההפצה בלבד**, מגיע לסוכן
ורואה את המסך. מחוץ לווילן — כמו היום (עיקרון 1, ‏#53, ‏#141).

**הכרעת נדב (04/11):** המחשב עולה לגואי, רואה את המסך עם MAC ו-IP, **ונשאר
עליו**. ‏F2 = הפעלה מחדש בלבד: הקושחה עולה שוב מהרשת, והמחשב חוזר לאותו
מסך — זו הכוונה. אנחנו לא שולטים בסדר האתחול ולא כותבים לקושחה. לכן,
כשההגדרה כבויה (ברירת המחדל), השרת מגיש את המסך **בכל אתחול**.

**ההגדרה** `unregistered_autoboot_seconds` (‏0 = כבוי) — **‏UEFI בלבד.** ב-BIOS
לעולם אין עלייה אוטומטית מהדיסק: ברשת ההפצה מכונת BIOS היא משכפל, והדיסק
שלה הוא מגירת סחורה (‏#323, ‏#345). כשההגדרה דלוקה, הסוכן (ב-UEFI בלבד) סופר
N שניות ומאתחל, והשרת זוכר שהגיש את המסך: בקשת תפריט של אותו MAC בתוך
`window_seconds` (‏N + ‏`WINDOW_SECONDS`) מקבלת את `AUTOBOOT_FLAG`. מחולל
התפריט הופך אותו לקובץ שבו **רק `$grub_platform = efi` בוחר בדיסק**; ‏BIOS
באותו קובץ מקבל שוב את הסוכן, כלומר שוב את המסך. השרת אינו יודע מה הפלטפורמה —
‏GRUB יודע, ולכן ההבחנה בקובץ.

**הזיכרון הוא שורת `boot_attempts`** (‏#75) עם ההקשר `CONTEXT` — "השרת שלח את
ה-MAC הזה למסך, ומתי". אין טבלה חדשה. ‏`CONTEXT` אינו `session:`/`task:`,
ולכן `bootguard` ו-`repeats` אינם רואים אותו.

**ספק = בלי דגל** (עיקרון 5): כתיבה שלא נקראה בחזרה אינה "הוגש", והתשובה
נשארת זו של היום (‏UEFI לדיסק, ‏BIOS לעצירה של #345).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from server.db import _write_lock, get_setting, now_iso, writing

log = logging.getLogger("imagesrv.unknown_screen")

#: ההקשר בשורת `boot_attempts`: השרת הגיש את המסך ל-MAC הזה.
CONTEXT = "unregistered-screen"

#: הדגלים בתשובת השרת (ממשק 3) — **רק במסלול התפריט**. ‏`grub_menu.decide`.
FLAG = "unregistered_screen"
#: ההגדרה דלוקה והמסך הוגש בחלון: ‏UEFI — הדיסק; ‏BIOS — שוב המסך.
AUTOBOOT_FLAG = "unregistered_autoboot"

#: המרווח שמעל N: הנתיב מסוף הספירה עד בקשת התפריט הבאה — אתחול, ‏POST,
#: ‏PXE — דקות בודדות. עשר דקות נותנות מרווח למחשב איטי.
WINDOW_SECONDS = 600

#: ההגדרה בקונסולה: אחרי כמה שניות מכונת UEFI לא רשומה עולה מהדיסק. 0 = כבוי.
#: ‏**UEFI בלבד** — ב-BIOS לעולם לא (‏#345, נדב 04/11).
SETTING = "unregistered_autoboot_seconds"
DEFAULT = 0
MAXIMUM = 600


def parse(raw: object) -> int | None:
    """שלם 0–600, או `None`. ‏`bool` אינו מספר; אפס מוביל נדחה (‏ash קורא
    ‎010 כאוקטלי) — אותו כלל של `writefloor.parse`."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif (isinstance(raw, str) and raw.isascii() and raw.isdigit()
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 3):
        value = int(raw)
    else:
        return None
    return value if 0 <= value <= MAXIMUM else None


def autoboot_seconds(conn: sqlite3.Connection) -> int:
    """ההגדרה, או 0 (כבוי). פגומה = כבוי **עם שורת יומן** — ספק אינו מאתחל."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return DEFAULT
    value = parse(raw)
    if value is None:
        log.warning("setting %s=%r is not 0-%d -- off", SETTING, raw, MAXIMUM)
        return DEFAULT
    return value


def window_seconds(conn: sqlite3.Connection) -> int:
    """החלון שבו הגשת המסך מסבירה את האתחול הבא: N שניות על המסך ועוד המרווח."""
    return autoboot_seconds(conn) + WINDOW_SECONDS


def _cutoff(now: str, seconds: int) -> str:
    """‏ISO ברוחב קבוע ב-UTC (`now_iso`) — סדר לקסיקוגרפי הוא כרונולוגי."""
    return (datetime.fromisoformat(now)
            - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def served_recently(conn: sqlite3.Connection, mac: str, now: str | None = None) -> bool:
    """האם השרת עצמו שלח את ה-MAC הזה למסך בחלון — ולכן ההגעה שלו לסוכן
    מוסברת (‏`agent_loops`). שגיאת קריאה = לא (ניטור בלבד)."""
    try:
        row = conn.execute("SELECT context, last_at FROM boot_attempts WHERE mac = ?",
                           (mac,)).fetchone()
        cutoff = _cutoff(now or now_iso(), window_seconds(conn))
    except sqlite3.Error:
        log.exception("could not read the unregistered-screen mark of %s", mac)
        return False
    return row is not None and row["context"] == CONTEXT and row["last_at"] >= cutoff


def offer(conn: sqlite3.Connection, mac: str, answer: dict,
          now: str | None = None) -> dict:
    """תשובת `known: false` לבקשת תפריט בווילן ההפצה → עם אחד הדגלים, או כמו שהיא.

    ההגדרה כבויה → המסך (`FLAG`) **בכל אתחול**, והסימון מתחדש.
    ההגדרה דלוקה → המסך פעם אחת בחלון; בקשה בתוך החלון → `AUTOBOOT_FLAG`.
    ה-UPSERT מתעדכן אז רק כשאין סימון בחלון ("הוגש לפני רגע" אינו נדרס), ומי
    שכתב יודע זאת משני דברים יחד: ‏`rowcount` 1 והחותמת שנקראה בחזרה.
    """
    ts = now or now_iso()
    try:
        auto = autoboot_seconds(conn)
        cutoff = _cutoff(ts, auto + WINDOW_SECONDS) if auto else "9999"   # כבוי: אין חלון
        with _write_lock, writing(conn):
            changed = conn.execute(
                "INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                " VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT (mac) DO UPDATE SET"
                "   context = excluded.context, attempts = 1,"
                "   first_at = excluded.first_at, last_at = excluded.last_at"
                " WHERE NOT (boot_attempts.context = excluded.context"
                "            AND boot_attempts.last_at >= ?)",
                (mac, CONTEXT, ts, ts, cutoff),
            ).rowcount
        row = conn.execute("SELECT context, last_at FROM boot_attempts WHERE mac = ?",
                           (mac,)).fetchone()
    except sqlite3.Error:
        log.exception("unregistered-screen mark for %s was not written -- as today", mac)
        return answer
    if row is None or row["context"] != CONTEXT:
        log.error("unregistered-screen mark for %s did not read back -- as today", mac)
        return answer
    if changed != 1 or row["last_at"] != ts:
        log.info("unregistered %s: the screen was served at %s -- UEFI boots the local disk"
                 " now, BIOS gets the screen again (setting %ss)", mac, row["last_at"], auto)
        return {**answer, AUTOBOOT_FLAG: True}
    log.info("unregistered %s on the deploy vlan: the screen (#1802)", mac)
    return {**answer, FLAG: True}


__all__ = ["AUTOBOOT_FLAG", "CONTEXT", "DEFAULT", "FLAG", "MAXIMUM", "SETTING",
           "WINDOW_SECONDS", "autoboot_seconds", "offer", "parse", "served_recently",
           "window_seconds"]
