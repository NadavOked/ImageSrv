"""#3657 (אפיק #3653, הכרעת נדב 18/11): חיות הגל בשרת. המוח מחליט, והשרת מבצע בלבד.

עד כאן ההחלטות "מתי חבר גל אבד", "הזרם נעצר ולא הכונן", "מתי מעירים שוב" ו"מתי משימה שקטה נכשלת" ישבו בתוך `room.py`,
`pulls.py` ו-`silent_tasks.py`: סף, טיימר והשוואה, מפוזרים בשרת. עכשיו הסף וההשוואה כאן, ושם נשארו רק שני דברים: **איסוף
העובדות** (חותמות, ספירות, מי נראה) ו**ביצוע התוצאה** (סימון `lost`, שליחת WoL, הכשלת סבב או משימה). אין בשרת עותק של
הסף ואין עותק של ההשוואה.

| נקודה (מיפוי 18/11) | כלל | מה מוחלט | מי מבצע |
|---|---|---|---|
| S22 `room._mark_lost` | ``K-LOST`` | חבר גל שלא דיווח התקדמות מעל הסף הוא `lost`; חותמת בעתיד היא קפיצת שעון, לא אובדן | `_mark_lost`: מסמן; מחיקת היומן נשארת מאחורי הווטו (K2, D21), ולא עברה לכאן |
| S23 `room._stream_stalled` | ``K-STALL`` | כל המגירות הכותבות עצרו יחד (180 ש'): "הזרם נעצר", לא "הכונן"; **#3896:** מגירה ש`waiting_disk` = "הכונן קפא" (`DISK_STALLED`), והשלמה ב-HTTP בלבד = `NO_STREAM` | `status_view`: מציג תווית |
| S24 `room._rewake` | ``K-REWAKE`` | WoL חוזר לגל פתוח, וכמה זמן עד שהסבב נכשל | `_rewake`: שולח, או מכשיל |
| S30 `pulls._failure`, `silent_tasks.sweep` | ``K-SILENT`` | משיכה או משימה שקטה נכשלת | `pulls.sweep`, `silent_tasks.sweep` |

**מצב.** המתג הוא `brain_veto_mode` (כמו כל כללי המוח): ``shadow`` (ברירת המחדל) ו-``enforce`` רושמים כל החלטה ב-
``brain_decisions`` עם הראיות, והשרת מבצע את ההחלטה; ``off`` **אינו מחזיר לקוד ישן** (אין קוד ישן): ההחלטה מתקבלת כרגיל, ורק
לא נרשמת. **הספים לא השתנו**: אותם 180/180/60x6+180/600 כמו בקוד שהוסר, ולכן ההתנהגות זהה להתנהגות של לפני #3657. מה
ש-``enforce`` יוסיף על כללים אלה (ראיות החיות הרחבות של K1, הווטו) יוכרע בשער #3563, אחרי אימות על ברזל; עד אז הוא אינו
משנה פסק.

**אין החלטה = לא עושים כלום** (עיקרון 1 ו-5). כלל שנפל, או עובדות שאינן נקראות, מחזירים ``UNDECIDED`` או ``NOT_MEASURED``, ולעולם
לא "תקין": הסימון, ה-WoL והכשלת הסבב והמשימה לא קורים, והנקודה נשאלת שוב בדופק הבא. הכשל נרשם בלוג, ושורת
``brain_decisions`` אחת לכל (כלל, גל) בתהליך.

**החלטה נרשמת לפני הביצוע, והתוצאה אחריו** (`settle`): "סומן אבוד" או "דיווח הגיע בינתיים, לא סומן". כך ``brain_decisions`` אומר
גם מה יצא מההחלטה, ולא רק מה הוחלט.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE_LOST, RULE_STALL, RULE_REWAKE, RULE_SILENT = "K-LOST", "K-STALL", "K-REWAKE", "K-SILENT"
POINT_LOST, POINT_STALL, POINT_REWAKE, POINT_SILENT = "S22", "S23", "S24", "S30"
LOST, FUTURE, UNMEASURED, UNDECIDED = "LOST", "FUTURE", "UNMEASURED", "UNDECIDED"
SEND, WAIT, FAIL, CALIBRATE = "SEND", "WAIT", "FAIL", "CALIBRATE"
KEEP, FAIL_SILENT, FAIL_REPORTED = "KEEP", "FAIL_SILENT", "FAIL_REPORTED"
STREAM_STALLED, NOT_STALLED, NOT_MEASURED = "STREAM_STALLED", "NOT_STALLED", "NOT_MEASURED"
#: #3723: השידור של הגל הסתיים (`send_done`). מצב שלישי: **לא רלוונטי**, ולא `NOT_STALLED` ("הזרם זז") ולא `NOT_MEASURED`.
STREAM_ENDED = "STREAM_ENDED"
#: #3896: מגירה כותבת שהסוכן אמר עליה `waiting_disk` (הזרם המשיך להגיע אחרי שהיא עצרה) -- **הכונן** קפא, לא הזרם. ו-``NO_STREAM``:
#: כל המגירות הכותבות משלימות ב-HTTP (`rejoin.step=http`) -- אין שידור שיכול "לעצור", והשתיקה שלהן אינה ראיה לזרם.
DISK_STALLED, NO_STREAM = "DISK_STALLED", "NO_STREAM"
#: מצב המתג: ``off`` = מחליטים ולא רושמים. (``shadow``/``enforce`` רושמים; ראו המסמך למעלה.)
OFF = "off"

#: S23, T38: כמה שניות בלי תנועה מגירה נחשבת עצורה. נגזר מ-1,896 דגימות של סבב 08/09: מגירה בריאה עצרה עד 120 שניות
#: (מדידה, 2026-09-08, מעבדה). היה זהה ל-`ROOM_STALL_S` של מסך הטקסט בסוכן, שהוסר ב-11/10.
STALL_S = 180

#: S24, #1721 (T42): WoL חוזר לגל פתוח, כל `REWAKE_S`, עד `REWAKE_MAX` פעמים, למחשב שיכפול שנבחר, שיש לו עוד מה לכתוב ושלא נראה
#: מאז שהגל נפתח. ה-WoL של הפתיחה נשלח כשהמשכפל עוד בספירת הכיבוי (`END_POWEROFF_S`=20; עצירה שלה אין לה תקרה עד "כבה", #2024,
#: #3037), ומכונה דלוקה בולעת אותו. 6x60 מכסה את 320 השניות של ספירה שנעצרה; תקרה, כי WoL בלולאה פתוחה מעמיס על הרשת. אחרי
#: התקרה, `REWAKE_BOOT_S` לאתחול, ומכונה שעדיין לא נראתה מפילה את הסבב בשמה (ערכי התחלה, נגזרים: "ספירה שנעצרה 320 ש'").
REWAKE_S = 60
REWAKE_MAX = 6
REWAKE_BOOT_S = 180

#: S30, #1573 (T-SILENT): מעל כמה שניות בלי **שום דיווח** מהמכונה משיכה (`pulls`) או משימה `running` (`silent_tasks`) נכשלת. הסוכן מדווח
#: כל ~2ש' לכל אורך העבודה, ודיווח בודד יכול להימשך ~40ש' (`curl --max-time 10 --retry 3`); אתחול של מכונה שחוזרת ל-PXE באמצע משימה לוקח
#: דקות, והיא אמורה לקבל אותה בחזרה. השוליים רחבים מהחדר (180) בכוונה: משיכות ומשימות אינן נסגרות בעליית שרת (`reconcile` נוגע בשידור
#: בלבד), ועדכון או אתחול שרת בן כמה דקות עוצר את הדיווחים של עבודה בריאה, ואסור שיפיל אותה. נגזר, לא נמדד. ערך אחד לשתיהן: היו שני
#: עותקים זהים של 600, `pulls.SILENT_SECONDS` ו-`silent_tasks.SILENT_SECONDS`.
SILENT_S = 600
#: #3038 (נדב 12/11: "גם שחזור מחכה לפי ההגדרה"): מעל `lost_wait_seconds` התקרה של **משיכה** נגזרת ממנה. הסוכן מחזיק משיכה עד `lost + 120`
#: ודיווח בודד שלו עד ~40ש', ולכן 180: השרת אינו מוותר לפני הסוכן. ב-180 (ברירת המחדל) זה 360, והתקרה נשארת 600.
LOST_MARGIN_S = 180


@dataclass(frozen=True)
class Call:
    """החלטה אחת: על מי, מה, ולמה. ``rule``/``point``/``action`` קובעים איך היא נרשמת."""
    rule: str
    point: str
    action: str
    verdict: str
    target: str                  # mac, או מזהה הגל/הסבב
    reason: str
    evidence: tuple[str, ...] = ()


def _stamp(raw: object) -> datetime | None:
    """חותמת ISO, או ``None`` כשאין או שאינה נקראת. אינה מניחה אזור זמן: ההשוואה מול `now` נעשית אצל הקורא."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# --- נרשם ---------------------------------------------------------------------------------------------------------


