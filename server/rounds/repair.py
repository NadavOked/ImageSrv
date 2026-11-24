"""שלב ההשלמה של מנוע החתיכות (#2533, שלב 3): מה חסר לכל דיסק, ומי ממלא את זה.

השולח החדש (`agent/chunkcast.c`) משדר את כל החתיכות בלי לחכות לאף מקלט. בסוף
המעבר כל משכפל כבר אמר לשרת אילו חתיכות עמידות על כל דיסק שלו (טווחים מצטברים,
חוזה הפורמט סעיף 9.1). **"מה חסר" הוא מה שאינו שם** — אין הודעת בקשה נפרדת, ולכן
אין סערת בקשות ברשת ההפצה (#2538).

המודול הזה הוא ההכרעה בלבד, בלי רשת ובלי DB; את הטווחים עצמם (בדיקה, איחוד, משלים) הוא
לוקח מהיומן של שלב 2 (`chunkledger`) — קורא אחד לצורה אחת:

- חתיכה שחסרה ל**שני מחשבים או יותר** נכנסת למעבר השלמה במולטיקאסט — שידור אחד
  מתקן את כולם.
- חתיכה שחסרה ל**מחשב אחד** (לכמה מהדיסקים שלו או לכולם): המחשב מוריד אותה
  בעצמו ב-HTTP, בבקשת Range לקובץ האימג'. אין קוד unicast חדש ב-UDP.
- "מקלט" הוא **מחשב**, לא דיסק: חבילה שאבדה אבדה לכל המגירות של אותו משכפל,
  וההורדה שלו ממלאת את כולן.

התקרות (עיקרון 5 — דיסק שלא הושלם נכשל בשמו, לעולם לא "מדולג"):

- דיסק שעדיין חסר אחרי `repairrule.MAX_ROUNDS` סבבי השלמה.

**דיסק איטי אינו כישלון** (#3170 מול #3295, הכרעת נדב 15/11, אפשרות ב': "ההפצה בקצב של המהירים. האיטי מפספס חתיכות ומשלים
אותן אחר כך"). דיסק שפיגר במעבר הראשון (`slow_reason`: כתב לאט מהמהירות המינימלית לכונן, לפי מה שהמקלט עצמו אמר
שהדיסק לא הספיק לכתוב, `skipped`) ואין לו כשל משלו **משלים** כמו כל חוסר: מהמגירה השכנה או ב-HTTP. הוא נרשם ב-`Plan.slow`
לשורת יומן בלבד. כשל נשאר רק בראיה של כשל אמיתי (כתיבה שנכשלה, `write_error`), והמוח מחזה ב-K3 (WAVE_ETA) כמה זמן ההשלמה
תארך, בצל, בלי להכשיל.

**חוסר שהדיסק אינו מסביר אינו כישלון** (הכרעת נדב, #3141 (א), 14/11): חתיכות שלא הגיעו למחשב —
ניתוק רשת או חבילות שאבדו — מושלמות מהשרת, כמה שלא יהיו, כמו מגירה שנשלפה וחזרה. עד כאן
דיסק שחסרו לו יותר מ-50% במעבר הראשון נכשל "לא עמד בקצב", גם כשהסיבה הייתה ניתוק של 4 דקות.

**#3659 (S10, S11, S12, הכרעת נדב 18/11): הסף והמכסה ו"דיסק איטי" הם החלטות של המוח** (`brain/repairrule.py`, K-REPAIR): `MULTICAST_MIN` (היה `THRESHOLD`) ו-`MAX_ROUNDS` (היה
`MAX_REPAIRS`) הם הצעה של התכנון ולא מדידה (design-2533-2528-unified.md סעיף 5.3), ושניהם שם. המודול הזה רק חותך טווחים לפי הערכים שקיבל. מחשב שהפסיק לדווח אינו כאן בכלל: הקורא מעביר רק את מי
שחי, ומי ש"אבד" עובר לשאלה של שלב 1 בלי לעכב את האחרים.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from server.brain import repairrule
from server.rounds import chunkledger

Ranges = list[tuple[int, int]]      # סגורים, ממוינים, בלי חפיפה ובלי צמודים (חוזה 9.1)


@runtime_checkable
class _PerDiskFloor(Protocol):
    """`hdd_policy.RepairFloor`: the minimum speed as a number, and the decision for one disk."""

    def for_disk(self, mac: str, serial: str) -> tuple[str | None, int | None]: ...


def slow_reason(taken: int, skipped: int | None, asked_mbps: float | None, floor_mbps: int | None) -> str | None:
    """#3170: הסיבה, כשהדיסק כתב במעבר הראשון לאט מהמינימום -- אחרת None. **ההכרעה היא של המוח** (`repairrule.slow_reason`, S12, #3659);
    השם נשאר כאן כי `brain.joinwatch` (B41) קורא דרכו את אותה הכרעה."""
    return repairrule.slow_reason(taken, skipped, asked_mbps, floor_mbps)


def parse_ranges(raw: object, chunks: int) -> Ranges | None:
    """הטווחים כפי שהסוכן שלח אותם — או ``None`` כשהם אינם בצורה של החוזה (9.2: נדחה כולו,
    לא "מתוקן"). הבודק הוא של היומן (`chunkledger.well_formed`): מה שהיומן היה מסרב לרשום,
    שלב ההשלמה אינו קורא."""
    try:
        return chunkledger.well_formed(raw, chunks)
    except chunkledger.LedgerProblem:
        return None


#: איחוד שני יומנים — של היומן עצמו. היומן רק גדל: דיווח ישן שהגיע באיחור אינו מוחק דבר.
union = chunkledger.union


def missing(chunks: int, durable: Ranges) -> Ranges:
    """מה שאינו ביומן, מתוך ``[0, chunks-1]`` (`chunkledger.missing`). המשלים של היומן מניח רשימה
    מנורמלת (ממוינת, בלי חפיפה) וטועה בשקט על אחרת, ולכן היא מנורמלת כאן, במקום אחד, ולא אצל כל קורא."""
    return chunkledger.missing(chunkledger.union(durable, []), chunks)


def count(ranges: Ranges) -> int:
    return sum(hi - lo + 1 for lo, hi in ranges)


def as_text(ranges: Ranges) -> str:
    """הצורה של ``chunkcast send --chunks``: ``0-3,7-7``."""
    return ",".join(f"{lo}-{hi}" for lo, hi in ranges)


def _events(per_machine: dict[str, Ranges]) -> list[tuple[int, int, str]]:
    return sorted((point, delta, mac) for mac, ranges in per_machine.items()
                  for lo, hi in ranges for point, delta in ((lo, 1), (hi + 1, -1)))


def _split(per_machine: dict[str, Ranges], threshold: int) -> tuple[Ranges, dict[str, Ranges]]:
    """(חתיכות שחסרות ל-``threshold`` מחשבים ומעלה, לכל מחשב מה שחסר רק לו).
    מעבר אחד על קצות הטווחים — לא על 47,500 חתיכות לכל מחשב."""
    shared: Ranges = []
    alone: dict[str, Ranges] = {}
    lacking: set[str] = set()
    events = _events(per_machine)
    for i, (point, delta, mac) in enumerate(events):
        (lacking.add if delta > 0 else lacking.discard)(mac)
        if i + 1 < len(events) and events[i + 1][0] == point:
            continue                            # עוד שינוי באותה נקודה: מכריעים אחרי כולם
        if i + 1 == len(events) or not lacking:
            continue
        span = [(point, events[i + 1][0] - 1)]
        if len(lacking) >= threshold:
            shared = union(shared, span)
        else:
            for mac in lacking:
                alone[mac] = union(alone.get(mac, []), span)
    return shared, alone


@dataclass
class Plan:
    """מה קורה בסבב ההשלמה הבא של מחיצה אחת."""

    multicast: Ranges = field(default_factory=list)             # משודרות שוב לכולם
    receive: set[str] = field(default_factory=set)              # המחשבים שמאזינים למעבר הזה
    fetch: dict[str, Ranges] = field(default_factory=dict)      # מחשב ← מה שהוא מוריד ב-HTTP
    failed: dict[tuple[str, str], str] = field(default_factory=dict)   # (מחשב, serial) ← הסיבה, למפעיל
    #: #3141: מחשב ← כמה חתיכות לא הגיעו אליו במעבר הראשון (החוסר שה-`skipped` של הדיסקים שלו אינו מסביר).
    #: רק כשהסוכן דיווח `skipped`: בלעדיו אין לדעת אם החוסר ברשת או בדיסק, ואין טענה.
    lost_in_net: dict[str, int] = field(default_factory=dict)
    #: #3170: (מחשב, serial) ← הסיבה, לדיסק שפיגר במעבר הראשון. **אינו נכשל**: מה שחסר לו משלים כמו כל חוסר.
    slow: dict[tuple[str, str], str] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        """אין מה לשדר ואין מה להוריד: כל דיסק שלא נכשל שלם."""
        return not self.multicast and not self.fetch


def plan(chunks: int, durable: dict[str, dict[str, Ranges]], *, repairs_done: int,
         skipped: dict[str, dict[str, int]] | None = None, asked_mbps: float | None = None,
         floor_mbps: int | None = None, threshold: int | None = None) -> Plan:
    """``durable``: מחשב ← (serial של דיסק ← היומן שלו), **רק מחשבים ודיסקים חיים**.
    ``repairs_done``: כמה סבבי השלמה כבר רצו למחיצה הזו (0 = זה עתה נגמר המעבר הראשון).
    ``skipped``: מחשב ← (serial ← כמה חתיכות המקלט ויתר עליהן לדיסק במעבר הראשון), כפי שהסוכן דיווח;
    ``asked_mbps``: הקצב שהמעבר ביקש לכתוב; ``floor_mbps``: המהירות המינימלית לכונן (`slow_reason`);
    ``threshold``: כמה מחשבים צריכים להחסיר חתיכה כדי שתשודר שוב -- ההחלטה של המוח (`repairrule.threshold`); ``None`` = שלה. 1 = אין הורדות כלל -- הפצה
    ישירה (#3161), שבה אין קובץ בשרת להוריד ממנו, וכל מה שחסר חוזר מהמקור במולטיקאסט."""
    threshold = repairrule.threshold() if threshold is None else threshold
    out = Plan()
    per_machine: dict[str, Ranges] = {}
    for mac, disks in durable.items():
        lacks: Ranges = []
        told = (skipped or {}).get(mac, {})
        for serial, have in disks.items():
            gap = missing(chunks, have)
            if not gap:
                continue
            # X16: מדיניות HDD לכל דיסק (`hdd_policy.RepairFloor`) -- סירוב, או הציפייה שנלמדה לו
            refusal, disk_floor = (floor_mbps.for_disk(mac, serial)
                                   if isinstance(floor_mbps, _PerDiskFloor) else (None, floor_mbps))
            if refusal:
                out.failed[(mac, serial)] = refusal
                continue
            slow = (slow_reason(chunks - count(gap), told.get(serial), asked_mbps, disk_floor)
                    if repairs_done == 0 else None)
            if repairs_done == 0 and serial in told and count(gap) > told[serial]:
                out.lost_in_net[mac] = max(out.lost_in_net.get(mac, 0), count(gap) - told[serial])
            if slow:
                out.slow[(mac, serial)] = slow
            if repairrule.exhausted(repairs_done):
                out.failed[(mac, serial)] = repairrule.exhausted_text(count(gap))
            else:
                lacks = union(lacks, gap)
        if lacks:
            per_machine[mac] = lacks
    out.multicast, alone = _split(per_machine, threshold)
    for mac, lacks in per_machine.items():
        if any(lo <= hi2 and lo2 <= hi for lo, hi in lacks for lo2, hi2 in out.multicast):
            out.receive.add(mac)                # מה שחסר רק לו ימתין לסבב הבא: פעולה אחת למחשב בכל סבב
        elif mac in alone:
            out.fetch[mac] = alone[mac]
    return out
