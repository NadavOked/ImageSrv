"""#3752 (המשך #1947): "עכשיו" שאינו קופץ עם שעון הקיר, לאתרים שמודדים **משך**.

`clockstep` מכייל את חותמות הסבבים אחרי קפיצה. אתרים אחרים (נעילת כניסה, חלון
אישור רשת, VM) מודדים משך בין שתי קריאות של אותו תהליך, וקפיצת שעון (NTP אחרי
עלייה, RTC שגוי, החזרת snapshot) מקצרת או מאריכה אותו בגודל הקפיצה.

`now()` הוא שעון קיר בתחילת התהליך + זמן monotonic שעבר: **קפיצה אינה זזה בו**,
וסחיפה איטית (עד `JUMP_SECONDS` מהניחוש) כן נבלעת — הבסיס מתיישר מחדש לקיר.
הערך נשאר בסקאלה של שניות-epoch, ולכן אפשר לשמור אותו ב-DB כמו כל חותמת; אחרי
אתחול התהליך הבסיס נלקח מחדש משעון הקיר, וחותמת שנשמרה לפני קפיצה שקרתה בין
האתחולים נראית ישנה או צעירה בגודל הקפיצה לכל היותר — את זה חוסמים באתרים
שנוגעים באבטחה בגדר של תקרת-משך (ראו `login_guard`).

לא לשימוש כשהחותמת נקראת בצד שלישי (TOTP, תעודות, התאריך שמוצג למפעיל): שם
שעון הקיר הוא האמת."""

from __future__ import annotations

import threading
import time

#: מעל כמה שניות הפרש מהניחוש הוא קפיצה ולא סחיפה (כמו `clockstep.JUMP_SECONDS`).
JUMP_SECONDS = 60

_lock = threading.Lock()
#: נקבע בעליית התהליך (import), לא בקריאה הראשונה: קפיצה שקרתה לפני שמישהו שאל
#: "מה השעה" אחרת הייתה נבלעת בעוגן.
_anchor: tuple[float, float] = (time.time(), time.monotonic())


def _wall() -> float:
    """פונקציה, כדי שהבדיקה תזייף את הקיר ואת ה-monotonic יחד."""
    return time.time()


def _mono() -> float:
    return time.monotonic()


def mono() -> float:
    """שעון ה-monotonic של התהליך — נקודת הזרקה אחת עם `_wall`."""
    return _mono()


def now() -> float:
    global _anchor
    wall, mono = _wall(), _mono()
    with _lock:
        guess = _anchor[0] + (mono - _anchor[1])
        if abs(wall - guess) <= JUMP_SECONDS:
            _anchor = (wall, mono)
            return wall
        return guess


def skew() -> float:
    """שעון הקיר פחות `now()`: ≈0 כשלא הייתה קפיצה, וגודל הקפיצה (חיובי = קדימה) אחריה."""
    return _wall() - now()
