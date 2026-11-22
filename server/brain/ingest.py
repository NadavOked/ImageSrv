"""#3669 (B6, B7, B8; אפיק #3653): קליטה והפצה ישירה -- רמת הדחיסה, מקום בשרת לקליטה, והמתנה לשרת ששותק. **בצל.**

שלוש החלטות קטנות שהיום יושבות בקוד שמבצע אותן: בסוכן הבנייה (`capture.sh`, `directsend.sh`, `directchunk.sh`) ובשרת
(`library/capture.py`). המוח מחזיק אותן כאן, **באותם ערכים בדיוק** (אין סף חדש ואין סף שהשתנה). הקוד הקיים מחליט, והמוח רושם שורה
ב-``brain_decisions`` (``mode=shadow``, ``outcome`` = "צל") עם מה שהיה מחליט ו-``agree=`` מול מה שהקוד הקיים עשה. ``brain_veto_mode=off`` =
לא נקרא ולא נרשם. מעבר ל-enforce הוא אחרי הוכחה על ברזל, ובשער #3563.

- **B6, רמת הדחיסה** (``B3669a``): zstd רמה 3, ``-T0`` (כל הליבות). הסוכן מחיל ``CAPTURE_LEVEL`` ו-``CAPTURE_THREADS`` ומדווח את הרמה במניפסט
  (``compression: zstd-N``), ולכן ההשוואה היא בקבלת המניפסט, בקליטה (``point=capture``) ובהפצה ישירה (``point=direct``). ``agree=unknown``
  כשהמניפסט לא דיווח רמה: "לא דיווח" אינו "אותה רמה" (עיקרון 5).
- **B7, מקום בשרת לקליטה** (``B3669b``): ``space`` הוא הפסק של `library/capture.py` (`_preflight_body`), על אותם קלטים: מה שהקליטה תתפוס, הפנוי
  שנמדד, הרזרבה למערכת (`disk_reserve.reserve`) ומה שלא נקרא. ``ok`` / ``warning`` / ``refused`` / ``unchecked`` הם ``PROCEED`` / ``WARN`` /
  ``BLOCK`` / ``UNDECIDED``; **מה שלא נקרא אינו ``ok``**. הסיבה היא של הקוד הקיים, מילה במילה. הגדרת ``MIN_FREE_GB`` נשארת למשתמש.
- **B8, שרת ששותק בהפצה ישירה** (``B3669c``): ``MISS_LIMIT`` שאלות שנכשלו ברצף, אחת ל-``POLL_S`` שניות, והמקור נכשל; ו-``EVICT_MARGIN_S``
  מעל התקרות של הזרם (``evict_ms``). **הערכים נשארים בסוכן** (`directsend.sh` `_dw_miss`, `directchunk.sh` `_cr_miss`, ``DIRECT_POLL_S``,
  ``_do_evict``/``_dp_evict``), וטסט מחזיק אותם שווים לערכים כאן. נרשמת שורה אחת לסבב ישיר, בפתיחתו.

**מה שאינו כאן:** אין נפילה שקטה לשולח הישן (קבוצה ג'; הוא הוסר ב-#3173), ו-``SILENCE_GRACE_S`` (שתיקת המקור בשרת, S32) -- סעיף נלווה שנשאר כמו שהוא.
**לעולם אינו זורק** אל הקליטה או אל פתיחת הסבב: מוח שנפל הוא שורת לוג.
"""

from __future__ import annotations

import logging
import re
from typing import Mapping, NamedTuple

from server.brain import memory, veto
from server.storage import disk_reserve

log = logging.getLogger("imagesrv.brain")

RULE_LEVEL, RULE_SPACE, RULE_SILENCE = "B3669a", "B3669b", "B3669c"
ACTION_LEVEL, ACTION_SPACE, ACTION_SILENCE = "CAPTURE_COMPRESSION", "CAPTURE_SPACE", "DIRECT_SILENCE"
SHADOW_HE = "צל: רק נרשם, הקוד הקיים החליט"
PROCEED, WARN, BLOCK, UNDECIDED, WAIT = "PROCEED", "WARN", "BLOCK", "UNDECIDED", "WAIT"

