"""#2338 (נדב 03/11): סף המהירות של בדיקת הכתיבה לפני הזרם — הגדרה בקונסולה.

עד כאן `WRITECHECK_MIN_MBPS` היה 50 MB/s קבוע בסוכן (#1366,
`agent/lib/restore/writecheck.sh`): דיסק שכתב לאט ממנו בבדיקה שלפני הזרם
נפסל באדום, כי דיסק איטי מעכב את כל הגל. בברזל 03/11 (משכפל 2,
room_2c946f3b) כונן TOSHIBA DT01ACA0 נמדד 33 MB/s ונפסל לפי הסף. עכשיו
הסף הוא `settings.writecheck_min_mbps` (מנהל בלבד, שלם 10–500), והשרת
מוסיף אותו למניפסט שהסוכן מושך (`writecheck_min_mbps`), כמו `readback_mode`.

הסוכן אוכף. ערך חסר או פגום במניפסט (שרת ישן, מניפסט שלא נקרא)
= 50 ושורת יומן בסוכן — לעולם לא 0 ולא "בלי סף". הבדיקה היחסית (#2037,
חצי מהחציון של אותו סוג) אינה תלויה בהגדרה.
"""

from __future__ import annotations

import logging
import sqlite3

from server.db import get_setting

log = logging.getLogger("imagesrv.writefloor")

SETTING = "writecheck_min_mbps"
#: התאום של `WRITECHECK_MIN_MBPS` ב-`agent/lib/restore/writecheck.sh` — טסט משווה.
DEFAULT = 50
MINIMUM = 10
MAXIMUM = 500


def parse(raw: object) -> int | None:
    """ערך תקין — מספר שלם 10–500 — או `None`. `bool` אינו מספר, ומחרוזת
    עם אפס מוביל נדחית (ash קורא 040 כאוקטלי)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() \
            and not raw.startswith("0") and len(raw) <= 3:
        value = int(raw)
    else:
        return None
    return value if MINIMUM <= value <= MAXIMUM else None


def mbps(conn: sqlite3.Connection) -> int:
    """ההגדרה, או ברירת המחדל (50). פגומה = ברירת המחדל **עם שורת יומן**
    — לעולם לא 0, שהוא "בלי סף"."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return DEFAULT
    value = parse(raw)
    if value is None:
        log.warning("setting %s=%r is not %d-%d -- using %d", SETTING, raw,
                    MINIMUM, MAXIMUM, DEFAULT)
        return DEFAULT
    return value