def _mode(conn) -> str:
    from server.brain import veto               # noqa: PLC0415 — veto כבד, ומי שקורא לכאן כבר טען אותו
    return veto.mode(conn)


UNDECIDED_HE = "לא הוחלט: אין החלטה שאפשר לבצע"
FAILED_KEYS = 256
_failed: set[tuple[str, str]] = set()           # (כלל, גל) שכבר נרשם עליהם UNDECIDED בתהליך הזה
_failed_lock = threading.Lock()


def record(conn, calls, *, round_id: str | None, entity_kind: str = "machine") -> dict[str, int | None]:
    """רושם כל החלטה ב-``brain_decisions`` (יעד -> מספר השורה). ``off`` או כשל כתיבה = ``None`` לאותו יעד: ההחלטה
    בכל מקרה כבר התקבלה, והשרת מבצע אותה. לעולם אינו זורק."""
    if not calls:
        return {}
    ids: dict[str, int | None] = {}
    try:
        mode = _mode(conn)
    except Exception:                           # noqa: BLE001 — המתג שלא נקרא אינו מבטל את הביצוע
        log.exception("brain: the mode was not read, %s of %s not recorded", [c.rule for c in calls], round_id)
        return {c.target: None for c in calls}
    for c in calls:
        # #3750: "לא הוחלט" אינו ביצוע -- שורה כזו מסומנת n/a, ולא נשארת ריקה כמו החלטה שלא נסגרה
        out = memory.na(UNDECIDED_HE) if c.verdict == UNDECIDED else None
        ids[c.target] = None if mode == OFF else memory.record(
            conn, mode=mode, rule=c.rule, point=c.point, action=c.action, verdict=c.verdict, reason=c.reason,
            evidence=c.evidence, round_id=round_id, entity=(entity_kind, c.target) if entity_kind else None, outcome=out)
    return ids


