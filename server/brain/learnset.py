"""#3655 (אפיק #3653, הכרעת נדב 18/11): הלמידה האוטומטית שכותבת הגדרות (S37). המוח מחליט, `learn/tuning.py` אוסף ראיות ומבצע.

עד כאן `tuning.py` החליט בעצמו אם לשנות הגדרה, לאיזה ערך ועד כמה, וכתב אותה ל-`settings` ול-`learning_changes` -- בלי שהמוח ידע: "כתיבה
שקטה להגדרות". עכשיו כל החלטה כזו היא החלטת מוח: **כלל ``K-LEARN``, שורה ב-`brain_decisions` עם הראיות לפני הכתיבה, והתוצאה (נכתב ל-
`learning_changes#N`, או לא נכתב) אחריה** (`silence.settle`). ב-`tuning.py` נשארו **איסוף הראיות** (מה כל גל מדד ומה מהן ראיה אמיתית) ו**ביצוע
התוצאה** (הכתיבה, היומן, ה-`revert`).

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| S37 `tuning._finish` | ``K-LEARN`` | האם הסבב למד (``REQUIRED_WAVES`` גלים עם ראיה) | `_finish`: סוגר את הסבב |
| S37 `tuning._floor_change` | ``K-LEARN`` | `writecheck_min_mbps` = הנמוך מבין ``FLOOR_OF_MEDIAN`` × החציון, המרווח × האיטי והזרם שהשרת מדד, עד `MAXIMUM`; רק כלפי מעלה, עד ``MAX_STEP`` | `_finish`: כותב ו-`learning_changes` |
| S37 `tuning._start_change` | ``K-LEARN`` | `learned_start_mbps` = הקצב שכל הגלים עמדו בו, מעוגל מטה, בלי אבדן מעל הסף; רק כלפי מעלה, עד ``MAX_STEP`` | `_finish`: כותב ו-`learning_changes` |
| S37 `tuning._measured` | ``K-LEARN`` | מדידה סבירה: לא מעל מה שהקישור מסוגל לו, ולא מעל ``MODEL_OUTLIER`` מחציון הדגם | `_measured`: מסנן |

**הלמידה רק כלפי מעלה** (נדב 18/11: "כמו היום, MAX_STEP 1.5"): ``bounded`` חוסם את הצעד, ו-``target <= current`` אינו שינוי. מדידה **נמוכה** אינה נדחית
(היא יכולה רק למנוע העלאה). **אין החלטה = לא משנים הגדרה** (עיקרון 1 ו-5): כלל שנפל מחזיר ``None``, ושורת ``UNDECIDED`` אחת לכל הגדרה בתהליך; ההגדרה
נשארת כמו שהייתה. **מצב:** ``shadow`` (`brain_veto_mode`) מבצע בדיוק את מה שהקוד שהוסר היה מחליט (הספים לא השתנו), ו-``off`` מחליט ואינו רושם.
"""

from __future__ import annotations

import logging
import statistics
import threading
from dataclasses import dataclass, replace
from typing import Callable, TypeVar

from server.brain import silence
from server.brain.silence import Call

_C = TypeVar("_C", bound=Call)

log = logging.getLogger("imagesrv.brain")

RULE, POINT, ACTION = "K-LEARN", "S37", "LEARN_SETTING"
RUN_ACTION = "LEARN_RUN"
#: ``RAISE``: ההגדרה עולה; ``UNCHANGED``: לא משנים (ואומרים למה); ``RUN_DONE`` / ``RUN_SHORT``: הסבב למד, או שלא הגיעו מספיק גלים עם ראיה.
RAISE, UNCHANGED, RUN_DONE, RUN_SHORT = "RAISE", "UNCHANGED", "RUN_DONE", "RUN_SHORT"

#: כמה שיכפולים לסבב. המקור: נדב 23/10 ("2 הרצות"), אושר במוקאפ 06/11, שאלה 2.
REQUIRED_WAVES = 2
#: הסף מחצי החציון -- אותו יחס שהסוכן כבר פוסל בו באדום (#1366, `writecheck.sh`).
FLOOR_OF_MEDIAN = 0.5
#: כמה הגדרה יכולה לעלות בסבב אחד. ⚠️ ניחוש מנומק, לא מדידה: הגנה מפני דיווח שקרי (סקירת אבטחה 06/11). 50→75 בסבב, ו-600→900 הוא גג הקו כולו.
MAX_STEP = 1.5
#: פי כמה מחציון הדגם מדידה נדחית. ⚠️ ניחוש מנומק: מרווח רחב, כי באותו דגם יש קושחות ומצבי מטמון שונים, והמטרה היא מספר שקרי ולא דיסק מהיר.
MODEL_OUTLIER = 3


