"""#3064 (B61): דיסק שנכשל באותה סיבה כמה גלים ברצף -- **המלצה בצל בלבד**, ושום דבר לא זז.

**למה.** ברזל, 15/11, שלושה סבבים על אותו APS: K9 רשם שלוש פעמים ``UNCLASSIFIED``, ואיש לא חיבר אותן. כל שורה נכונה, והדפוס
(אותו דיסק, אותה סיבה, שוב ושוב) הוא מה שמפעיל צריך לשמוע: "זה לא מקרה, החלף או בדוק".

**הכלל.** אחרי כל שורת K9 על דיסק (`cause.judge`), הכלל קורא את הזיכרון של הדיסק (`memory.decisions`, ``kind="disk"``,
כלל K9) ואת ההשתתפות שלו בגלים (``port_history``), ומחשב את **הרצף**: כמה גלים אחרונים, ברצף, נכשל הדיסק באותה סיבה.
``Config.fail_repeats`` גלים ומעלה (3) = שורה אחת, ``B61 / REPEAT_FAIL``, עם המלצה בטקסט.

- **סיבת הגל** = הספציפית ביותר שנרשמה בו (`ORDER`): ``LINK`` לפני ``DRIVE`` לפני ``NETWORK`` לפני ``SOFTWARE``, ו-
  ``UNCLASSIFIED`` רק כשאין אחרת. אותו גל ש-D11 רשם בו "לא סווג" ו-`disk_failure` רשם בו ``DRIVE`` הוא ``DRIVE``.
- **רצף נשבר** בגל שהדיסק השתתף בו והצליח (``port_history.failed = 0``), בגל שנכשל בלי שורת K9 (``failed = 1``), בגל שלא נרשם
  לו אם נכשל (``failed`` NULL: "לא נקרא" אינו "נכשל באותה סיבה"), ובגל שנכשל בסיבה אחרת. גל שהדיסק לא היה בו אינו קיים
  לדיסק: הרצף נמדד על הגלים **שלו**.
- **גל, לא סבב חדר.** אין טבלה שמקשרת גלים לסבב חדר (``room_rounds.wave_session_id`` מחזיק רק את הנוכחי), ולכן גל 1 וגל 2
  של אותו סבב נספרים כשניים. זה ידוע ולא אומת מול הפסקה של סבב אמיתי.

**הסף אינו הגדרה.** נדב, 14/11 בערב (`veto.py`, ראש המודול): "הספים בהגדרות כנראה מפריעים למוח". `fail_repeats` הוא ערך התחלה
בקוד (`decide.Config`), והוא **הערכה, לא מדידה**: שלושה = כמו `min_returns` ו-`learn.models.MIN_SAMPLES`, ואין לו מקור אחר.

**בצל בלבד.** הכלל רושם ל-``brain_decisions`` ואינו משנה צבע, כשל או פעולה (כמו K9). שורה אחת לכל (דיסק, גל, סיבה). לעולם אינו
זורק לתוך K9.
"""

from __future__ import annotations

import logging

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, RULE_K9 = "B61", "REPEAT_FAIL", "K9"
SHADOW = "shadow"
#: מהספציפית לכללית: אותו גל שבו נרשמו כמה סיבות הוא הראשונה מהן.
ORDER = ("LINK", "DRIVE", "NETWORK", "SOFTWARE", "UNCLASSIFIED")
#: כמה שורות K9 ושורות השתתפות נקראות לדיסק: מעבר לזה הרצף כבר ארוך בהרבה מכל סף.
LIMIT = 200
#: ההמלצה לכל סיבה. אף אחת מהן אינה פעולה: מפעיל קורא, ואיש לא מבצע בשמו.
ADVICE = {
    "DRIVE": "החלף את הדיסק או בדוק אותו (SMART, ומגירה אחרת)",
    "LINK": "בדוק את הכבל או את המגירה, לא את הדיסק",
    "NETWORK": "הסיבה היא הרשת, לא הדיסק: בדוק את הקישור של המשכפל",
    "SOFTWARE": "הסיבה היא הזרם או התוכנה, לא הדיסק: בדוק את הגל ואת האימג'",
    "UNCLASSIFIED": "אין ראיה לסיבה: בדוק את הדיסק ואת המגירה (SMART, והחלף מגירה) לפני שמחליפים",
}
OUTCOME = "צל: המלצה בלבד, שום דבר לא זז"