def settle(conn, row: int | None, outcome: str) -> None:
    """מה שהשרת עשה עם ההחלטה. ``None`` (לא נרשמה) = אין מה לסגור. לעולם אינו זורק."""
    if row is not None:
        memory.resolve(conn, [row], outcome)


def undecided(conn, rule: str, point: str, action: str, round_id: str, why: str) -> None:
    """הכלל נפל: שורה אחת לכל (כלל, גל) בתהליך, כדי שכשל קבוע לא ימלא את ההחלטות. השרת אינו מבצע דבר."""
    key = (rule, round_id)
    with _failed_lock:
        if key in _failed:
            return
        if len(_failed) >= FAILED_KEYS:
            _failed.clear()                     # הרשם אינו גדל לנצח: אחרי הניקוי כשל קבוע נרשם שוב פעם אחת
        _failed.add(key)
    record(conn, [Call(rule, point, action, UNDECIDED, round_id,
                       f"המוח לא הצליח להחליט ({why}): השרת לא עושה דבר ושואל שוב בדופק הבא")], round_id=round_id,
           entity_kind="round")


def forget() -> None:
    """לבדיקות: התהליך מתחיל בלי כשלים רשומים ובלי עצירות פתוחות."""
    with _failed_lock:
        _failed.clear()
    with _stall_lock:
        _stalled.clear()


# --- S22: חבר גל אבד ----------------------------------------------------------------------------------------------


def judge_lost(members, *, started_at: object, now: datetime, lost_s: float, jump_s: float,
               final) -> list[Call]:
    """טהור. לכל חבר גל שאינו במצב סופי: ``LOST`` (לא דיווח מעל ``lost_s``), ``FUTURE`` (חותמת מעבר ל-``jump_s``
    בעתיד: קפיצת שעון שהגלאי לא ראה, מכוילת ל"עכשיו" ולא נספרת כאבוד), או ``UNMEASURED`` (אין חותמת). חבר חי אינו
    מוחזר.

    הדופק הנמדד הוא progress ולא hello (#450): מכונה שכותבת מפסיקה לשלוח hello אך מדווחת התקדמות כל ~2 שניות (#449), ולכן
    הפקעה לפי hello הייתה קוטלת מכונה בריאה עסוקה בכתיבה. `updated_at` זז על כל דיווח (וגם על מגירה קפואה: הוא זמן הדיווח,
    לא זמן התנועה), ומכונה שנעלמה מפסיקה להזיז אותו. השרת מעביר את סף האובדן בפועל
    (`agent_timing.effective_lost_seconds`, #4044).

    הגרייס נמדד מ-`max(updated_at, started_at)`: גל שרק התחיל נותן לכל חבר ``lost_s`` להשמיע קול ראשון (#450). שני
    המקורות ריקים = ``UNMEASURED``, לא "אבוד" (עיקרון 5)."""
    ahead = now + timedelta(seconds=jump_s)
    floor = _stamp(started_at)
    out = []
    for m in members:
        if m["state"] in final:
            continue
        stamps = [(name, s) for name, s in (("updated_at", _stamp(m["updated_at"])), ("started_at", floor))
                  if s is not None]
        if not stamps:
            out.append(Call(RULE_LOST, POINT_LOST, "MARK_LOST", UNMEASURED, m["mac"],
                            "אין חותמת דיווח וגם אין חותמת התחלה: לא נמדד, ולא מכריזים אבוד"))
            continue
        source, last = max(stamps, key=lambda p: p[1])
        age = (now - last).total_seconds()
        evidence = (f"state={m['state']}", f"last={source}@{last.isoformat(timespec='seconds')}",
                    f"limit={lost_s:g}s")
        if last > ahead:
            out.append(Call(RULE_LOST, POINT_LOST, "MARK_LOST", FUTURE, m["mac"],
                            f"החותמת {source} בעתיד ({-age:.0f}ש'): קפיצת שעון, לא אובדן. מכוילת ל'עכשיו'",
                            (*evidence, f"ahead={-age:.0f}s")))
        elif age > lost_s:
            out.append(Call(RULE_LOST, POINT_LOST, "MARK_LOST", LOST, m["mac"],
                            f"לא דווחה התקדמות {age:.0f}ש' (הסף {lost_s:g}ש')", (*evidence, f"age={age:.0f}s")))
    return out