#: B6. רמה 9 עולה פי 3.1 בזמן ומחזירה 1.1% (#72, נמדד בשרת המעבדה); ``-T0`` = כל הליבות (הכרעת נדב 16/10). **הערך של הקוד היום**
#: (`capture.sh`: ``CAPTURE_LEVEL``, ``CAPTURE_THREADS``), לא הערכה חדשה.
LEVEL = 3
THREADS = 0
#: B8. הערכים של `directsend.sh` (`direct_wait_running`) ושל `directchunk.sh`: 30 שאלות שנכשלו ברצף, אחת ל-2 ש', ושוליים של 60 ש' מעל
#: תקרת ההתחלה ותקרת השקט של הזרם. **הערכים של הקוד היום**, לא מדידה.
MISS_LIMIT = 30
POLL_S = 2
EVICT_MARGIN_S = 60

_ZSTD = re.compile(r"zstd-(\d+)")


def compression() -> tuple[int, int]:
    """B6: ``(רמה, ליבות)``. אין עוד קלט: אין מדידה שמצדיקה אחר, ולכן ברירת המחדל הבטוחה היא הערך של היום."""
    return LEVEL, THREADS


def applied_level(manifest: object) -> int | None:
    """הרמה שהסוכן דיווח במניפסט (``compression: zstd-N``), או ``None`` כשלא דיווח או שהשדה אינו ``zstd-N`` (לא נמדד)."""
    raw = manifest.get("compression") if isinstance(manifest, Mapping) else None
    found = _ZSTD.fullmatch(raw) if isinstance(raw, str) else None
    return int(found.group(1)) if found else None


def silence_s() -> int:
    """B8: כמה שניות שרת שותק לפני שהמקור נכשל."""
    return MISS_LIMIT * POLL_S


def evict_ms(start_s: int, stall_s: int) -> int:
    """B8: תקרת הפינוי של ``fanout`` (מילישניות), מעל תקרת ההתחלה ותקרת השקט: ``(start + stall + 60) * 1000``."""
    return (start_s + stall_s + EVICT_MARGIN_S) * 1000


class Space(NamedTuple):
    status: str     # ok / warning / refused / unchecked -- המילים של `POST /api/v1/capture/<task>/preflight`
    reason: str


#: הפסק של המוח לכל סטטוס של השרת. מה שאינו ברשימה הוא ``UNDECIDED``.
SPACE_VERDICT = {"ok": PROCEED, "warning": WARN, "refused": BLOCK, "unchecked": UNDECIDED}


def space(*, used: int | None, free: int | None, keep: int, missing: list[str]) -> Space:
    """B7: אם הקליטה נכנסת. ``used`` = מה שהקליטה תתפוס (``None`` כשמחיצה לא דיווחה), ``free`` = הפנוי באחסון השרת (``None`` כשלא נמדד),
    ``keep`` = הרזרבה למערכת, ``missing`` = מספרי המחיצות שלא דיווחו נפח תפוס. הרזרבה אינה מקום לקליטה, גם לא באזהרה (#1737)."""
    unchecked = []
    if missing:
        unchecked.append("נפח תפוס במחיצות " + ", ".join(missing))
    elif used is None:
        unchecked.append("נפח תפוס")
    if free is None:
        unchecked.append("מקום פנוי באחסון השרת (loc_local)")
    if unchecked:
        return Space("unchecked", "לא נבדק: " + " · ".join(unchecked) + " — הקליטה נעצרה לפני הכיווץ")
    numbers = f"תפוס {used / 1e9:.1f} GB · פנוי בשרת {free / 1e9:.1f} GB"
    if keep:
        numbers += f" · נשמרים למערכת {disk_reserve.gb(keep)}"
    if used + keep > free:
        return Space("refused", "אין מקום בשרת לקליטה: " + numbers)
    if used * 2 + keep > free:
        return Space("warning", "אזהרת מקום: " + numbers + " — יחס הדחיסה אינו ידוע; ניתן להמשיך")
    return Space("ok", numbers)