def _specific(causes) -> str | None:
    got = {c for c in causes if c in ORDER}
    return next((c for c in ORDER if c in got), None)


def waves(conn, serial: str) -> list[tuple[str, str | None]]:
    """הגלים של הדיסק, החדש ראשון: ``(גל, סיבה)``. ``None`` = השתתף ולא נכשל בסיבה ידועה (הצליח, או לא נרשם, או נכשל בלי K9).
    גל שאין לו לא שורת K9 ולא שורת השתתפות אינו כאן."""
    causes: dict[str, list[str]] = {}
    first: dict[str, tuple[str, int]] = {}      # (הזמן, מספר השורה): שני גלים באותה שנייה נשארים בסדר שנכתבו
    for row in memory.decisions(conn, kind="disk", key=serial, rule=RULE_K9, limit=LIMIT):
        rid = row.get("round_id")
        if rid:
            causes.setdefault(rid, []).append(row["verdict"])
            first[rid] = min(first.get(rid, (row["at"], row["id"])), (row["at"], row["id"]))
    for row in conn.execute("SELECT session_id, at FROM port_history WHERE serial = ? ORDER BY at DESC LIMIT ?",
                            (serial, LIMIT)):
        first[row["session_id"]] = min(first.get(row["session_id"], (row["at"], 0)), (row["at"], 0))
    order = sorted(first, key=lambda r: first[r], reverse=True)
    return [(r, _specific(causes.get(r, ()))) for r in order]


def streak(history: list[tuple[str, str | None]]) -> tuple[str | None, list[str]]:
    """``(הסיבה של הגל החדש, הגלים ברצף)``. החדש ראשון. בלי סיבה בגל החדש -- אין רצף."""
    if not history or history[0][1] is None:
        return None, []
    cause, run = history[0][1], []
    for wave, got in history:
        if got != cause:
            break
        run.append(wave)
    return cause, run


def observe(conn, serial: str | None, mac: str, round_id: str | None, *, repeats: int | None = None) -> int | None:
    """אחרי שורת K9 של דיסק: אם הרצף הגיע לסף, שורה אחת. מחזיר את מספר השורה, או ``None``. לעולם אינו זורק."""
    try:
        if not serial or not round_id:
            return None
        if repeats is None:
            from server.brain import veto                     # noqa: PLC0415 -- הסף הוא `Config`, ו-veto קורא את K9
            repeats = veto.config(conn).fail_repeats
        history = waves(conn, serial)
        at = next((i for i, (w, _) in enumerate(history) if w == round_id), None)
        if at is None:
            return None                                       # השורה של K9 לא נכתבה: אין על מה לבנות
        history = history[at:]                                # הגל הזה ואחריו הישנים (דיווח מאוחר של גל חדש יותר אינו חלק)
        cause, run = streak(history)
        if cause is None or len(run) < repeats:
            return None
        for row in memory.decisions(conn, kind="disk", key=serial, rule=RULE, round_id=round_id, limit=20):
            if f"cause={cause}" in (row.get("evidence") or ()):
                return None                                   # כבר נרשם לגל הזה ולסיבה הזו
        advice = ADVICE[cause]
        return memory.record(
            conn, mode=SHADOW, rule=RULE, action=ACTION, verdict=f"REPEAT_{cause}", round_id=round_id,
            reason=f"הדיסק נכשל ב-{len(run)} הגלים האחרונים שלו באותה סיבה ({cause}): {advice}",
            evidence=(f"mac={mac}", f"serial={serial}", f"cause={cause}", f"waves={len(run)}", f"fail_repeats={repeats}",
                      *(f"wave={w}" for w in run[:10])),
            entity=("disk", serial), outcome=OUTCOME)
    except Exception:                                         # noqa: BLE001 -- המוח אינו מפיל את K9
        log.exception("brain: the repeated-failure rule of %s in %s failed", serial, round_id)
        return None