def decide_lost(conn, members, *, wave_id: str, started_at: object, now: datetime, lost_s: float, jump_s: float,
                final) -> list[Call] | None:
    """S22: מי אבד ומי חותמתו בעתיד. ``None`` = המוח נכשל: אין החלטה, והשרת אינו מסמן ואינו מכייל דבר. לעולם אינו זורק."""
    try:
        return judge_lost(members, started_at=started_at, now=now, lost_s=lost_s, jump_s=jump_s, final=final)
    except Exception as exc:                    # noqa: BLE001 — לא ידוע אינו "אף אחד לא אבד", אבל גם אינו אבוד
        log.exception("brain: %s of %s failed", RULE_LOST, wave_id)
        undecided(conn, RULE_LOST, POINT_LOST, "MARK_LOST", wave_id, f"{type(exc).__name__}: {exc}")
        return None


# --- S24: WoL חוזר, ומתי הסבב נכשל ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RewakeCall(Call):
    """``n``/``of``: מספר ההערה ומתוך כמה (``SEND`` בלבד); הספירה היא שורות `room_rewake` ביומן, ולכן היא שורדת אתחול."""
    n: int = 0
    of: int = 0


def judge_rewake(*, round_id: str, sent: int, age_s: float, since_last, quiet: int, waiting: int,
                 silent_for_s: float | None = None) -> RewakeCall:
    """טהור. מה לעשות עם גל פתוח שיש בו מחשבים שלא נראו: ``SEND`` (הערה חוזרת, מס' ``sent + 1`` מתוך ``REWAKE_MAX``), ``WAIT``,
    ``FAIL`` (הסבב נכשל בשמם, אחרי התקרה והאתחול), או ``CALIBRATE`` (שעון הגל בעתיד ואין ממה למדוד: מכיילים ל'עכשיו').

    ``quiet`` = כמה מהם שקטים (WoL ישפיע), ``waiting`` = כמה דלוקים ומחזיקים תוצאות (#2031): WoL אליהם לשווא, והם נספרים רק לקראת
    הכשל בשמם. ``since_last`` = פונקציה בלי ארגומנטים: כמה שניות מאז ההערה האחרונה של הגל (``None`` = לא נשלחה אחת או שאינה נקראת);
    נקראת רק כשהשעון בעתיד, כמו שהקוד שהוסר קרא אותה.

    **שעון בעתיד** (#1771, ברזל: `created_at` שלוש שעות קדימה): גיל שלילי היה מדכא כל הערה. מודדים מההערה האחרונה (שנכתבה אחרי
    הקפיצה, כלומר זמן אמיתי): המרווח בין ההערות נשאר ``REWAKE_S`` והתקרה עדיין מכשילה, וגיל שלילי לא קופא ולא שורף שש הערות בנשימה
    אחת. #3590: גל שממתין רק למחזיק לא שולח הערות, ואין לו חותמת זמן אמיתי למדוד ממנה, ולכן מכיילים אותו ל'עכשיו' והתקרה רצה מכאן.

    **#3912:** ``silent_for_s`` הוא כמה שניות עברו מאז שהמכונה השקטה עזבה את המוחזק (השרת מודד; ``None`` = לא כולם עזבו מוחזק
    בגל הזה, והשעון נשאר פתיחת הגל). גל שחיכה דקות למוחזק כבר מקיים ``age >= (sent+1)*REWAKE_S`` לכל ההערות, והתקרה כבר מאחוריו,
    ולכן בלי זה ההערות יוצאות בכל דופק והאתחול לא מקבל את ``REWAKE_BOOT_S``. כשהוא נתון הוא **מחליף** את גיל הגל — לא נוסף עליו."""
    base = (RULE_REWAKE, POINT_REWAKE, "REWAKE")
    evidence = [f"sent={sent}/{REWAKE_MAX}", f"quiet={quiet}", f"held={waiting}", f"age={age_s:.0f}s"]
    age = age_s
    origin = "מאז שהגל נפתח"
    if silent_for_s is not None:
        age = silent_for_s
        origin = "מאז שעבר משקט"
        evidence.append(f"silent_for={silent_for_s:.0f}s")
    elif age < 0:
        since = since_last()
        if since is None and not quiet:
            return RewakeCall(*base, CALIBRATE, round_id,
                              f"חותמת פתיחת הגל בעתיד ({-age_s:.0f}ש') ואין הערה שנשלחה למדוד ממנה: מכיילים ל'עכשיו', והתקרה רצה מכאן",
                              tuple(evidence))
        since = REWAKE_S if since is None else since
        age = sent * REWAKE_S + max(0.0, since)
        evidence.append(f"clock_step: age measured from the last re-wake ({since:.0f}s ago) -> {age:.0f}s")
    if quiet and sent < REWAKE_MAX:
        if age >= (sent + 1) * REWAKE_S:
            return RewakeCall(*base, SEND, round_id,
                              f"הערה {sent + 1} מתוך {REWAKE_MAX}: עברו {age:.0f}ש' {origin} (הערה כל {REWAKE_S}ש')",
                              tuple(evidence), n=sent + 1, of=REWAKE_MAX)
        return RewakeCall(*base, WAIT, round_id, f"עוד לא הגיע הזמן להערה {sent + 1}: {age:.0f}ש' מתוך {(sent + 1) * REWAKE_S}ש'",
                          tuple(evidence), n=sent + 1, of=REWAKE_MAX)
    limit = REWAKE_MAX * REWAKE_S + REWAKE_BOOT_S
    if age >= limit:
        return RewakeCall(*base, FAIL, round_id,
                          f"עברו {age:.0f}ש' {origin} (התקרה {limit}ש': {REWAKE_MAX} הערות ועוד {REWAKE_BOOT_S}ש' לאתחול): "
                          "הסבב נכשל בשם המחשבים שלא חזרו", tuple(evidence), of=REWAKE_MAX)
    return RewakeCall(*base, WAIT, round_id, f"{age:.0f}ש' מתוך {limit}ש': ממתינים", tuple(evidence), of=REWAKE_MAX)