@dataclass(frozen=True)
class LearnCall(Call):
    """``value``: הערך החדש (``RAISE`` בלבד): MB/s לסף הכתיבה, ביטים לשנייה לקצב הפתיחה. ``row``: מספר שורת ההחלטה ב-`brain_decisions`, לסגירת התוצאה."""
    value: int | None = None
    row: int | None = None


def bounded(current: int, target: int) -> int:
    """טהור. הצעד של סבב אחד: לא מעל ``MAX_STEP`` מהערך הנוכחי."""
    return min(target, int(current * MAX_STEP))


def measurement_sane(mbps: float, limit: float, base: float | None) -> bool:
    """טהור. מדידת כתיבה סבירה: לא מעל ``limit`` (מה שהקישור שלה מסוגל לו) ולא מעל ``MODEL_OUTLIER`` מחציון הדגם מחוץ לסבב (``base``, או ``None``
    כשאין). מדידה **נמוכה** אינה נדחית: היא יכולה רק למנוע העלאה, ודיסק גוסס אמיתי הוא בדיוק מי שהסף חייב להתחשב בו."""
    return mbps <= limit and (base is None or mbps <= base * MODEL_OUTLIER)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def judge_run(good: int, required: int, *, round_id: str = "") -> LearnCall:
    """טהור. הסבב למד כשהגיעו ``required`` גלים עם ראיה; אחרת לא משנים כלום ("לא נלמד: 1 מתוך 2", עיקרון 5: בלי ראיה אין שינוי)."""
    base = (RULE, POINT, RUN_ACTION)
    if good >= required:
        return LearnCall(*base, RUN_DONE, round_id, f"{good} מתוך {required} גלים עם ראיה: הסבב למד", (f"waves={good}/{required}",))
    return LearnCall(*base, RUN_SHORT, round_id, f"לא נלמד: {good} מתוך {required}. לא משנים כלום", (f"waves={good}/{required}",))


def judge_floor(*, key: str, current: int, speeds: list, serials: int, streams: list, rejected: int, maximum: int, margin: float,
                min_samples: int, min_serials: int, round_id: str = "") -> LearnCall:
    """טהור. סף בדיקת הכתיבה הנלמד. ``speeds``: מדידות הכתיבה שנספרו (MB/s), ``serials``: כמה דיסקים שונים, ``streams``: הזרם שהשרת מדד בכל גל
    (bps; ``None`` = לא נמדד), ``rejected``: כמה מדידות נדחו. הנמוך מבין ``FLOOR_OF_MEDIAN`` × החציון, ``margin`` × האיטי והזרם, עד ``maximum``,
    ורק כלפי מעלה."""
    base = (RULE, POINT, ACTION)
    evidence = [f"key={key}", f"current={current}MB/s", f"measurements={len(speeds)}", f"disks={serials}"]
    if len(speeds) < min_samples or serials < min_serials:
        return LearnCall(*base, UNCHANGED, key, f"אין מספיק מדידות: {len(speeds)} מ-{serials} דיסקים", tuple(evidence))
    if not streams or not all(_number(v) for v in streams):
        return LearnCall(*base, UNCHANGED, key, "קצב הזרם לא נמדד בכל השיכפולים", tuple(evidence))
    median, slowest, stream = statistics.median(speeds), min(speeds), int(min(streams) / 10 ** 6)
    why = (f"חציון הדיסקים בחדר: {int(median)} MB/s, האיטי: {slowest} MB/s ({serials} דיסקים),"
           f" הזרם בשרת: {stream} MB/s" + (f", {rejected} מדידות נדחו" if rejected else ""))
    evidence += [f"median={median:g}", f"slowest={slowest}", f"stream={stream}MB/s", f"floor_of_median={FLOOR_OF_MEDIAN:g}",
                 f"margin={margin:g}", f"max_step={MAX_STEP:g}", f"maximum={maximum}"]
    target = bounded(current, min(int(median * FLOOR_OF_MEDIAN), int(slowest * margin), stream, maximum))
    if target <= current:
        return LearnCall(*base, UNCHANGED, key, why, tuple(evidence))
    return LearnCall(*base, RAISE, key, why, tuple(evidence), value=target)


