"""הגבלת קצב להעברות בין אתרים (‏#1473, הכרעת נדב 16/10).

העברת אימג' לסניף (push) או ממנו (pull) רצה בכל הקצב שהקו נותן — על קו
בין-אתרי משותף זה חונק את שאר התעבורה. **אופציונלי**, בשליטת המפעיל
(מסך הסניפים), **ברירת מחדל: ללא הגבלה**:

    ‏`settings.intersite_rate_cap_mbps` — מספר שלם 0–100,000, ב-Mb/s (מגה-ביט,
    השפה שבה מתארים קו); ‏0 או חסר = ללא הגבלה

**לכל העברה בנפרד** (לא סכום על כל ההעברות): שתי העברות לשני סניפים
בו-זמנית — כל אחת עד התקרה.

איפה נאכף — תמיד אצל הראשי, בתהליכון של ההעברה (`storage_transfer`):

* **push** — הראשי שולח: אחרי כל מנה של ה-tar ממתינים עד שהקצב הממוצע
  יורד לתקרה. זה הצד השולח.
* **pull** — המשני שולח, והראשי קורא בקצב התקרה: חלון ה-TCP מתמלא והמשני
  נחסם ב-`sendall` (חוסם בלי תקרת זמן אחרי ה-handshake, ‏
  ``interserver_api``), כך שגם **השולח** אינו יוצא מעל התקרה. בלי שינוי
  בפרוטוקול — עובד מול משני בכל גרסה, ובלי לגעת ב-`storage_client`.

ההגדרה נקראת מחדש כל `RECHECK_S` שניות: שינוי בקונסולה תופס גם העברה
שכבר רצה. אין "אשראי" — העברה שהייתה איטית מהתקרה (דיסק, קו) אינה
מתפרצת מעליה כדי להשלים.

עיקרון 5: ערך שנכתב נבדק לפני הכתיבה (‏400, ``console_api``). ערך פגום
שכבר ב-DB (עריכה ידנית) — ברירת המחדל **עם שורת לוג**, ומסך הסניפים מציג
את הערך שבתוקף (`cap_mbps`), לא את מה שנכתב.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Callable

from server.db import get_setting

log = logging.getLogger("imagesrv.transfer_cap")

SETTING = "intersite_rate_cap_mbps"
DEFAULT = 0          # ללא הגבלה — הכרעת נדב 16/10
MAXIMUM = 100_000    # ‏100Gb/s; מעל זה אין קו, יש טעות הקלדה
#: כל כמה שניות ההעברה קוראת שוב את ההגדרה.
RECHECK_S = 2.0
#: בייטים לשנייה לכל Mb/s.
BYTES_PER_MBPS = 125_000


def parse(raw: object) -> int | None:
    """מספר שלם 0–100,000 — או `None`. ‏`bool` אינו מספר; מחרוזת עם אפס
    מוביל, סימן או נקודה — נדחית (כמו `crcwarn.parse`)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() \
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 6:
        value = int(raw)
    else:
        return None
    return value if 0 <= value <= MAXIMUM else None


def cap_mbps(conn: sqlite3.Connection) -> int:
    """התקרה שבתוקף ב-Mb/s; ‏0 = ללא הגבלה. פגומה = ברירת המחדל **עם לוג**."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return DEFAULT
    value = parse(raw)
    if value is None:
        log.warning("setting %s=%r is not 0-%d -- using %d (unlimited)",
                    SETTING, raw, MAXIMUM, DEFAULT)
        return DEFAULT
    return value


def bytes_per_second(conn: sqlite3.Connection) -> int | None:
    """התקרה בבייטים לשנייה; ‏None = ללא הגבלה."""
    mbps = cap_mbps(conn)
    return mbps * BYTES_PER_MBPS if mbps > 0 else None


class Pacer:
    """נקרא עם סך הבייטים שעברו בהעברה (מונוטוני), וממתין עד שהקצב הממוצע
    מנקודת העיגון אינו מעל התקרה. העיגון זז כשהתקרה משתנה וכשההעברה מפגרת
    אחרי התקרה — כך אין התפרצות מעליה."""

    def __init__(self, rate: Callable[[], int | None], *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._read, self._clock, self._sleep = rate, clock, sleep
        self._rate: int | None = None
        self._checked: float | None = None
        self._t0, self._b0 = 0.0, 0

    def __call__(self, sent: int) -> None:
        now = self._clock()
        if self._checked is None or now - self._checked >= RECHECK_S:
            self._checked = now
            rate = self._read()
            if rate != self._rate:
                self._rate, self._t0, self._b0 = rate, now, sent
                return
        if self._rate is None:
            return
        due = self._t0 + (sent - self._b0) / self._rate
        if due > now:
            self._sleep(due - now)
        else:
            self._t0, self._b0 = now, sent          # מפגרת — בלי אשראי


def pacer(conn: sqlite3.Connection, **kw) -> Pacer:
    """‏Pacer שקורא את ההגדרה מ-`conn` (חיבור התהליכון של ההעברה)."""
    return Pacer(lambda: bytes_per_second(conn), **kw)