def decide_rewake(conn, *, round_id: str, sent: int, age_s: float, since_last, quiet: int, waiting: int,
                  silent_for_s: float | None = None) -> RewakeCall | None:
    """S24. ``None`` = המוח נכשל (או שהעובדות אינן נקראות): אין החלטה, והשרת אינו שולח WoL, אינו מכשיל ואינו מכייל. לעולם אינו זורק."""
    try:
        return judge_rewake(round_id=round_id, sent=sent, age_s=age_s, since_last=since_last, quiet=quiet,
                            waiting=waiting, silent_for_s=silent_for_s)
    except Exception as exc:                    # noqa: BLE001 — לא ידוע אינו "תקין", וגם אינו הכשלה
        log.exception("brain: %s of %s failed", RULE_REWAKE, round_id)
        undecided(conn, RULE_REWAKE, POINT_REWAKE, "REWAKE", round_id, f"{type(exc).__name__}: {exc}")
        return None


# --- S30: משיכה או משימה שקטה ----------------------------------------------------------------------------------------------


def pull_silent_s(lost_s: float) -> float:
    """התקרה של משיכה: 600, או `lost_wait_seconds` ועוד השוליים כשזה יותר (#3038). הקלט הוא ההגדרה, והמוח קובע ממנה."""
    return max(SILENT_S, lost_s + LOST_MARGIN_S)


def judge_pull(*, members, created_at: object, now: datetime, final, silent_s: float) -> Call:
    """טהור. האם משיכה שעוד `running` נכשלה: ``FAIL_REPORTED`` (כל החברים במצב סופי ולא כולם `done`: המכונה **אמרה** שנכשלה),
    ``FAIL_SILENT`` (המכונה **שתקה** מעל ``silent_s`` מאז הדיווח האחרון שלה), ``KEEP``, או ``UNMEASURED``.

    `updated_at` זז על כל דיווח שאינו סופי, ולכן כאן הוא בדיוק הראיה (בניגוד לקיפאון בייטים). חבר שעוד לא דיווח נמדד מפתיחת
    המשיכה. חותמת שאינה נקראת, או בלי אזור זמן (שורה ישנה או ידנית; naive מול aware זורק TypeError), אינה "שתיקה": לא נמדד, ולא
    מכריזים (אותה הכרעה כמו K-LOST). הצלחה (כל החברים `done`) אינה כאן: היא ראיה חיובית אחרת, והשרת סוגר לפיה."""
    base = (RULE_SILENT, POINT_SILENT, "SILENT_PULL")
    mac = members[0]["mac"] if members else ""
    if members and all(m["state"] in final for m in members):
        errors = "; ".join(m["error"] for m in members if m["error"])
        return Call(*base, FAIL_REPORTED, mac, errors or "המכונה דיווחה " + ", ".join(m["state"] for m in members),
                    tuple(f"state={m['state']}" for m in members))
    stamps = [_aware(m["updated_at"]) for m in members] or [_aware(created_at)]
    read = [s for s in stamps if s is not None]
    if len(read) != len(stamps):
        return Call(*base, UNMEASURED, mac, "חותמת הדיווח אינה נקראת (או בלי אזור זמן): לא נמדד, ולא מכריזים שקט")
    last = max(read)
    age = (now - last).total_seconds()
    evidence = (f"last={last.isoformat(timespec='seconds')}", f"age={age:.0f}s", f"limit={silent_s:g}s")
    if age <= silent_s:
        return Call(*base, KEEP, mac, f"דיווח לפני {age:.0f}ש' (התקרה {silent_s:g}ש')", evidence)
    return Call(*base, FAIL_SILENT, mac, f"אין דיווח מהמכונה מאז {last.astimezone().strftime('%H:%M')}", evidence)


