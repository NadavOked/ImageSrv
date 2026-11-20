"""#3655 (אפיק #3653, הכרעת נדב 18/11): הקצב שהדיסקים נדרשים לכתוב (S3) וגבולות הבקר של השולח (S4). המוח מחליט, והמנוע מבצע.

עד כאן שני החישובים ישבו ב-`chunksend.py` (`_write_base`, `feedback_bounds`, `feedback_grow_cap`), ולצידם שלישי ב-`chunk_rate.py`
(K8) שבנה את הקצב מעל מה שהם חישבו: "מקום אחד מחליט" (נדב: "לא להשאיר חישוב קצב מקביל 'כגיבוי'"). עכשיו המרווח, מי נספר ומה
הגבולות כאן, ובמנוע נשארו **איסוף העובדות** (מה כל דיסק מדד בבדיקת הכתיבה, מה ההיסטוריה שלו) ו**ביצוע התוצאה** (הדגלים של
`chunkcast send`). אין בשרת עותק של המרווח ושל מכפיל הגג.

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| S3 `ChunkEngine._write_base` | ``K-WRITE`` | `--write-kbps`: האיטי שנמדד × ``MARGIN``, לא פחות מהרצפה; דיסק שפיגר בגל קודם לא נספר (כל עוד יש דיסק בלי היסטוריה) | `_write_base`: מעביר ל-K8 (`chunk_rate.write_rate`) ולשולח |
| S4 `feedback_bounds`, `feedback_grow_cap` | ``K-RATE-BOUNDS`` | `--rate-min` / `--rate-max` / `--rate-cap` של הבקר בתוך הגל | `_command`: כותב את הדגלים |

**מצב.** כמו שאר כללי "המוח מחליט, השרת מבצע" (`silence.py`): ``shadow`` (ברירת המחדל) ו-``enforce`` רושמים כל החלטה ב-
``brain_decisions`` עם הראיות; ``off`` מחליט ואינו רושם. **הספים לא השתנו** (0.9, פי 2, אותן השוואות), ולכן ההתנהגות זהה לזו של
לפני #3655. K8 (`chunk_rate.write_rate`) ממשיך להחליט על ה-`--write-kbps` הסופי, מעל הבסיס שכאן: זו השרשרת "חוש -> כלל -> `--write-kbps`
-> ביצוע" בלי חישוב בצד.

**אין החלטה = הרצפה** (עיקרון 1 ו-5): כלל שנפל מחזיר ``None``, והמנוע שם את הרצפה (המהירות המינימלית לכונן, הערך שהגל כבר מחויב
אליו) ואינו מחשב דבר בעצמו. הכשל נרשם בלוג, ושורת ``brain_decisions`` אחת לכל (כלל, גל) בתהליך.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from server.brain import silence
from server.brain.silence import Call

log = logging.getLogger("imagesrv.brain")

RULE_BASE, RULE_BOUNDS = "K-WRITE", "K-RATE-BOUNDS"
POINT_BASE, POINT_BOUNDS = "S3", "S4"
ACTION_BASE, ACTION_BOUNDS = "WRITE_BASE", "FEEDBACK_BOUNDS"
#: ``PACE``: הדיסקים שנספרו קבעו את הקצב; ``FLOOR``: אין מדידה או מדידה אחת חסרה, ולכן הרצפה; ``SET``: הגבולות נקבעו.
PACE, FLOOR, SET = "PACE", "FLOOR", "SET"

#: S3, T4: המרווח מתחת למה שדיסק נמדד. נגזר מ-`drive_rates.MARGIN` (אותם 10% שמקבל הסף של udpcast); הערכה, לא מדידה. K8
#: (`chunk_rate`) מחיל אותו על מה שדיסק כתב בגל שפיגר בו, ולכן הערך כאן ולא עותק שני.
MARGIN = 0.9
#: S4, #3512: פי כמה מהבסיס הבקר רשאי להעלות את קצב הכתיבה בתוך הגל. הבקר עצמו הוא השומר (מאט כשהתור של מקלט גדל), ו-`--kbps`
#: נשאר תקרת החוט. #3520 (ברזל 16/11): המקסימום ש-K8 למד רק מרחיב את החדר ואינו מצמצם אותו. נגזר: ניחוש מנומק, לא נמדד.
RATE_MAX_FACTOR = 2
#: kbit/s לכל MB/s של `--write-kbps` (8 × 1000).
KBPS_PER_MBS = 8000


@dataclass(frozen=True)
class BaseCall(Call):
    """``kbps``: ה-`--write-kbps` (בסיס, לפני הלולאה של K8). ``counted``: ה-serial שהבדיקה שלהם קבעה אותו. ``left_out``: מי שלא
    נספר, serial -> (בדיקת הכתיבה שלו ב-MB/s, מה שכתב בגל שפיגר בו ב-MB/s, הגל)."""
    kbps: int = 0
    counted: tuple[str, ...] = ()
    left_out: tuple = ()
    #: כל הדיסקים פיגרו בעבר, ולכן כולם נספרו (האיטי קובע) -- ולא הוחרגו.
    all_behind: bool = False


@dataclass(frozen=True)
class BoundsCall(Call):
    """``low``/``high``: `--rate-min` / `--rate-max`. ``cap``: `--rate-cap`, או ``None`` (לא נשלח)."""
    low: int = 0
    high: int = 0
    cap: int | None = None


# --- S3 ----------------------------------------------------------------------------------------------------------------


def probe_kbps(probes: list[int | None], floor_mbs: int) -> int:
    """טהור. `--write-kbps` מבדיקת הכתיבה לבדה: הבדיקה האיטית × ``MARGIN``, לא מתחת לרצפה (MB/s); בדיקה שלא נמדדה (``None``)
    או שאין בדיקה כלל = הרצפה. `ChunkEngine._write_base` והחיזוי של K3 (`brain.waveeta`, #3599: `--rate-max` הוא פי ``RATE_MAX_FACTOR``
    מזה) קוראים את אותו מספר מכאן."""
    if not probes or any(v is None for v in probes):
        return floor_mbs * KBPS_PER_MBS
    return max(floor_mbs, int(min(probes) * MARGIN)) * KBPS_PER_MBS


def left_out(measured: list[tuple[str, int | None]], history: dict[str, tuple[int, str]],
             floor_mbs: int) -> dict[str, tuple[int | None, tuple[int, str]]]:
    """טהור. הדיסקים של ``measured`` (זוגות serial, MB/s של הבדיקה) שאינם קובעים את הקצב של הגל -> {serial: (הבדיקה שלו, ההיסטוריה
    שלו)}. ``history``: serial -> (ה-MB/s שכתב בגל חתיכות קודם שפיגר בו, אותו גל), רק לדיסק שיש לו.

    דיסק עם היסטוריה אינו נספר (נדב 15/11, #3295: "ההפצה בקצב של הדיסקים המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך") -- כל עוד
    נשאר דיסק בלי היסטוריה שקובע את הקצב. כשלכל דיסק יש היסטוריה, "כולם פיגרו: האיטי קובע" טועה כשפיגור פעם אחת אינו איטיות
    (ברזל 18/11, R1: APS שכתב 48MB/s ו-Samsung שכתב 248MB/s בגל ישן שפספס בו שיהוק): דיסק שהתקרה שלו (מה שכתב × ``MARGIN``) לא
    מתחת לקצב של הבדיקה האיטית בגל עומד בו וסופר, ורק מי שמתחתיו מוחרג. כשאין כזה, כולם מוחזרים: החיתוך האמיתי כשכולם פיגרו נשאר."""
    got = {s: history[s] for s, _v in measured if s in history}
    if not got or any(s not in got for s, _v in measured):
        return {s: (v, got[s]) for s, v in measured if s in got}
    pace = probe_kbps([v for _s, v in measured], floor_mbs)
    return {s: (v, got[s]) for s, v in measured if int(got[s][0] * KBPS_PER_MBS * MARGIN) < pace}


def judge_base(measured: list[tuple[str, int | None]], history: dict[str, tuple[int, str]], floor_mbs: int, *,
               round_id: str = "") -> BaseCall:
    """טהור. הבסיס של `--write-kbps` לשיכפול של מחיצה: ``PACE`` (הדיסקים שנספרו קבעו אותו) או ``FLOOR`` (אין מה למדוד, או בדיקה
    אחת שלא נמדדה: הרצפה, כמו תמיד -- מספר שנמדד, לא ניחוש)."""
    base = (RULE_BASE, POINT_BASE, ACTION_BASE)
    evidence = [f"floor={floor_mbs}MB/s", f"margin={MARGIN:g}",
                *(f"check[{s}]={'?' if v is None else v}MB/s" for s, v in measured)]
    if not measured or any(v is None for _s, v in measured):
        why = "אין בדיקת כתיבה שנמדדה" if not measured else "בדיקת כתיבה אחת לפחות לא נמדדה: לא מנחשים לה מספר"
        return BaseCall(*base, FLOOR, round_id, f"{why}: הרצפה ({floor_mbs}MB/s)", tuple(evidence),
                        kbps=floor_mbs * KBPS_PER_MBS)
    behind = left_out(measured, history, floor_mbs)
    fast = [v for s, v in measured if s not in behind]
    counted = [s for s, _v in measured if not fast or s not in behind]
    kbps = probe_kbps(fast or [v for _s, v in measured], floor_mbs)
    evidence += [f"behind[{s}]=check {v}MB/s, wrote {h[0]}MB/s in {h[1]}" for s, (v, h) in behind.items()]
    who = f"{len(counted)} מתוך {len(measured)} דיסקים נספרו" + (", כולם פיגרו בעבר (האיטי קובע)" if behind and not fast else "")
    return BaseCall(*base, PACE, round_id,
                    f"{kbps / KBPS_PER_MBS:g}MB/s: הדיסק האיטי שנספר × {MARGIN:g}, לא מתחת לרצפה ({floor_mbs}MB/s); {who}",
                    tuple(evidence), kbps=kbps, counted=tuple(sorted(counted)),
                    left_out=tuple((s, v, h[0], h[1]) for s, (v, h) in behind.items()), all_behind=bool(behind) and not fast)


def decide_base(conn, measured, history, floor_mbs: int, *, round_id: str, part: int) -> BaseCall | None:
    """S3. נרשם ב-``brain_decisions`` לפני הביצוע. ``None`` = המוח נכשל: אין החלטה, והמנוע שם את הרצפה. לעולם אינו זורק."""
    try:
        call = judge_base(measured, history, floor_mbs, round_id=round_id)
    except Exception as exc:                    # noqa: BLE001 -- לא ידוע אינו "תקין": הרצפה, והכשל נרשם
        log.exception("brain: %s of %s failed", RULE_BASE, round_id)
        if conn is not None:
            silence.undecided(conn, RULE_BASE, POINT_BASE, ACTION_BASE, round_id, f"{type(exc).__name__}: {exc}")
        return None
    if conn is not None:
        silence.record(conn, [Call(call.rule, call.point, call.action, call.verdict, round_id, f"מחיצה {part}: {call.reason}",
                                   (*call.evidence, f"part={part}"))], round_id=round_id, entity_kind="round")
    return call


# --- S4 ----------------------------------------------------------------------------------------------------------------


def bounds(kbps: int, write_kbps: int, floor_mbs: int, learned_max: int | None = None) -> tuple[int, int]:
    """טהור. (`--rate-min`, `--rate-max`) של `chunkcast send` (#3512): עם קצב כתיבה, הבקר יורד עד הרצפה (#3170, MB/s), לא מעל
    ההתחלה, ולפחות 1, ועולה עד ``RATE_MAX_FACTOR`` פעמים ההתחלה -- או למקסימום ש-K8 למד (``learned_max``, #3511), כשהוא גבוה
    יותר (#3520: המקסימום שנלמד אינו מוריד לעולם). בלי קצב כתיבה, הקצב הוא תקציב הרשת: min = max = ההתחלה."""
    if write_kbps:
        high = write_kbps * RATE_MAX_FACTOR
        if learned_max and learned_max > high:
            high = learned_max
        return max(1, min(floor_mbs * KBPS_PER_MBS, write_kbps)), high
    return kbps, kbps


def grow_cap(kbps: int, learned_max: int | None, file_bytes: int | None, chunks: int | None, chunk_bytes: int) -> int | None:
    """טהור. `--rate-cap` (#3599): הגג שאליו `--rate-max` רשאי **לגדול** כל עוד כל המקלטים בריאים -- תקרת החוט `--kbps` (דחוסה) כקצב של
    נתונים לא דחוסים (חתיכות המחיצה × גודל חתיכה, מול הבייטים של הקובץ), קיבולת הרשת שנמדדה. ``None`` = לא גדל: מקסימום ש-K8 למד
    לדיסקים של הגל הוא כל מה שהם הוכחו לכתוב, ובלי חוט, גודל או מספר חתיכות אין במה להמיר אותו."""
    if learned_max or not kbps or not file_bytes or not chunks or file_bytes <= 0 or chunks <= 0:
        return None
    return kbps * chunks * chunk_bytes // file_bytes


def judge_bounds(kbps: int, write_kbps: int, floor_mbs: int, learned_max: int | None = None, *, file_bytes: int | None = None,
                 chunks: int | None = None, full_pass: bool = False, chunk_bytes: int = 4 * 1024 * 1024,
                 round_id: str = "") -> BoundsCall:
    """טהור. כל הדגלים של הבקר בתוך הגל: ``--rate-min``, ``--rate-max``, ו-``--rate-cap`` (רק במעבר מלא של מחיצה, כשהוא מעל
    ``--rate-max``; מעבר השלמה קצר ואינו מרחיב)."""
    low, high = bounds(kbps, write_kbps, floor_mbs, learned_max)
    cap = grow_cap(kbps, learned_max, file_bytes, chunks, chunk_bytes) if full_pass else None
    if not (write_kbps and cap is not None and cap > high):
        cap = None
    evidence = (f"kbps={kbps}", f"write_kbps={write_kbps}", f"floor={floor_mbs}MB/s", f"factor={RATE_MAX_FACTOR}",
                f"learned_max={learned_max if learned_max else 'none'}", f"full_pass={int(full_pass)}")
    return BoundsCall(RULE_BOUNDS, POINT_BOUNDS, ACTION_BOUNDS, SET, round_id,
                      f"--rate-min {low} --rate-max {high}" + (f" --rate-cap {cap}" if cap is not None else ""), evidence,
                      low=low, high=high, cap=cap)


def decide_bounds(conn, kbps: int, write_kbps: int, floor_mbs: int, learned_max: int | None, *, round_id: str, part: int,
                  file_bytes: int | None, chunks: int | None, full_pass: bool, chunk_bytes: int) -> BoundsCall | None:
    """S4. נרשם ב-``brain_decisions``. ``None`` = המוח נכשל: אין החלטה, והמנוע מקבע את הקצב (min = max = מה שהתבקש), בלי חדר לבקר."""
    try:
        call = judge_bounds(kbps, write_kbps, floor_mbs, learned_max, file_bytes=file_bytes, chunks=chunks, full_pass=full_pass,
                            chunk_bytes=chunk_bytes, round_id=round_id)
    except Exception as exc:                    # noqa: BLE001
        log.exception("brain: %s of %s failed", RULE_BOUNDS, round_id)
        if conn is not None:
            silence.undecided(conn, RULE_BOUNDS, POINT_BOUNDS, ACTION_BOUNDS, round_id, f"{type(exc).__name__}: {exc}")
        return None
    if conn is not None:
        silence.record(conn, [Call(call.rule, call.point, call.action, call.verdict, round_id, f"מחיצה {part}: {call.reason}",
                                   (*call.evidence, f"part={part}"))], round_id=round_id, entity_kind="round")
    return call
