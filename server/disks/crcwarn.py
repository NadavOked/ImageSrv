"""סף CRC מוחלט — כבל או מגירה חשודים (‏#1366, הרחבה של נדב 13/10).

‏#432 מזהיר על **עלייה** במונה ה-CRC (מאפיין 199) מאז הסבב הקודם של הדיסק.
ב-13/10 על ברזל נכנסו לגל ראשון דיסקים עם 667 ו-1,752 שגיאות CRC, ושום דבר
לא סומן — לדיסק בגל ראשון אין סבב קודם, ולכן אין "עלייה". הדיסק עם 667
נכשל ב-`fsync errno 5` (השרת סיווג cause=cable).

הכלל: מונה CRC **מוחלט** מעל `crc_warn_threshold` — **כתום**: "כבל או מגירה
חשודים — N שגיאות". אזהרה, לא חסימה (כמו #432; שיכפול תמיד עם אדם ליד
המכונות). ‏0 = האזהרה כבויה.

**ברירת המחדל 500** (נדב 21/10, #1921 — כמו ב-USB; מחליפה את ה-0 שנקבע ב-13/10).
המשמעות עכשיו: **הערת מגירה/כבל** בטבלת "בעיות החומרה", ולא צבע דיסק — הצבע הוא
של טבלת ה-SMART בלבד. ההכרעה הישנה (13/10, מדידה על הברזל): מוני ה-CRC לא עלו
במהלך הגל — 667, ‏1,752 ו-137 לפני ואחרי — כלומר מספרים היסטוריים, והדיסק שנכשל ב-fsync
errno 5 לא נכשל בגלל CRC; לכן העלייה מאז הסבב הקודם (#432) נשארת האזהרה העיקרית, והסף
המוחלט הוא הערה (‏⚠️ ‏55,242 — מחשב 2 דיסק 2, 15/09 — שכפל 20GB מושלם). ‏0 עדיין מכבה.

הערך הוא הגדרה בקונסולה (admin), באותו דפוס של `min_free_gb` (#1354):
‏`settings.crc_warn_threshold`, ונשלח לסוכן בכל hello (המסך הממתין של מחשב
השיכפול צובע ממנו את המגירה לפני שיש סבב).
"""

from __future__ import annotations

import logging
import sqlite3

from server.db import get_setting

log = logging.getLogger("imagesrv.crcwarn")

SETTING = "crc_warn_threshold"
DEFAULT = 500   # Nadav 21/10 (#1921): as on USB; a drawer/cable note, not a disk colour
MAXIMUM = 1_000_000


def parse(raw: object) -> int | None:
    """ערך תקין — מספר שלם 0–1,000,000 — או `None`. ‏`bool` אינו מספר, ומחרוזת
    עם אפס מוביל נדחית (‏ash קורא ‎0500 כאוקטלי)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() \
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 7:
        value = int(raw)
    else:
        return None
    return value if 0 <= value <= MAXIMUM else None


def threshold(conn: sqlite3.Connection) -> int:
    """ההגדרה, או ברירת המחדל (500). פגומה = ברירת המחדל **עם שורת
    יומן** — הערך הפגום אינו נבלע בשקט."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return DEFAULT
    value = parse(raw)
    if value is None:
        log.warning("setting %s=%r is not 0-%d -- using %d", SETTING, raw, MAXIMUM, DEFAULT)
        return DEFAULT
    return value


def suspect(crc: int | None, limit: int) -> bool | None:
    """‏`True` = נקרא ומעל הסף; ‏`False` = נקרא ולא מעל (או שהאזהרה כבויה);
    ‏`None` = המונה לא נקרא — "לא ידוע", לא "תקין" (עיקרון 5)."""
    if crc is None:
        return None
    return limit > 0 and crc > limit


def text_he(crc: int) -> str:
    return f"כבל או מגירה חשודים — {crc} שגיאות"
