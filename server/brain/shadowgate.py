"""#3563: שער המעבר מצל לאכיפה -- **כלי מדידה בלבד**. לכל כלל בצל: כמה דגימות, כמה הסכמה, והאם עומד בשער.

**המוח מציע, לא פוסל** (הכרעת נדב 16/11, ו-18/11: "כל ההחלטות למוח"). לכן הכלי הזה **אינו מעביר שום כלל**:
``READY`` אומר "מוכן ל-canary" והוא המלצה. המעבר ל-enforce נשאר החלטה של אדם; אין כאן כתיבה, מתג או מעבר אוטומטי.

**ההסכמה היא ``matched`` ב-``brain_decisions``** (``memory.resolve``; ההגדרה לכל כלל ב-`forecast.py`): החלטה שנסגרה
בתוצאה שנמדדה, מול מה שהיא חזתה. ``matched = NULL`` -- "לא נמדד", כולל החלטה בלי ``outcome`` (#3750) -- **אינה הסכמה**:
היא לא נספרת לא במונה ולא במכנה, ונספרת בנפרד (``unmeasured``). כלל שאין לו שורה אחת שנמדדה אינו עובר את השער ואינו
"נכשל" בו: ``NOT_MEASURED``, כי "לא נמדד" ו"נמדד והסכים" הם שני מצבים שונים (עיקרון 5).

**שני ספים בהגדרות** (לא קבועים בקוד; ערך התחלה כשלא נקבע או שהשמור פגום):
``brain_gate_min_samples`` ו-``brain_gate_min_agreement`` (אחוזים). ⚠️ **ערכי ההתחלה הם הערכה ולא נמדדו** (עיקרון 5ב):
הם נבחרו כדי שהכלי יענה משהו, ונדב מכריע מה הסף בפועל. דגימה = אירוע בלתי תלוי (גל), לא שורה (``memory.rule_tally``).

**מה שאין כאן:** שלב canary, rollback, וה-replay על ההיסטוריה (#3563). **ולא נבדק כאן**, כי הוא לא ב-`brain_decisions`:
בלי הרעה בזמן גל ובשיעור כשל, וביצועי הכלל לפי שכבות חומרה. כלל שעומד בשער הזה **לא הוכח בטוח** -- רק שהתחזית שלו
תאמה את המציאות מספיק פעמים.
"""

from __future__ import annotations

import logging

from server.brain import memory
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")

SAMPLES_SETTING = "brain_gate_min_samples"
AGREEMENT_SETTING = "brain_gate_min_agreement"
#: ⚠️ הערכה, לא מדידה. ראו את תיאור המודול.
SAMPLES_DEFAULT, SAMPLES_RANGE = 30, (1, 100_000)
AGREEMENT_DEFAULT, AGREEMENT_RANGE = 90, (1, 100)
KEYS = (SAMPLES_SETTING, AGREEMENT_SETTING)
LABELS_HE = {SAMPLES_SETTING: "המוח: שער מצל לאכיפה, מינימום דגימות",
             AGREEMENT_SETTING: "המוח: שער מצל לאכיפה, מינימום הסכמה (%)"}

#: מצבי הכלל. ``ENFORCE`` = כבר אוכף (השער אינו חל). ``READY`` = מוכן ל-canary (המלצה בלבד).
ENFORCE, READY, NOT_READY, NOT_MEASURED = "ENFORCE", "READY", "NOT_READY", "NOT_MEASURED"


def parse(raw: object, bounds: tuple[int, int]) -> int | None:
    """שלם בטווח, אחרת ``None`` (הקונסולה דוחה ב-400). ``bool`` אינו מספר, ואפס מוביל וטקסט עשרוני נדחים."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and not raw.startswith("0") and len(raw) <= 6:
        value = int(raw)
    else:
        return None
    return value if bounds[0] <= value <= bounds[1] else None


def _stored(conn, key: str, default: int, bounds: tuple[int, int]) -> int:
    try:
        got = parse(get_setting(conn, key), bounds)
    except Exception:                           # noqa: BLE001 -- הגדרה שלא נקראה היא ערך ההתחלה, לא קריסה
        log.exception("brain: %s unreadable, using %s", key, default)
        return default
    return default if got is None else got


def thresholds(conn) -> dict:
    return {"min_samples": _stored(conn, SAMPLES_SETTING, SAMPLES_DEFAULT, SAMPLES_RANGE),
            "min_agreement_pct": _stored(conn, AGREEMENT_SETTING, AGREEMENT_DEFAULT, AGREEMENT_RANGE)}


def effective(conn) -> dict[str, str]:
    """הערכים שבתוקף, לקונסולה: ערך ההתחלה כשלא נקבע -- לא "לא נקבע"."""
    t = thresholds(conn)
    return {SAMPLES_SETTING: str(t["min_samples"]), AGREEMENT_SETTING: str(t["min_agreement_pct"])}


def judge(tally: dict, min_samples: int, min_agreement_pct: int) -> dict:
    """שורת כלל אחת. ההשוואה בשלמים (``agreed * 100 >= pct * measured``): בלי עיגול ששובר סף."""
    measured, agreed, samples = tally["measured"] or 0, tally["agreed"] or 0, tally["samples"] or 0
    agreement = round(100 * agreed / measured, 1) if measured else None
    reasons = []
    if tally["mode"] == "enforce":
        state = ENFORCE
    elif measured == 0:
        state, reasons = NOT_MEASURED, ["no_measured_decisions"]
    else:
        if samples < min_samples:
            reasons.append("few_samples")
        if agreed * 100 < min_agreement_pct * measured:
            reasons.append("low_agreement")
        state = NOT_READY if reasons else READY
    return {"rule": tally["rule"], "mode": tally["mode"], "state": state, "reasons": reasons,
            "shadow_rows": tally["shadow_rows"] or 0, "measured": measured, "unmeasured": tally["unmeasured"] or 0,
            "samples": samples, "agreement_pct": agreement, "last_at": tally["last_at"]}


def report(conn) -> dict:
    """הדוח לכל הכללים. קריאה בלבד: אין כתיבה, ואין מעבר. ``ready_for_canary`` = רשימת ההמלצות, והמעבר החלטה של אדם."""
    t = thresholds(conn)
    rules = [judge(r, t["min_samples"], t["min_agreement_pct"]) for r in memory.rule_tally(conn)]
    return {"thresholds": t, "rules": rules, "ready_for_canary": [r["rule"] for r in rules if r["state"] == READY]}


def create_console_router(ctx):
    from fastapi import APIRouter, Depends                 # noqa: PLC0415 -- ייבוא עצל, כמו `ask_api`
    from fastapi.concurrency import run_in_threadpool      # noqa: PLC0415
    from server.console import auth                        # noqa: PLC0415
    router = APIRouter(prefix="/api/console/brain")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/shadow-gate")
    async def shadow_gate(user=Depends(admin_only)):
        """לכל כלל: דגימות, הסכמה ומצב. קריאה בלבד -- המעבר ל-enforce אינו כאן."""
        return await run_in_threadpool(report, ctx.conn)

    return router
