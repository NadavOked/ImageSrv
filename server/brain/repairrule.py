"""#3659 (אפיק #3653, הכרעת נדב 18/11): ניתוב ההשלמה אחרי המעבר הראשון -- multicast או HTTP (S10), כמה סבבי השלמה (S11) ומתי דיסק איטי משלים ולא נכשל (S12).
המוח מחליט, `rounds/repair.py` ו-`rounds/chunksend.py` מבצעים.

עד כאן `THRESHOLD` (2) ו-`MAX_REPAIRS` (5) היו קבועים בקוד של `repair.py`, ו-`slow_reason` ישב שם: "הצעה של התכנון ולא מדידה". עכשיו הערכים וההשוואות כאן
(**לא קבועים בקוד אלא ערך החלטה**), ו-`repair.plan` רק חותך טווחים לפי הערך שקיבל, ו-`chunksend` רושם את ההחלטה ומוסר את התוכנית.

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| S10 `repair.plan` | ``K-REPAIR`` | חתיכה שחסרה ל-``MULTICAST_MIN`` (2) מחשבים ומעלה משודרת שוב לכולם, אחרת המחשב מוריד אותה ב-HTTP; בהפצה ישירה (אין קובץ בשרת) הסף 1 | `repair.plan`: חותך |
| S11 `repair.plan`, `chunksend._answer` | ``K-REPAIR`` | דיסק שעדיין חסר אחרי ``MAX_ROUNDS`` (5) סבבי השלמה נכשל, בשמו (עיקרון 5: "לא מושלם = נכשל"; **המוח קובע את המכסה, לא אם "כמעט" נחשב הצלחה**) | `plan.failed` / `w.failed_disks` |
| S12 `repair.slow_reason` | ``K-REPAIR`` | דיסק שפיגר במעבר הראשון (כתב לאט מהרצפה, לפי מה שהמקלט אמר ש"הדיסק לא הספיק") משלים ואינו נכשל | `plan.slow`: שורת יומן |
| S14 `ChunkEngine._answer` | ``K-REPAIR`` | מה עונים למשכפל שדיווח: ``receive`` (להאזין לשידור), ``fetch`` (להוריד בעצמו, גם מי שמאחר אחרי שהמחיצה נסגרה), ``done``, או ``wait`` | `_answer`: בונה את התשובה |

**S17 (carousel)** כבר במוח: `brain.carousel` (B7, B12, B47) מחליט ו-`ChunkEngine` רק מקבל את התוכנית שהוא מחזיר (`brain_carousel`, כבוי); אין שם ערך להעביר. **מה שאינו כאן:** השלמה מהשכן (`peerfill`, `brain_peer_mode`) ו-carousel (`brain_carousel`) **כבויים, ולא הודלקו**.

**מצב.** ``shadow`` (`brain_veto_mode`): כל תוכנית השלמה (מחיצה x סבב) היא שורה ב-``brain_decisions`` עם הראיות (הסף, המכסה, כמה חתיכות לאן, מי נכשל ומי איטי); ``off`` מחליט ואינו
רושם. **הערכים לא השתנו** (2 / 5 / אותה נוסחת `wrote`), ולכן ההתנהגות זהה להתנהגות של לפני #3659 ו"הוא מבצע גם בצל". **לא מורידים את המכסה:** ``MAX_ROUNDS`` הוא
העיקרון "דיסק שחסר אחרי המכסה נכשל" כערך החלטה, ולא כניסיון לקבל "כמעט".
**אין החלטה = ממתינים** (עיקרון 1 ו-5): ב-S14 כלל שנפל מחזיר ``None``, והמנוע עונה ``wait`` (הסוכן שואל שוב בעוד שתי שניות) -- לא ``done`` ולא ``fetch``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from server.brain import silence
from server.brain.silence import Call

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K-REPAIR", "REPAIR_PLAN"
POINT = "S10/S11/S12"
ANSWER_ACTION, ANSWER_POINT = "REPAIR_ANSWER", "S14"
#: S14: ``ANSWER_RECEIVE``: להאזין למעבר; ``ANSWER_FETCH``: להוריד בעצמו; ``ANSWER_DONE``: שלם; ``ANSWER_WAIT``: ממתינים (נשאל בכל דופק, ולכן אינו נרשם).
ANSWER_RECEIVE, ANSWER_FETCH, ANSWER_DONE, ANSWER_WAIT = "ANSWER_RECEIVE", "ANSWER_FETCH", "ANSWER_DONE", "ANSWER_WAIT"
#: ``PLAN``: סבב השלמה מתוכנן; ``DONE``: אין מה לשדר ואין מה להוריד; ``EXHAUSTED``: דיסק לפחות נכשל בגלל המכסה בסבב הזה.
PLAN, DONE, EXHAUSTED = "PLAN", "DONE", "EXHAUSTED"

#: S10, D8, T1: כמה מחשבים צריכים להחסיר חתיכה כדי שהיא תשודר שוב לכולם. **הצעה של התכנון ולא מדידה** (design-2533-2528-unified.md סעיף 5.3).
MULTICAST_MIN = 2
#: S11, D12, T2: כמה סבבי השלמה (מולטיקאסט או HTTP) דיסק מקבל אחרי המעבר הראשון. אותו מקור: הצעה, לא מדידה. הוא מכסה, לא "כמעט".
MAX_ROUNDS = 5


def threshold(direct: bool = False) -> int:
    """טהור. S10: הסף למולטיקאסט. הפצה ישירה (#3161): אין קובץ בשרת להוריד ממנו, ולכן הסף 1 -- כל מה שחסר, גם למחשב אחד, חוזר מהמקור במולטיקאסט."""
    return 1 if direct else MULTICAST_MIN


def exhausted(repairs_done: int) -> bool:
    """טהור. S11: האם המכסה נגמרה. ``repairs_done`` = כמה סבבי השלמה כבר רצו (או כמה סבבים מאז שדיסק מאחר נכנס)."""
    return repairs_done >= MAX_ROUNDS


#: תחילת הסיבה שהמפעיל רואה כשהמכסה נגמרה: `judge_plan` מזהה לפיה דיסק שנכשל בגלל המכסה (ולא מסיבה אחרת, למשל סירוב HDD).
EXHAUSTED_PREFIX = "לא הצליח לקבל את כל הנתונים אחרי"


def exhausted_text(missing: int) -> str:
    """הסיבה שהמפעיל רואה, בשם הדיסק, כשהמכסה נגמרה."""
    return f"{EXHAUSTED_PREFIX} {MAX_ROUNDS} ניסיונות השלמה ({missing} חלקים עדיין חסרים)"


def slow_reason(taken: int, skipped: int | None, asked_mbps: float | None, floor_mbps: int | None) -> str | None:
    """טהור. S12, #3170 (הכרעת נדב 14/11: החדר לא מאט; 15/11, אפשרות ב': והדיסק האיטי **אינו נכשל**): הסיבה, כשהדיסק כתב במעבר הראשון לאט מהמינימום -- אחרת None.
    היא נרשמת ליומן בלבד (`Plan.slow`).

    ``skipped``: חתיכות שהגיעו למחשב והמקלט ויתר עליהן **לדיסק הזה**, כי התור שלו היה מלא (``chunkcast``: "the drive was behind"). ``taken``: מה שעמיד עליו.
    הזרם ביקש לכתוב ``asked_mbps`` (``--write-kbps``); דיסק שלקח רק ``taken / (taken + skipped)`` ממה שהגיע כתב בערך בחלק הזה מהקצב. כשהזרם עצמו היה איטי מהמבוקש
    (הרשת), המספר גבוה מהאמת -- ההערכה מקלה עם הדיסק, לעולם לא מחמירה. בלי ``skipped`` (סוכן שלא דיווח), או בלי קצב וסף, אין ראיה לאיטיות (עיקרון 5ב): None, והחוסר מושלם."""
    if not skipped or not asked_mbps or not floor_mbps or taken < 0:
        return None
    wrote = asked_mbps * taken / (taken + skipped)
    if wrote >= floor_mbps:
        return None
    return (f"הדיסק פיגר בשידור הראשון: כתב בכ-{wrote:.0f}MB/s ({skipped} מתוך {taken + skipped}"
            f" החלקים שהגיעו לא נכתבו כי הוא פיגר) — מתחת למהירות המינימלית לכונן ({floor_mbps}MB/s)."
            " הוא אינו נכשל: החלקים החסרים משלימים לו מהמגירה השכנה או מהשרת")


def judge_plan(*, plan, repairs_done: int, direct: bool, machines: int, round_id: str = "", part: int = 0) -> Call:
    """טהור. פסק על תוכנית השלמה אחת (מחיצה x סבב) -- מה שהתוכנית עצמה כבר חישבה לפי הסף והמכסה של המוח: ``EXHAUSTED`` (דיסק נכשל בגלל המכסה), ``DONE``, או ``PLAN``."""
    base = (RULE, POINT, ACTION)
    multicast = sum(hi - lo + 1 for lo, hi in plan.multicast)
    fetch = {mac: sum(hi - lo + 1 for lo, hi in ranges) for mac, ranges in plan.fetch.items()}
    capped = [k for k, why in plan.failed.items() if why.startswith(EXHAUSTED_PREFIX)]
    evidence = (f"part={part}", f"round={repairs_done + 1}", f"multicast_min={threshold(direct)}", f"max_rounds={MAX_ROUNDS}", f"direct={int(direct)}",
                f"machines={machines}", f"multicast_chunks={multicast}", f"receive={len(plan.receive)}",
                f"fetch={ {m: n for m, n in sorted(fetch.items())} }", f"failed={len(plan.failed)}", f"capped={len(capped)}", f"slow={len(plan.slow)}")
    if capped:
        return Call(*base, EXHAUSTED, round_id, f"מחיצה {part}: {len(capped)} דיסקים נכשלו אחרי {MAX_ROUNDS} סבבי השלמה", evidence)
    if plan.done:
        return Call(*base, DONE, round_id, f"מחיצה {part}: אין מה לשדר ואין מה להוריד", evidence)
    return Call(*base, PLAN, round_id,
                f"מחיצה {part}, סבב {repairs_done + 1}: {multicast} חתיכות במולטיקאסט (חסרות ל-{threshold(direct)} מחשבים ומעלה) ל-{len(plan.receive)} מחשבים,"
                f" ו-{len(fetch)} מחשבים מורידים ב-HTTP", evidence)


def record_plan(conn, plan, *, repairs_done: int, direct: bool, machines: int, round_id: str, part: int) -> None:
    """S10/S11/S12: רושם את ההחלטה ב-``brain_decisions`` ומסמן שנמסרה לביצוע. לעולם אינו זורק: מוח שנפל אינו עוצר גל, והשרת כבר ביצע את מה שהחליט ב-`plan`."""
    if conn is None:
        return
    try:
        call = judge_plan(plan=plan, repairs_done=repairs_done, direct=direct, machines=machines, round_id=round_id, part=part)
        rows = silence.record(conn, [call], round_id=round_id, entity_kind="round")
        silence.settle(conn, rows.get(call.target), "התוכנית נמסרה לביצוע")
    except Exception:                           # noqa: BLE001 -- רישום אינו מפיל גל
        log.exception("brain: %s of %s part %s was not recorded", RULE, round_id, part)


# --- S14: מה עונים למשכפל ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerCall(Call):
    """``pass_``/``ranges``: לתשובות ``receive`` / ``fetch``. ``whole``: כל הדיסקים שלו שלמים וההמתנה היא למחשבים אחרים; ``others``: הוא כבר דיווח `ended` על
    הסבב הנוכחי והשרת מחכה לאחרים -- שתיהן הודעות להמתנה שהסוכן אינו סופר מול התקרה שלו."""
    pass_: int = 0
    ranges: tuple = ()
    whole: bool = False
    others: bool = False


def judge_answer(*, part_done: bool, late_ranges, no_lacks: bool, has_mark: bool, ended_now: bool, planned: bool, in_receive: bool,
                 fetch, rnd: int, wave_round: int, mac: str = "", round_id: str = "") -> AnswerCall:
    """טהור. מה עונים למשכפל שדיווח על מעבר ``rnd`` של מחיצה:

    - המחיצה כבר נסגרה (``part_done``, דיווח אחרי התקרה): אין צורך בשולח כדי להשלים מחשב אחד -- שלם: ``done``, חסר: ``fetch`` של האיחוד ``late_ranges``, בסבב ``rnd + 1``.
    - אחרת, עוד לא כולם דיווחו או שהשרת עוד לא תכנן (``planned`` שקר): ``wait``; ``whole`` כשאין לו חוסר ``no_lacks``, ``others`` כשהוא דיווח על הסבב הנוכחי (``ended_now``).
    - התוכנית של הסבב הנוכחי: ``receive`` אם הוא מאזין (``in_receive``), ``fetch`` של ``fetch`` אם הוא מוריד, אחרת ``wait`` (שלם: ממתין ל-`done` של המחיצה כולה)."""
    base = (RULE, ANSWER_POINT, ANSWER_ACTION)
    evidence = (f"part_done={int(part_done)}", f"rnd={rnd}", f"wave_round={wave_round}", f"lacks={int(not no_lacks)}", f"planned={int(planned)}")
    if part_done:
        if no_lacks:
            return AnswerCall(*base, ANSWER_DONE, mac, "המחיצה נסגרה והמחשב שלם", evidence)
        return AnswerCall(*base, ANSWER_FETCH, mac, "המחיצה נסגרה בלעדיו: מוריד את מה שחסר לו בעצמו", evidence,
                          pass_=rnd + 1, ranges=tuple(tuple(r) for r in late_ranges or ()))
    whole, others = has_mark and no_lacks, has_mark and ended_now
    if planned and in_receive:
        return AnswerCall(*base, ANSWER_RECEIVE, mac, f"מאזין למעבר {wave_round}", evidence, pass_=wave_round)
    if planned and fetch is not None:
        return AnswerCall(*base, ANSWER_FETCH, mac, f"מוריד בעצמו בסבב {wave_round}", evidence, pass_=wave_round,
                          ranges=tuple(tuple(r) for r in fetch))
    return AnswerCall(*base, ANSWER_WAIT, mac, "ממתינים", evidence, whole=whole, others=others)


_answered: set[tuple] = set()
_answered_lock = threading.Lock()
ANSWERED_KEYS = 512


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי תשובות רשומות."""
    with _answered_lock:
        _answered.clear()


def decide_answer(conn, **facts) -> AnswerCall | None:
    """S14. ``None`` = המוח נכשל: אין החלטה, והמנוע עונה ``wait``. תשובה ממתינה אינה נרשמת (נשאלת בכל דופק); שאר התשובות נרשמות פעם אחת לכל (גל, מחיצה,
    מחשב, תשובה, מעבר), כדי שדיווח חוזר לא ימלא את ההחלטות. לעולם אינו זורק."""
    try:
        call = judge_answer(**facts)
    except Exception as exc:                    # noqa: BLE001 -- לא ידוע אינו "שלם": ממתינים
        log.exception("brain: %s of %s failed", ANSWER_ACTION, facts.get("round_id"))
        if conn is not None:
            silence.undecided(conn, RULE, ANSWER_POINT, ANSWER_ACTION, facts.get("round_id") or "", f"{type(exc).__name__}: {exc}")
        return None
    if conn is None or call.verdict == ANSWER_WAIT:
        return call
    key = (facts.get("round_id"), facts.get("mac"), call.verdict, call.pass_, tuple(call.ranges))
    with _answered_lock:
        if key in _answered:
            return call
        if len(_answered) >= ANSWERED_KEYS:
            _answered.clear()
        _answered.add(key)
    try:
        rows = silence.record(conn, [call], round_id=facts.get("round_id") or "", entity_kind="machine")
        # #3750: התשובה מוחזרת למנוע באותה קריאה, ומשם לתגובת הדיווח של המשכפל
        silence.settle(conn, rows.get(call.target), f"התשובה {call.verdict} נמסרה למנוע")
    except Exception:                           # noqa: BLE001 -- רישום אינו מפיל תשובה
        log.exception("brain: %s of %s was not recorded", ANSWER_ACTION, facts.get("round_id"))
    return call
