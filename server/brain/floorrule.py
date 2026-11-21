"""#3655 (אפיק #3653, הכרעת נדב 18/11): רצפת הכתיבה לדיסק והציפייה של HDD (S34). המוח מחליט, `disks/writefloor.py` ו-`disks/hdd_policy.py` מבצעים.

עד כאן הכרעת "מה המהירות המינימלית לדיסק הזה" ישבה בשני קבצים בתוך `disks/`: ברירת המחדל (50, טווח 10-500) ב-`writefloor.py`, והציפייה
של HDD (חצי מהחציון של 10 הגלים האחרונים של אותו serial ואותו דגם, ו-20 כשאין גל) ב-`hdd_policy.py`. עכשיו הערכים וההשוואות
כאן, ושם נשארו **איסוף העובדות** (ההגדרה, המתג `hdd_support`, `rotational`, שורות `drive_rates`) ו**ביצוע התוצאה** (סירוב או רצפה
בתוך התוכנית של ההשלמה, והמספר שהמניפסט נושא לסוכן).

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| S34 `hdd_policy.decision` | ``K-FLOOR`` | לדיסק מוצק או למקור: הרצפה מההגדרה (ברירת מחדל ``DEFAULT``); ל-HDD כיעד: סירוב כשהמתג כבוי, אחרת הציפייה שנלמדה מהגלים שלו, או ``INITIAL_MBPS`` כשאין גל | `RepairFloor.for_disk` (בהשלמה), `manifest` (לסוכן) |

**מה שאינו כאן:** המתג `hdd_support` עצמו הוא החלטת משתמש (קבוצה ב', #3675). הסף שהסוכן אוכף (`writecheck.sh`, A5/A6) לא נגעתי בו: הוא
נשאר ב-Issue #3655 סעיף 4. ``DEFAULT`` כאן הוא התאום של `WRITECHECK_MIN_MBPS` שלו, וטסט משווה.

**מצב.** ``shadow`` (`brain_veto_mode`): כל החלטה **לדיסק** (בהשלמה) נרשמת ב-``brain_decisions`` עם הראיות; ``off`` מחליט ואינו רושם. קריאת
הרצפה הכללית (`writefloor.mbps`, נשאלת בכל hello) אינה החלטה על דיסק ואינה נרשמת: שורה לכל hello הייתה מציפה. **הערכים לא השתנו.**

**אין החלטה = ברירת המחדל הקיימת:** כלל שנפל מחזיר ``None``, והמבצע שם את הרצפה ``DEFAULT`` (מדיניות הדיסק המוצק), ושורת ``UNDECIDED`` אחת
לדיסק בתהליך. לעולם לא סירוב שקט ולעולם לא "בלי סף".
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from statistics import median
from typing import Callable, Iterable

from server.brain import silence
from server.brain.silence import Call

log = logging.getLogger("imagesrv.brain")

RULE, POINT, ACTION = "K-FLOOR", "S34", "WRITE_FLOOR"
#: ``FROM_SETTING``: הרצפה מההגדרה (דיסק מוצק או מקור); ``LEARNED``: ציפיית HDD מהגלים שלו; ``ESTIMATE``: HDD בלי גל, הערכת פתיחה;
#: ``REFUSED``: HDD כיעד כשהמתג כבוי.
FROM_SETTING, LEARNED, ESTIMATE, REFUSED = "FROM_SETTING", "LEARNED", "ESTIMATE", "REFUSED"

#: S34, #2338: הרצפה כשההגדרה חסרה או פגומה, והטווח התקף שלה (MB/s). התאום של `WRITECHECK_MIN_MBPS` ב-`agent/lib/restore/writecheck.sh` --
#: טסט משווה. ההגדרה עצמה (`writecheck_min_mbps`) היא רק האחסון של הלמידה (LR5); ערך התחלה: 50 (נדב 03/11). לא נמדד.
DEFAULT, MINIMUM, MAXIMUM = 50, 10, 500
#: S34, X16: נקודת פתיחה ל-HDD בלי גל שנמדד. **הערכה, לא מדידה.** התאום של `HDD_ESTIMATE_MBPS` ב-`writecheck.sh`.
INITIAL_MBPS = 20
#: חצי מהחציון של הגלים של הדיסק עצמו -- מרווח לשונות בין גלים. הערכה, לא נמדדה. ו-``WINDOW`` הגלים האחרונים שנספרים.
EXPECT_MARGIN = 0.5
WINDOW = 10


@dataclass(frozen=True)
class FloorCall(Call):
    """``mbps``: הרצפה לדיסק הזה, או ``None`` כשהוא נדחה (``REFUSED``)."""
    mbps: int | None = None


def parse(raw: object) -> int | None:
    """ערך תקין של ההגדרה -- מספר שלם 10-500 -- או ``None``. ``bool`` אינו מספר, ומחרוזת עם אפס מוביל נדחית (ash קורא 040 כאוקטלי)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and not raw.startswith("0") and len(raw) <= 3:
        value = int(raw)
    else:
        return None
    return value if MINIMUM <= value <= MAXIMUM else None


