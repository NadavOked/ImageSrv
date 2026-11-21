"""#3668 (S50, T2; אפיק #3653): ההגדרה היחידה של "עסוק" -- מתי מותר להפעיל מחדש את השרת ולעדכן אותו. **בצל.**

**מה זה.** שלושה מקומות שואלים "אפשר להפעיל מחדש עכשיו?": האתחול המתוזמן (`auto_reboot.Scheduler`), "החל עדכון" (`update.restart_blocker`)
ו-`tools/server-upgrade.sh`. כולם קוראים לאותה פונקציה, `auto_reboot.busy_reason` (#1891). המוח מחזיק את **הפסק**: אילו חושים נשאלים,
באיזה סדר, ומתי התשובה "פנוי" -- והקוד שם רק קורא את החושים ופועל לפי התשובה.

**החושים** (הקריאה שלהם נשארת ב-`busy_reason`, וכל אחד מחזיר את הסיבה בעברית או ``None``), לפי הסדר:
``db`` (סבב, סבב חדר גם בין גלים, קליטה, העברה בין אתרים -- `busy_db.reason`), ``streams`` (קליטה, העלאת אימג' וקבלה משרת אחר --
`inflight.running`), ``update`` (עדכון שרת רץ; רק למי שאינו העדכון עצמו), ``vm`` (עדכון VM), ``local_copy`` (מחשב רשום שמדווח על שיכפול
מקומי). **הסדר הוא החלק של הכלל:** הראשון שאומר "עסוק" קובע את הסיבה, ושאר החושים כבר אינם נקראים.

**שלושה פסקים, ואין רביעי:**

- ``DEFER`` -- חוש אחד אמר "עסוק". הסיבה היא **של הקוד הקיים, מילה במילה**. לא מפעילים מחדש ולא מעדכנים.
- ``PROCEED`` -- **כל** החושים נקראו, וכל אחד אמר שאין מה שמונע. ראיה חיובית, לא "לא מצאנו".
- ``UNDECIDED`` -- חוש לא נקרא (זרק), או שלא כל החושים נקראו. **זה אינו ``PROCEED``** (עיקרון 5): אין החלטה = דחייה, כמו היום, שבו חוש
  שזרק מפיל את הבדיקה וכל קורא מסרב בשמו.

**צל.** הקוד הקיים הוא שמחליט, והמוח רושם מה היה מחליט מאותם חושים בדיוק (`shadow`): שורה ב-``brain_decisions`` לכל בדיקה, עם
``legacy=`` (מה שהקוד הקיים החזיר) ו-``agree=`` (האם הפסק זהה). **אי-הסכמה נרשמת בשמה ואינה משנה דבר.** ``off`` ב-`brain_veto_mode` = לא
נקרא ולא נרשם. מעבר ל-enforce הוא אחרי הוכחה על ברזל, ובשער #3563: אז `busy_reason` מחזיר את הפסק הזה, והשרשרת שלו נמחקת.

**לעולם אינו זורק** אל הבדיקה: מוח שנפל הוא שורת לוג, והשרת מחליט כמו בלעדיו.
"""

from __future__ import annotations

import logging
from typing import Mapping

from server.brain import memory, veto
from server.brain.gate_core import Reading

log = logging.getLogger("imagesrv.brain")

#: מזהה הפריט בקטלוג (S50 במיפוי של #3653). T2 (`server-upgrade.sh`) קורא את אותו פסק, ונרשם תחת ``RULE_UPGRADE``.
RULE = "B3668a"
RULE_UPGRADE = "B3668b"
ACTION = "RESTART_GATE"
DEFER, PROCEED, UNDECIDED = "DEFER", "PROCEED", "UNDECIDED"
#: החושים לפי סדר הבדיקה. הסדר הוא הכלל; ``update`` נשאל רק כש-``include_update``.
SENSORS = ("db", "streams", "update", "vm", "local_copy")
#: מי שקורא. ``server_upgrade`` רץ בתהליך משלו (T2); האחרים בתהליך השרת.
SITES = ("auto_reboot", "update", "server_upgrade")
SHADOW_HE = "צל: רק נרשם, הקוד הקיים החליט"


def decide(seen: Mapping[str, str | None], *, include_update: bool = True, error: str | None = None) -> Reading:
    """הפסק מתוך מה שהחושים אמרו. ``seen`` = לכל חוש שנקרא, הסיבה או ``None`` (פנוי); חוש שלא נקרא אינו במפתח.
    ``error`` = שם החריגה של החוש שזרק. טהור: לא קורא דבר."""
    wanted = [s for s in SENSORS if include_update or s != "update"]
    for name in wanted:
        if name not in seen:
            break
        if seen[name]:
            return Reading(DEFER, str(seen[name]), (f"sensor={name}",))
    if error is not None:
        return Reading(UNDECIDED, f"לא ניתן לבדוק אם השרת עסוק ({error})", (f"error={error}", *_trail(seen, wanted)))
    unread = [s for s in wanted if s not in seen]
    if unread:
        return Reading(UNDECIDED, "לא כל החושים נקראו: " + ", ".join(unread), _trail(seen, wanted))
    return Reading(PROCEED, f"פנוי: כל {len(wanted)} החושים נקראו ואין מה שמונע", _trail(seen, wanted))


def _trail(seen: Mapping[str, str | None], wanted: list[str]) -> tuple[str, ...]:
    return tuple(f"{s}={'free' if s in seen and not seen[s] else 'busy' if s in seen else 'unread'}" for s in wanted)


def agrees(reading: Reading, legacy: str | None, *, failed: bool) -> bool:
    """הפסק זהה למה שהקוד הקיים עשה: סיבה זהה (עסוק), ``None`` (פנוי), או חריגה (לא הוכרע)."""
    if failed:
        return reading.verdict == UNDECIDED
    if reading.verdict == DEFER:
        return reading.reason == legacy
    return reading.verdict == PROCEED and legacy is None


def shadow(conn, site: str, seen: Mapping[str, str | None], *, include_update: bool, legacy: str | None,
           error: BaseException | None = None) -> Reading | None:
    """שורת צל אחת לבדיקה: מה היה הפסק, ומה הקוד הקיים החזיר. לא משנה דבר, ולעולם אינו זורק. ``None`` = לא נקרא (`off`) או נכשל."""
    try:
        if veto.mode(conn) == veto.OFF:
            return None
        reading = decide(seen, include_update=include_update, error=None if error is None else type(error).__name__)
        same = agrees(reading, legacy, failed=error is not None)
        shown = "exception" if error is not None else "none" if legacy is None else legacy
        memory.record(conn, mode="shadow", rule=RULE_UPGRADE if site == "server_upgrade" else RULE, point=site, action=ACTION,
                      verdict=reading.verdict, reason=reading.reason,
                      evidence=[f"site={site}", f"include_update={int(include_update)}", *reading.evidence, f"legacy={shown}",
                                f"agree={int(same)}"],
                      outcome=SHADOW_HE, orphans=site != "server_upgrade")
        if not same:
            log.warning("brain: the busy verdict %s disagrees with the existing code at %s (legacy=%s)", reading.verdict, site, shown)
        return reading
    except Exception:                                   # noqa: BLE001 -- מוח שנפל אינו מפיל את בדיקת העסוק
        log.exception("brain: the busy verdict at %s was not recorded", site)
        return None
