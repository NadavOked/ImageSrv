"""מסך "המחשב הזה לא רשום" ברשת ההפצה — פעם אחת, ואז הדיסק (‏#1802).

הכרעת נדב (19/10): מחשב שאינו רשום, **בווילן ההפצה בלבד**, מגיע לסוכן,
רואה את המסך עשר שניות ועולה מהדיסק המקומי. מחוץ לווילן — כמו היום
(עיקרון 1, ‏#53, ‏#141).

**הלולאה, והיכן היא נעצרת.** הסוכן עולה מהדיסק דרך `reboot -f` — כלומר
הוא רק *מקווה* שהאתחול הבא ילך לדיסק (בדיוק הלקח של #75). הראיה היחידה
היא של מי שמגיש את התפריט: השרת. לכן הזיכרון כאן: השרת רושם שהגיש את
המסך ל-MAC הזה, ובחלון של `WINDOW_SECONDS` אחר כך אותו MAC מקבל את
התשובה של היום (‏`known: false` בלי הדגל — `timeout=0` לדיסק).

**הזיכרון הוא שורת `boot_attempts`** (‏#75) עם ההקשר `CONTEXT` — "השרת שלח
את ה-MAC הזה לסוכן, לעבודה הזו, ומתי". אין טבלה חדשה: לטבלה כבר יש MAC
כמפתח, ‏`last_at`, ניקוי ב-`housekeeping`, ואותם מנגנוני כתיבה (#272).
מכונה לא רשומה אינה בסבב ואינה במשימה, ולכן אין לה שורה בהקשר אחר.

**ספק = דיסק** (עיקרון 5): כתיבה שלא נקראה בחזרה אינה "הוגש", והתשובה
נשארת זו של היום. המחיר — מכונה שלא תראה את המסך — הוא הכיוון הבטוח.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from .db import _write_lock, now_iso, writing

log = logging.getLogger("imagectl.unknown_screen")

#: ההקשר בשורת `boot_attempts`. אינו `session:`/`task:`, ולכן `bootguard`
#: ו-`repeats` (התצוגה בקונסולה) אינם רואים אותו.
CONTEXT = "unregistered-screen"

#: הדגל בתשובת השרת (ממשק 3) — **רק במסלול התפריט**. ‏`grub_menu.decide`.
FLAG = "unregistered_screen"

#: כמה זמן אחרי שהמסך הוגש אותו MAC עולה מהדיסק בלי מסך. הנתיב שהחלון
#: צריך לכסות: הורדת הקרנל וה-initramfs הגרפי, ‏hello, ‏10 שניות, אתחול,
#: ‏POST ו-PXE — דקות בודדות. עשר דקות נותנות מרווח למחשב איטי; מי
#: שמאתחל בתוכן רואה את מה שראה עד היום (דיסק בלי מסך).
WINDOW_SECONDS = 600


def _cutoff(now: str) -> str:
    """‏ISO ברוחב קבוע ב-UTC (`now_iso`) — סדר לקסיקוגרפי הוא כרונולוגי."""
    return (datetime.fromisoformat(now)
            - timedelta(seconds=WINDOW_SECONDS)).isoformat(timespec="seconds")


def served_recently(conn: sqlite3.Connection, mac: str, now: str | None = None) -> bool:
    """האם המסך הוגש ל-MAC הזה בחלון. שגיאת קריאה = לא (ניטור בלבד)."""
    try:
        row = conn.execute("SELECT context, last_at FROM boot_attempts WHERE mac = ?",
                           (mac,)).fetchone()
    except sqlite3.Error:
        log.exception("could not read the unregistered-screen mark of %s", mac)
        return False
    return (row is not None and row["context"] == CONTEXT
            and row["last_at"] >= _cutoff(now or now_iso()))


def offer(conn: sqlite3.Connection, mac: str, answer: dict,
          now: str | None = None) -> dict:
    """תשובת `known: false` לבקשת תפריט בווילן ההפצה → עם הדגל, או כמו שהיא.

    ה-UPSERT מתעדכן רק כשאין סימון בחלון — "הוגש לפני רגע" אינו נדרס,
    וכך החלון נמדד מההגשה ולא מהבקשה האחרונה. מי שכתב יודע זאת משני
    דברים יחד: ‏`rowcount` 1 (‏`DO UPDATE ... WHERE` שלא התקיים משנה 0
    שורות — גם כשהבקשה השנייה באה באותה שנייה ממש) והחותמת שנקראה בחזרה.
    """
    ts = now or now_iso()
    try:
        with _write_lock, writing(conn):
            changed = conn.execute(
                "INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                " VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT (mac) DO UPDATE SET"
                "   context = excluded.context, attempts = 1,"
                "   first_at = excluded.first_at, last_at = excluded.last_at"
                " WHERE NOT (boot_attempts.context = excluded.context"
                "            AND boot_attempts.last_at >= ?)",
                (mac, CONTEXT, ts, ts, _cutoff(ts)),
            ).rowcount
        row = conn.execute("SELECT context, last_at FROM boot_attempts WHERE mac = ?",
                           (mac,)).fetchone()
    except sqlite3.Error:
        log.exception("unregistered-screen mark for %s was not written -- local disk", mac)
        return answer
    if row is None or row["context"] != CONTEXT:
        log.error("unregistered-screen mark for %s did not read back -- local disk", mac)
        return answer
    if changed != 1 or row["last_at"] != ts:
        log.info("unregistered %s: the screen was served at %s -- local disk now",
                 mac, row["last_at"])
        return answer
    log.info("unregistered %s on the deploy vlan: the screen, once (#1802)", mac)
    return {**answer, FLAG: True}


__all__ = ["CONTEXT", "FLAG", "WINDOW_SECONDS", "offer", "served_recently"]
