"""#2338 (נדב 03/11): סף המהירות של בדיקת הכתיבה לפני הזרם.

**נדב 14/11 בערב: "ספים טכניים לא בהגדרות" -- הסף יצא ממסך ההגדרות** (ה-API והשורה בקונסולה הוסרו). המפתח
`settings.writecheck_min_mbps` נשאר רק כאחסון של הלמידה (LR5, `learn/tuning.py`), ו-`mbps` קורא אותו; ערך ההתחלה הוא `DEFAULT`.

עד כאן `WRITECHECK_MIN_MBPS` היה 50 MB/s קבוע בסוכן (#1366,
`agent/lib/restore/writecheck.sh`): דיסק שכתב לאט ממנו בבדיקה שלפני הזרם
נפסל באדום, כי דיסק איטי מעכב את כל הגל. בברזל 03/11 (משכפל 2,
room_2c946f3b) כונן TOSHIBA DT01ACA0 נמדד 33 MB/s ונפסל לפי הסף. עכשיו
הסף הוא `settings.writecheck_min_mbps` (מנהל בלבד, שלם 10–500), והשרת
מוסיף אותו למניפסט שהסוכן מושך (`writecheck_min_mbps`), כמו `readback_mode`.

הסוכן אוכף. ערך חסר או פגום במניפסט (שרת ישן, מניפסט שלא נקרא)
= 50 ושורת יומן בסוכן — לעולם לא 0 ולא "בלי סף". הבדיקה היחסית (#2037,
חצי מהחציון של אותו סוג) אינה תלויה בהגדרה.

**#3655 (S34, הכרעת נדב 18/11): הרצפה היא החלטה של המוח** (`brain/floorrule.py`, K-FLOOR): ברירת המחדל, הטווח 10-500 ומה ש"פגום"
הם שם. כאן נשארו רק שני דברים: שם ההגדרה, וקריאתה -- `mbps` אוסף את הערך, שואל את המוח וכותב שורת יומן כשההגדרה פגומה.
"""

from __future__ import annotations

import logging
import sqlite3

from server.brain import floorrule
from server.db import get_setting

log = logging.getLogger("imagesrv.writefloor")

SETTING = "writecheck_min_mbps"


def mbps(conn: sqlite3.Connection) -> int:
    """ההגדרה, או ברירת המחדל (`floorrule.DEFAULT`). פגומה = ברירת המחדל **עם שורת יומן** — לעולם לא 0, שהוא "בלי סף". ההחלטה במוח
    (`floorrule.setting_floor`); כאן נקראת ההגדרה ונכתבת שורת היומן."""
    raw = get_setting(conn, SETTING)
    value, damaged = floorrule.setting_floor(raw)
    if damaged:
        log.warning("setting %s=%r is not %d-%d -- using %d", SETTING, raw, floorrule.MINIMUM, floorrule.MAXIMUM, value)
    return value
