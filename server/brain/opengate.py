"""#3662 (S27, B70): פסק אחד של המוח לפני פתיחת סבב -- פתח / הזהר / חסום -- ו-`room.open_round` מציית.

**הכרעת נדב 18/11 (#3653):** ההחלטה "לפתוח או לחסום" היא של המוח; השרת רק מבצע. עד #3662 אף שער (`gates.py`, B23 B49 B51 B52 B53
B54 B55 B56 B57 B60) לא חסם, ואף אחד לא חיבר את קריאותיו לפסק אחד. כאן הן הופכות לפסק אחד לסבב, ברשימה אחת של כללים:

- ``BLOCK`` אם **שער אחד לפחות** הוכיח שהפתיחה חסרת תוחלת או מסוכנת (זה הגדרת ``BLOCK`` בכל שער, ראו `gates.py`);
- אחרת ``WARN`` אם שער אחד לפחות מצא בעיה והפתיחה עדיין סבירה;
- אחרת ``UNDECIDED`` אם שער אחד לפחות **לא הצליח לבדוק** (עיקרון 5: זה אינו ``OPEN``, וגם אינו ``BLOCK``);
- ``OPEN`` רק כשכל השערים ענו ``OPEN`` על ראיה חיובית. אין שערים = ``UNDECIDED``.

**שני מצבים, מתג אחד** (``brain_round_gate``): ``shadow`` -- ברירת המחדל, וגם לכל ערך חסר, פגום או לא מוכר -- הפתיחה כמו
שהייתה: השערים רצים ברקע **אחרי** שהסבב נפתח (`gates.start`), והפסק נרשם ב-``brain_decisions`` עם מה שהקוד הישן עשה
(``legacy=opened``) והאם המוח הסכים (``agreement``): זה החומר של שער המעבר צל -> אכיפה (#3563). ``enforce`` -- ממומש, **כבוי, ואינו
ב-`WRITE_SETTINGS` של הקונסולה**: ``open_round`` קורא ל-`gate` **לפני** שהוא תופס את חריץ הסבב, ו-``BLOCK`` מסרב לפתוח (409,
הסיבה היא הטקסט של הקוד הקיים בשער). **הקריאה ב-``enforce`` מסונכרנת בתוך בקשת הפתיחה**, עד `gates.TIMEOUT_S` (20 ש', בשל ה-`collect` האיטי של B51), והשערים רצים שוב ברקע אחרי הפתיחה כמו ב-``shadow``. ההפעלה היא החלטה של נדב אחרי הוכחה על ברזל, ולא כאן. ``brain_veto_mode=off`` = לא נקרא
ולא נרשם, בשני המצבים.

**לפני הפתיחה נקראת רק התת-קבוצה ש-``Gate.pre_open``** (RAID, מקום, בריאות, AHCI, PXE, כרטיס רשת, מוחזק/probe/F1): שלושת האחרים
(אתחולים B52, שאלות פתוחות B55, קושחה וחיווט B56) קוראים את שורת `room_rounds` של הסבב, והיא עוד לא קיימת. הם נקראים אחרי
הפתיחה, והפסק של אחרי הפתיחה (``point=after_open``) כולל את כולם. **AHCI לפני הפתיחה לעולם אינו ``BLOCK``**: ה-``BLOCK`` שלו (פס ייצור)
נקבע לפי שורת הסבב, שעוד אין.

**מה אינו כאן, במכוון** (#3653, קבוצה ג): דיסק אדום אינו יעד (`disk_rule`), האימג' חייב להיכנס פיזית לדיסק הקטן ביותר (`fit_refusal`),
ומבנה בחירת הדיסקים (`_validate_target_slots`) -- אלה אינווריאנטים, ונשארים בקוד שלהם ומסרבים כמו קודם. `min_free_gb` הוא הגדרה של
המשתמש (קבוצה ב). הפסק כאן לא מוסיף ולא מחליף אותם: הם רצים לפניו, ומסרבים לפניו.

**לעולם אינו זורק** אל פתיחת הסבב: מוח שנפל הוא שורת לוג, והפסק שלו ``UNDECIDED`` שאינו חוסם ואינו מאשר.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from server.brain import memory, veto
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN, Reading
from server.db import get_setting, SqlConnection

log = logging.getLogger("imagesrv.brain")


class _Titled(Protocol):
    """מה ש-`combine` קורא משער: מזהה וכותרת. פרוטוקול ולא `gates.Gate`, כי `gates` מייבא את המודול הזה."""
    @property
    def id(self) -> str: ...
    @property
    def title_he(self) -> str: ...


SETTING = "brain_round_gate"
SHADOW, ENFORCE = "shadow", "enforce"
RULE, ACTION = "B70", "ROUND_OPEN"
POINT_BEFORE, POINT_AFTER = "open_round", "after_open"
NOT_RUN = "OFF"
#: חומרת הפסק, מהקל לחמור. ``UNDECIDED`` מעל ``OPEN`` ומתחת ל-``WARN``: "לא נבדק" אינו תקין.
_RANK = {OPEN: 0, UNDECIDED: 1, WARN: 2, BLOCK: 3}
#: כמה סיבות נכנסות לטקסט (השאר נספרות), כדי שפסק של עשרה משכפלים לא יהפוך לקיר טקסט. כל הראיות נשמרות בכל מקרה.
SHOWN = 3
SHADOW_OPENED_HE = "צל: הסבב כבר נפתח כמו קודם"
ENFORCED_BLOCK_HE = "הסבב לא נפתח"
ENFORCED_OPEN_HE = "הסבב נפתח"


def mode(conn: SqlConnection) -> str:
    """המתג. רק ``enforce`` בדיוק מדליק את האכיפה; חסר, פגום, לא מוכר או שלא נקרא = ``shadow`` (לעולם לא חוסם בגלל "לא יודע")."""
    try:
        return ENFORCE if get_setting(conn, SETTING) == ENFORCE else SHADOW
    except Exception:                                                       # noqa: BLE001
        log.exception("brain: %s unreadable, using %s", SETTING, SHADOW)
        return SHADOW


def combine(pairs: Sequence[tuple[_Titled, Reading]]) -> tuple[str, str, tuple[str, ...]]:
    """``(פסק, סיבה בעברית, ראיות)`` מזוגות ``(שער, קריאה)`` (לשער ``id`` ו-``title_he``). טהור.

    הסיבה היא הסיבות של הקוד הקיים, מילה במילה, עם שם השער: המוח אינו מנסח מחדש. הראיות: הפסק החמור של כל שער
    (``B49=WARN``), ואחריו ספירה לכל פסק."""
    if not pairs:
        return UNDECIDED, "אף שער לא ענה: לא נבדק דבר, ולכן אין פסק", ("gates=0",)
    worst: dict[str, tuple[str, str]] = {}          # id השער -> (הפסק החמור, כותרת)
    by: dict[str, list[tuple[_Titled, Reading]]] = {BLOCK: [], WARN: [], UNDECIDED: [], OPEN: []}
    for gate, reading in pairs:
        by[reading.verdict].append((gate, reading))
        before = worst.get(gate.id)
        if before is None or _RANK[reading.verdict] > _RANK[before[0]]:
            worst[gate.id] = (reading.verdict, gate.title_he)
    evidence = (*(f"{gid}={v}" for gid, (v, _t) in worst.items()),
                *(f"count_{v.lower()}={len(rows)}" for v, rows in by.items()))
    for verdict, head in ((BLOCK, "פתיחת הסבב חסרת תוחלת או מסוכנת"), (WARN, "יש בעיה, והפתיחה עדיין סבירה")):
        rows = by[verdict]
        if rows:
            shown = " · ".join(f"{g.title_he}: {r.reason}" for g, r in rows[:SHOWN])
            more = f" · ועוד {len(rows) - SHOWN}" if len(rows) > SHOWN else ""
            return verdict, f"{head}: {shown}{more}", evidence
    if by[UNDECIDED]:
        titles = sorted({g.title_he for g, _r in by[UNDECIDED]})
        return UNDECIDED, f"לא הצלחנו לבדוק ({len(titles)} שערים): " + ", ".join(titles[:SHOWN]) + (" ועוד" if len(titles) > SHOWN else ""), evidence
    return OPEN, f"כל {len(worst)} השערים נבדקו, ואין מה להזהיר", evidence


@dataclass(frozen=True)
class Decision:
    """מה `gate` החזיר ל-`open_round`. ``block`` רק ב-``enforce`` וכש-``verdict`` הוא ``BLOCK``: מי שפותח לא פותח."""
    verdict: str
    reason: str
    block: bool = False

    @property
    def he(self) -> str:
        return f"הסבב לא נפתח: {self.reason}"


def gate(conn: SqlConnection, macs: Sequence[str], *, timeout_s: float | None = None,
         read: Callable[[SqlConnection, tuple[str, ...], float | None], Sequence[tuple[_Titled, Reading]]] | None = None) -> Decision:
    """הפסק **לפני** שהסבב נפתח. ב-``shadow`` (ברירת המחדל) ו-``off`` לא קורה כאן דבר ולא נקרא דבר: ``NOT_RUN``, בלי חסימה, כמו קודם.
    ב-``enforce``: קורא את שערי ``pre_open`` (ב-`gates.read_pre_open`, או ב-``read`` שהטסט מזריק), רושם פסק, ו-``BLOCK`` חוסם.
    ``UNDECIDED`` ו-``WARN`` אינם חוסמים. לעולם אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF or mode(conn) != ENFORCE:
            return Decision(NOT_RUN, "הפסק לא נקרא לפני הפתיחה")
        from server.brain import gates                                      # noqa: PLC0415 -- `gates` מייבא את המודול הזה
        pairs = (read or gates.read_pre_open)(conn, tuple(macs), timeout_s)
        verdict, reason, evidence = combine(pairs)
        block = verdict == BLOCK
        memory.record(conn, mode=veto.ENFORCE, rule=RULE, action=ACTION, point=POINT_BEFORE, entity=None, verdict=verdict,
                      reason=reason, evidence=(*evidence, f"gates={len(pairs)}"),
                      outcome=ENFORCED_BLOCK_HE if block else ENFORCED_OPEN_HE)
        log.log(logging.WARNING if verdict in (WARN, BLOCK) else logging.INFO, "brain %s %s: %s", RULE, verdict, reason)
        return Decision(verdict, reason, block)
    except Exception:                                                       # noqa: BLE001 -- הסבב אינו נחסם ואינו מאושר בגלל כשל של המוח
        log.exception("brain: %s failed before the round opened", RULE)
        return Decision(UNDECIDED, "ההכרעה נכשלה")


def record_after_open(conn: SqlConnection, round_id: str, pairs: Sequence[tuple[_Titled, Reading]]) -> int | None:
    """הפסק של **אחרי** הפתיחה, על כל עשרת השערים (`gates.run`), בצל תמיד: שורה אחת על הסבב, עם מה שהקוד הישן עשה (פתח) והאם המוח
    הסכים. ``pairs`` ריק (המתג הכללי ``off``) = לא נרשם. אינו זורק; ``None`` = לא נרשם."""
    try:
        if not pairs:
            return None
        verdict, reason, evidence = combine(pairs)
        agrees = verdict != BLOCK
        return memory.record(conn, mode=veto.SHADOW, rule=RULE, action=ACTION, point=POINT_AFTER, round_id=round_id,
                             entity=("round", round_id), verdict=verdict, reason=reason,
                             evidence=(*evidence, "legacy=opened", f"agreement={'yes' if agrees else 'no'}", f"gates={len(pairs)}"),
                             outcome=SHADOW_OPENED_HE + ("" if agrees else "; המוח היה חוסם"))
    except Exception:                                                       # noqa: BLE001 -- הסבב כבר נפתח
        log.exception("brain: %s of %s was not recorded", RULE, round_id)
        return None