def judge_start(*, key: str, current: int, proven: list, waves: int, worst_loss: float | None, loss_limit: float, step: int,
                round_id: str = "") -> LearnCall:
    """טהור. קצב הפתיחה הנלמד (bps). ``proven``: הקצב שכל גל באמת עמד בו (``None`` = לא נמדד), ``worst_loss``: האבדן הגרוע מכולם (``None`` כשאין גלים או
    שאחד לא נמדד), ``loss_limit``: האבדן שמעליו לא לומדים, ``step``: העיגול כלפי מטה. הנמוך מבין הגלים, בלי אבדן, ורק כלפי מעלה."""
    base = (RULE, POINT, ACTION)
    evidence = [f"key={key}", f"current={current // 10 ** 6}Mb/s", f"waves={waves}"]
    if not waves or None in proven:
        return LearnCall(*base, UNCHANGED, key, "קצב הזרם לא נמדד בכל השיכפולים", tuple(evidence))
    if worst_loss is None or worst_loss > loss_limit:
        why = "אבדן לא נמדד" if worst_loss is None else f"אבדן {worst_loss:.2%} בשיכפול אחד לפחות"
        return LearnCall(*base, UNCHANGED, key, why, (*evidence, f"loss_limit={loss_limit:g}"))
    target = bounded(current, min(proven)) // step * step
    why = f"הזרם עמד ב-{target // 10 ** 6} Mb/s ב-{waves} שיכפולים, אבדן עד {worst_loss:.2%}"
    evidence += [f"proven_min={min(proven) // 10 ** 6}Mb/s", f"worst_loss={worst_loss:.4%}", f"loss_limit={loss_limit:g}", f"max_step={MAX_STEP:g}"]
    if target <= current:
        return LearnCall(*base, UNCHANGED, key, why, tuple(evidence))
    return LearnCall(*base, RAISE, key, why, tuple(evidence), value=target)


_failed: set[str] = set()
_failed_lock = threading.Lock()
FAILED_KEYS = 64


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי כשלים רשומים."""
    with _failed_lock:
        _failed.clear()


def _decide(conn, key: str, round_id: str, make: Callable[[], _C]) -> _C | None:
    """מחליט (``make`` טהורה), רושם לפני הביצוע, ולעולם אינו זורק. ``None`` = המוח נכשל: אין החלטה, ולא משנים דבר, ושורת ``UNDECIDED`` אחת לכל מפתח."""
    try:
        call = make()
    except Exception as exc:                    # noqa: BLE001 -- לא ידוע אינו "אפשר להעלות"
        log.exception("brain: %s of %s failed", RULE, key)
        with _failed_lock:
            fresh = key not in _failed
            if len(_failed) >= FAILED_KEYS:
                _failed.clear()
            _failed.add(key)
        if conn is not None and fresh:
            silence.record(conn, [Call(RULE, POINT, ACTION, silence.UNDECIDED, key,
                                       f"המוח לא הצליח להחליט ({type(exc).__name__}: {exc}): לא משנים את ההגדרה")],
                           round_id=round_id, entity_kind="settings")
        return None
    if conn is None:
        return call
    ids = silence.record(conn, [call], round_id=round_id, entity_kind="settings")
    return replace(call, row=ids.get(call.target)) if isinstance(call, LearnCall) else call


def decide_run(conn, good: int, required: int, *, run_id: object, round_id: str) -> Call | None:
    """S37: האם הסבב למד. נרשם כשורה על הסבב (`learning_run#N`)."""
    key = f"learning_run#{run_id}"
    return _decide(conn, key, round_id, lambda: judge_run(good, required, round_id=key))


def decide_floor(conn, *, round_id: str, **facts) -> LearnCall | None:
    """S37: סף בדיקת הכתיבה (``facts`` של `judge_floor`, כולל ``key``). נרשם לפני הכתיבה; ``None`` = לא משנים."""
    return _decide(conn, facts.get("key", ""), round_id, lambda: judge_floor(round_id=round_id, **facts))


def decide_start(conn, *, round_id: str, **facts) -> LearnCall | None:
    """S37: קצב הפתיחה הנלמד (``facts`` של `judge_start`, כולל ``key``). נרשם לפני הכתיבה; ``None`` = לא משנים."""
    return _decide(conn, facts.get("key", ""), round_id, lambda: judge_start(round_id=round_id, **facts))


def settled(conn, call: Call | None, outcome: str) -> None:
    """מה שהשרת עשה עם ההחלטה: נכתב, או לא. ``None`` (לא הוחלט) או שורה שלא נרשמה = אין מה לסגור. לעולם אינו זורק."""
    if conn is not None and isinstance(call, LearnCall):
        silence.settle(conn, call.row, outcome)