def decide_pull(conn, *, pull_id: str, members, created_at: object, now: datetime, final,
                silent_s: float) -> Call | None:
    """S30 (משיכה). ``None`` = המוח נכשל: אין החלטה, והמשיכה אינה נכשלת. לעולם אינו זורק."""
    try:
        return judge_pull(members=members, created_at=created_at, now=now, final=final, silent_s=silent_s)
    except Exception as exc:                    # noqa: BLE001 — לא ידוע אינו "שתק"
        log.exception("brain: %s of the pull %s failed", RULE_SILENT, pull_id)
        undecided(conn, RULE_SILENT, POINT_SILENT, "SILENT_PULL", pull_id, f"{type(exc).__name__}: {exc}")
        return None


def silent_task_grace(up_for_s: float) -> bool:
    """שקט של השרת אינו שקט של המכונה: שרת שהיה למטה עשר דקות (עדכון, אתחול) לא שמע איש, וקליטה או מקור חיים ידווחו שוב שניות אחרי
    שהוא עונה. עד ``SILENT_S`` מעליית השרת (בשעון שאינו קופץ) שום משימה אינה נסגרת. ``True`` = לא סוגרים עכשיו כלום. נשאל בכל
    מעבר, ולכן אינו נרשם. נכשל = ``True`` (לא סוגרים)."""
    try:
        return up_for_s <= SILENT_S
    except Exception:                           # noqa: BLE001 — לא נקרא כמה זמן השרת למעלה: לא סוגרים
        log.exception("brain: %s grace failed", RULE_SILENT)
        return True


def judge_task(*, task_id: str, mac: str, task_type: str, last: datetime | None, now: datetime) -> Call:
    """טהור. האם משימה `running` שקטה: ``FAIL_SILENT`` (אין דיווח ואין בייטים חדשים מעל ``SILENT_S``), ``KEEP``, או ``UNMEASURED``
    (`last` ריק: אי אפשר לדעת מתי המכונה הראתה חיים, למשל תיקיית ההכנה אינה נקראת, ואין מכריזים שקט). `last` הוא הרגע האחרון
    שהמכונה הראתה חיים: הדיווח האחרון, ובקליטה גם הבייט האחרון שהועלה (נאסף בשרת, לא כאן)."""
    base = (RULE_SILENT, POINT_SILENT, "SILENT_TASK")
    if last is None:
        return Call(*base, UNMEASURED, mac, "הרגע האחרון שבו המכונה הראתה חיים אינו נקרא: לא נמדד, ולא מכריזים שקט")
    age = (now - last).total_seconds()
    evidence = (f"task={task_id}", f"type={task_type}", f"last={last.isoformat(timespec='seconds')}",
                f"age={age:.0f}s", f"limit={SILENT_S}s")
    if age <= SILENT_S:
        return Call(*base, KEEP, mac, f"חיים לפני {age:.0f}ש' (התקרה {SILENT_S}ש')", evidence)
    return Call(*base, FAIL_SILENT, mac,
                f"המכונה הפסיקה לדווח — אין דיווח ואין בייטים חדשים מאז {last.astimezone().strftime('%H:%M')}"
                f" (מעל {SILENT_S // 60} דקות)", evidence)


def decide_task(conn, *, task_id: str, mac: str, task_type: str, last: datetime | None, now: datetime) -> Call | None:
    """S30 (משימה). ``None`` = המוח נכשל: אין החלטה, והמשימה אינה נסגרת. לעולם אינו זורק."""
    try:
        return judge_task(task_id=task_id, mac=mac, task_type=task_type, last=last, now=now)
    except Exception as exc:                    # noqa: BLE001 — לא ידוע אינו "שתק"
        log.exception("brain: %s of the task %s failed", RULE_SILENT, task_id)
        undecided(conn, RULE_SILENT, POINT_SILENT, "SILENT_TASK", task_id, f"{type(exc).__name__}: {exc}")
        return None


def _aware(raw: object) -> datetime | None:
    """חותמת ISO **עם אזור זמן**, או ``None``. חותמת בלי אזור אינה נקראת: naive מול aware זורק `TypeError`, והוא היה מפיל את
    `/overview` ואת `open_pull` עצמם (`now_iso` כותב תמיד עם אזור)."""
    got = _stamp(raw)
    return got if got is not None and got.tzinfo is not None else None


# --- S23: הזרם נעצר ולא הכונן -------------------------------------------------------------------------------------