def compression_shadow(conn, task_id: str, manifest: object, point: str) -> None:
    """B6: שורה אחת לקבלת מניפסט: הרמה שהמוח היה קובע מול מה שהסוכן דיווח שהחיל. לא משנה דבר, ולעולם אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        level, threads = compression()
        got = applied_level(manifest)
        raw = manifest.get("compression") if isinstance(manifest, Mapping) else None
        agree = "unknown" if got is None else str(int(got == level))
        shown = f"zstd-{got}" if got is not None else "unreported"
        memory.record(conn, mode="shadow", rule=RULE_LEVEL, point=point, action=ACTION_LEVEL, verdict=PROCEED,
                      reason=f"דחיסה zstd-{level}, כל הליבות: הסוכן דיווח {shown}",
                      evidence=[f"point={point}", f"task={task_id}", f"level={level}", f"threads={threads}",
                                f"applied={raw if isinstance(raw, str) else 'unreported'}", f"agree={agree}"],
                      outcome=SHADOW_HE)
        if agree == "0":
            log.warning("brain: the capture of %s reports %s, and the compression rule says zstd-%s", task_id, shown, level)
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל קליטה
        log.exception("brain: the compression of %s was not recorded", task_id)


def space_shadow(conn, task_id: str, legacy: tuple[str, str], *, used: int | None, free: int | None, keep: int,
                 missing: list[str]) -> Space | None:
    """B7: שורה אחת לבדיקת מקום: הפסק של המוח מאותם קלטים, מול ``(status, reason)`` שהקוד הקיים כבר קבע. לא משנה דבר, ולעולם אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF:
            return None
        brain = space(used=used, free=free, keep=keep, missing=missing)
        same = brain == Space(*legacy)
        memory.record(conn, mode="shadow", rule=RULE_SPACE, point="preflight", action=ACTION_SPACE,
                      verdict=SPACE_VERDICT.get(brain.status, UNDECIDED), reason=brain.reason,
                      evidence=[f"task={task_id}", f"status={brain.status}", f"used={'unread' if used is None else used}",
                                f"free={'unread' if free is None else free}", f"keep={keep}",
                                f"legacy={legacy[0]}", f"agree={int(same)}"],
                      outcome=SHADOW_HE)
        if not same:
            log.warning("brain: the capture space verdict %s of %s differs from the existing %s", brain.status, task_id, legacy[0])
        return brain
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל קליטה
        log.exception("brain: the capture space of %s was not recorded", task_id)
        return None


def silence_shadow(conn, round_id: str, task_id: str) -> None:
    """B8: שורה אחת לסבב ישיר, בפתיחתו: כמה זמן מחשב הבנייה ממתין לשרת ששותק לפני שהוא נכשל. הסוכן מחיל את הערכים שלו עד #3563."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        memory.record(conn, mode="shadow", rule=RULE_SILENCE, point="direct_open", action=ACTION_SILENCE, verdict=WAIT,
                      reason=f"שרת ששותק: מחשב הבנייה נכשל אחרי {MISS_LIMIT} שאלות שנכשלו ברצף, אחת ל-{POLL_S} ש' ({silence_s()} ש')",
                      evidence=[f"task={task_id}", f"misses={MISS_LIMIT}", f"poll_s={POLL_S}", f"silence_s={silence_s()}",
                                f"evict_margin_s={EVICT_MARGIN_S}"],
                      round_id=round_id, entity=("round", round_id), outcome=SHADOW_HE)
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל פתיחת סבב
        log.exception("brain: the direct-send silence of %s was not recorded", round_id)