def setting_floor(raw: object) -> tuple[int, bool]:
    """טהור. (הרצפה בMB/s, האם ההגדרה פגומה). חסרה = ``DEFAULT``; פגומה = ``DEFAULT`` **והקורא רושם שורת יומן** -- לעולם לא 0, שהוא "בלי סף"."""
    if raw is None:
        return DEFAULT, False
    value = parse(raw)
    return (DEFAULT, True) if value is None else (value, False)


def expect(rows: Iterable[tuple[object, object]]) -> int | None:
    """טהור. הציפייה של HDD, MB/s: חצי (``EXPECT_MARGIN``) מהחציון של ``rows`` -- זוגות (בייטים, שניות), החדשים ראשונים, עד ``WINDOW`` -- או
    ``None`` כשאין גל שנמדד (לא 0). שורה בלי בייטים או בלי שניות אינה גל."""
    speeds = [b / s / 1_000_000 for b, s in rows
              if isinstance(b, (int, float)) and isinstance(s, (int, float)) and b > 0 and s > 0][:WINDOW]
    if not speeds:
        return None
    return max(1, int(median(speeds) * EXPECT_MARGIN))


def judge_floor(raw: object, *, rotational: int, role: str, support: bool, rates: Callable[[], Iterable],
                serial: str = "", where: str = "") -> FloorCall:
    """טהור (``rates`` נקרא רק כשצריך). הרצפה לדיסק אחד: מקור או דיסק שאינו HDD -- ההגדרה (``FROM_SETTING``); HDD כיעד כשהמתג כבוי -- ``REFUSED``;
    HDD כיעד כשהמתג דלוק -- ``LEARNED`` מהגלים שלו, או ``ESTIMATE`` בלי גל. מקור אינו נפסל לעולם."""
    base = (RULE, POINT, ACTION)
    target = serial or where
    floor, damaged = setting_floor(raw)
    evidence = [f"role={role}", f"rotational={rotational}", f"setting={'unset' if raw is None else raw}"]
    if damaged:
        evidence.append(f"setting_damaged -> default {DEFAULT}")
    if role == "source" or not rotational:
        why = "מקור אינו נפסל" if role == "source" else "דיסק מוצק: הרצפה של כל דיסק"
        return FloorCall(*base, FROM_SETTING, target, f"{why}: {floor}MB/s", tuple(evidence), mbps=floor)
    evidence.append(f"hdd_support={'on' if support else 'off'}")
    if not support:
        return FloorCall(*base, REFUSED, target, "HDD כיעד, ותמיכה ב-HDD כבויה בהגדרות: נדחה לפני כתיבה", tuple(evidence), mbps=None)
    learned = expect(rates())
    if learned is None:
        return FloorCall(*base, ESTIMATE, target, f"HDD בלי גל שנמדד: הערכת הפתיחה {INITIAL_MBPS}MB/s (הערכה, לא מדידה)",
                         tuple(evidence), mbps=INITIAL_MBPS)
    return FloorCall(*base, LEARNED, target, f"HDD: חצי מחציון הגלים שלו, {learned}MB/s", (*evidence, f"margin={EXPECT_MARGIN:g}", f"window={WINDOW}"),
                     mbps=learned)


_failed: set[str] = set()
_failed_lock = threading.Lock()
FAILED_KEYS = 256


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי כשלים רשומים."""
    with _failed_lock:
        _failed.clear()


def decide_disk(conn, raw: object, *, rotational: int, role: str, support: bool, rates: Callable[[], Iterable],
                serial: str = "", where: str = "") -> FloorCall | None:
    """S34. נרשם ב-``brain_decisions`` (ישות ``disk``). ``None`` = המוח נכשל: אין החלטה, והמבצע שם את ``DEFAULT``. לעולם אינו זורק."""
    try:
        call = judge_floor(raw, rotational=rotational, role=role, support=support, rates=rates, serial=serial, where=where)
    except Exception as exc:                    # noqa: BLE001 -- לא ידוע אינו "תקין", וגם אינו סירוב
        log.exception("brain: %s of %s failed", RULE, serial or where)
        key = serial or where
        with _failed_lock:
            fresh = key not in _failed
            if len(_failed) >= FAILED_KEYS:
                _failed.clear()                 # הרשם אינו גדל לנצח
            _failed.add(key)
        if conn is not None and fresh:
            silence.record(conn, [Call(RULE, POINT, ACTION, silence.UNDECIDED, key,
                                       f"המוח לא הצליח להחליט ({type(exc).__name__}: {exc}): הרצפה {DEFAULT}MB/s, כמו לדיסק מוצק")],
                           round_id=None, entity_kind="disk")
        return None
    if conn is not None:
        silence.record(conn, [call], round_id=None, entity_kind="disk")
    return call