def _held_s(d: dict) -> float | None:
    """כמה שניות מגירה עומדת: הגדול בין מה שהשרת מדד (`stalled_s`) למה שהסוכן אמר (`stall_s`). שניהם חסרים = ``None``."""
    vals = [v for v in (d.get("stalled_s"), d.get("stall_s")) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return max(vals) if vals else None


def judge_stall(machines, round_id: str = "", *, stall_s: float = STALL_S, stream_ended: bool = False) -> Call:
    """טהור. האם **כל** המגירות הכותבות עצרו יחד, כלומר הזרם ולא הכונן.

    `fanout` רואה מגירה אחת, ולכן כשהיא נעצרת הוא מסיק `drive too slow` ומאשים אותה. בסבב 08/09 נמדד שארבע המגירות
    הבריאות קפאו באותן דגימות בדיוק (81%-100% חפיפה), כולל שתי מגירות במכונה פיזית אחרת: מקור משותף פירושו סיבה
    משותפת, ותווית "כונן תקוע" על עצירת זרם שולחת אדם להחליף חומרה תקינה.

    פחות משתי מגירות כותבות = אין רוב, ואין מה להסיק (``NOT_MEASURED``, עיקרון 5): מגירה בודדת שעצרה היא **המגירה**.
    `stalled_s` ריק הוא "לא נמדד", ומגירה שלא נמדדה אינה יכולה להשתתף בהכרעה שכולן עצרו.

    #3723: ``stream_ended`` = השידור של הגל כבר הסתיים (`send_done`: הבלוקים והתיקונים נשלחו). אחריו אין זרם שיכול
    "לעצור": המגירות רק מסיימות לכתוב לדיסק ולאמת (דיסק איטי משלים ברקע דקות), הן לא זזות מול הזרם ולכן "כולן עצרו
    יחד". זה מצב שלישי, ``STREAM_ENDED`` ("לא רלוונטי"), ולא ``NOT_STALLED``: לא אמרנו שהזרם זז. הדגל הוא ראיה חיובית
    (שורת `send_done` ביומן); שידור שלא ידוע שהסתיים נשפט בדיוק כמו קודם, כך שעצירה אמיתית באמצע שידור לא מוסתרת."""
    base = (RULE_STALL, POINT_STALL, "STREAM_STALL")
    if stream_ended:
        return Call(*base, STREAM_ENDED, round_id,
                    "השידור הסתיים (send_done): המגירות רק מסיימות לכתוב, אין זרם שיכול לעצור",
                    ("stream_ended=1",))
    writing = [d for m in machines for d in m.get("drawer_list", []) if d.get("state") == "writing"]
    # #3896: המקור נקבע לפי מה שהסוכן ראה (`stall`, stall.sh), לא לפי "כולן עצרו". `waiting_disk` = הזרם המשיך להגיע ורק המגירה הזו לא
    # כתבה: הכונן. זה פסק גם למגירה בודדת וגם כשהשלמה רצה ב-HTTP בלבד (אין רוב, אבל יש ראיה ישירה).
    frozen = [h for d in writing if d.get("stall") == "waiting_disk" and (h := _held_s(d)) is not None and h >= stall_s]
    if frozen:
        longest = max(frozen)
        return Call(*base, DISK_STALLED, round_id,
                    f"{len(frozen)} מתוך {len(writing)} המגירות הכותבות עומדות בגלל הכונן שלהן (הזרם המשיך להגיע, עד {longest:.0f}ש'): "
                    "הכונן קפא, לא הזרם",
                    (f"writing={len(writing)}", f"waiting_disk={len(frozen)}", f"max_held_s={longest:.0f}", f"limit={stall_s:g}s",
                     "cause=disk"))
    if writing and all(isinstance(d.get("rejoin"), dict) and d["rejoin"].get("step") == "http" for d in writing):
        return Call(*base, NO_STREAM, round_id,
                    f"כל {len(writing)} המגירות הכותבות משלימות ב-HTTP: אין שידור שיכול לעצור, ושתיקתן אינה ראיה לזרם",
                    (f"writing={len(writing)}", "completion=http", "cause=not_stream"))
    if len(writing) < 2:
        return Call(*base, NOT_MEASURED, round_id, f"{len(writing)} מגירות כותבות: אין רוב, ואין מה להסיק על הזרם",
                    (f"writing={len(writing)}",))
    if any(d.get("stalled_s") is None for d in writing):
        return Call(*base, NOT_MEASURED, round_id, "מגירה כותבת שלא נמדדה: אינה יכולה להשתתף בהכרעה שכולן עצרו",
                    (f"writing={len(writing)}", "unmeasured=1"))
    stalled = [d["stalled_s"] for d in writing]
    if all(s >= stall_s for s in stalled):
        return Call(*base, STREAM_STALLED, round_id,
                    f"כל {len(writing)} המגירות הכותבות עצרו יחד (לפחות {min(stalled):.0f}ש', הסף {stall_s:g}ש'):"
                    " הזרם נעצר ולא הכונן",
                    (f"writing={len(writing)}", f"min_stalled_s={min(stalled):.0f}", f"limit={stall_s:g}s"))
    return Call(*base, NOT_STALLED, round_id,
                f"לא כל המגירות עצרו ({sum(s >= stall_s for s in stalled)} מתוך {len(writing)} מעל {stall_s:g}ש')",
                (f"writing={len(writing)}", f"limit={stall_s:g}s"))


_stalled: dict[str, tuple[str, int | None]] = {}   # גל -> (פסק העצירה הפתוחה, מספר השורה; None: לא נרשמה)
_stall_lock = threading.Lock()
STALL_ROUNDS = 16


def stream_sent_done(conn, wave_id: str | None) -> bool:
    """#3723: האם השידור של הגל הזה הסתיים -- יש שורת `send_done` ביומן על הגל (המנוע רושם
    אותה עם `round_id` = מזהה הגל). **ראיה חיובית בלבד**: אין גל, אין חיבור, או שהקריאה נכשלה = ``False`` ("לא ידוע
    שהסתיים"), והתווית נשפטת כמו קודם: הכשל כאן לא מסתיר עצירה אמיתית. נקרא רק כשכל המגירות כבר עצורות (לא בכל דופק של המסך)."""
    if conn is None or not wave_id:
        return False
    try:
        return conn.execute("SELECT 1 FROM journal WHERE event = 'send_done' AND round_id = ? LIMIT 1",
                            (wave_id,)).fetchone() is not None
    except Exception:                           # noqa: BLE001 -- אי אפשר לדעת = לא ידוע שהסתיים
        log.exception("brain: %s could not read send_done of the wave %s", RULE_STALL, wave_id)
        return False


def stream_stalled(conn, machines, round_id: str | None, wave_id: str | None = None) -> bool:
    """S23: התווית שמסך החדר מציג. נרשמת רק **כניסה** לעצירה (פעם אחת), והתוצאה נסגרת כשהיא נגמרת: המסך נקרא בלי הרף,
    ושורה לכל קריאה הייתה מציפה. בלי conn או בלי סבב (בדיקות, מסך ריק) מחליטים בלי לרשום. מוח שנפל = ``False``.

    #3723: ``wave_id`` = הגל שהסבב משדר עכשיו. כשכל המגירות עצורות והשידור שלו כבר הסתיים (`send_done`), הפסק הוא
    ``STREAM_ENDED`` ולא ``STREAM_STALLED``; עצירה שנפתחה לפני כן נסגרת בתוצאה "השידור הסתיים"."""
    try:
        call = judge_stall(machines, round_id or "")
        if call.verdict == STREAM_STALLED and stream_sent_done(conn, wave_id):
            call = judge_stall(machines, round_id or "", stream_ended=True)
    except Exception as exc:                    # noqa: BLE001 — תווית אינה מפילה את המסך
        log.exception("brain: %s failed", RULE_STALL)
        if conn is not None and round_id:
            undecided(conn, RULE_STALL, POINT_STALL, "STREAM_STALL", round_id, f"{type(exc).__name__}: {exc}")
        return False
    if conn is None or not round_id:
        return call.verdict == STREAM_STALLED
    closing = None
    standing = call.verdict in (STREAM_STALLED, DISK_STALLED)
    with _stall_lock:
        was = _stalled.get(round_id)
        # #3896: עצירה פתוחה נסגרת כשהפסק משתנה (זרם <-> כונן, או כלום), ופסק חדש נרשם כעצירה חדשה
        entering = standing and (was is None or was[0] != call.verdict)
        if was is not None and (not standing or was[0] != call.verdict):
            closing = was[1]
            del _stalled[round_id]
        if entering:
            _stalled[round_id] = (call.verdict, None)
            for old in list(_stalled)[:max(len(_stalled) - STALL_ROUNDS, 0)]:
                del _stalled[old]               # סבב שנגמר בעצירה: הרשם אינו גדל לנצח
    if was is not None and (not standing or was[0] != call.verdict):
        settle(conn, closing, "הזרם חזר לזוז" if call.verdict == NOT_STALLED
               else "השידור הסתיים (send_done): אין זרם, המגירות רק מסיימות לכתוב" if call.verdict == STREAM_ENDED
               else "הפסק התחלף: העצירה אינה של הזרם עוד" if standing
               else "ההשלמה ב-HTTP: אין שידור שיכול לעצור" if call.verdict == NO_STREAM
               else "העצירה אינה נמדדת עוד (פחות משתי מגירות כותבות, או מגירה שלא נמדדה)")
    if entering:
        row = record(conn, [call], round_id=round_id, entity_kind="round")
        with _stall_lock:
            if round_id in _stalled:
                _stalled[round_id] = (call.verdict, row.get(call.target))
    return call.verdict == STREAM_STALLED
