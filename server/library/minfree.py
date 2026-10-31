"""מינימום מקום פנוי במחיצת המערכת אחרי שחזור (‏#1354).

הכרעת נדב 13/10: אימג' מכווץ שמשוחזר לדיסק קטן יכול "להיכנס" ולהשאיר
ל-Windows כמה GB בודדים — זה לא תקין. יעד שאחרי השחזור יישארו בו פחות
מ-`min_free_gb` פנויים במחיצת המערכת **אינו נכנס**, בדיוק כמו יעד קטן מדי.

הערך הוא **הגדרה בקונסולה** (‏`settings.min_free_gb`, ברירת מחדל 20, ‏0–500),
ולא קבוע בקוד: נדב רוצה לשנות אותו בלי עדכון גרסה. הסוכן מקבל אותו בכל
תשובת hello (‏`min_free_gb`), והאכיפה היא שלו (‏`agent/lib/restore/diskfit.sh`,
‏`min_free_check`) — מה שכאן הוא התאום שהקונסולה מציגה ממנו "נכנס לדיסק
מ-X GB", וחדר השיכפולים מסרב ממנו לפני שנפתח גל.

**מחיצת המערכת** = `system_partition` (‏#1875): המסומנת `"system": true`
בקליטה, אחרת מחיצת ה-`windows` הגדולה ביותר, ואם אין — ה-`linux` הראשונה.
אותו כלל בסוכן (`system_part`, ‏diskfit.sh) וב-hostname.sh. היא גדלה על היעד רק כשהיא
המחיצה שהשחזור מותח (‏`expandable_candidate`) ורק מעבר לסף של 1GiB של
`expand_last` — ובחלקה בלבד: ‏#1897 מחלק את המקום הנוסף בינה לבין המחיצות
המכווצות שאחריה לפי היחס במקור (`grow_share`). ‏`used_bytes` חסר = **לא נבדק**,
לא "נכנס" (עיקרון 5).
"""

from __future__ import annotations

import logging
import math
import sqlite3

from server.db import get_setting
from server.library.imagefit import expandable_candidate
from server.library.images import _whole, required_bytes

log = logging.getLogger("imagesrv.minfree")

SETTING = "min_free_gb"
#: התאום של `MIN_FREE_GB_DEFAULT` ב-`agent/lib/restore/diskfit.sh` — טסט משווה.
MIN_FREE_GB_DEFAULT = 20
MIN_FREE_GB_MAX = 500
GIB = 1 << 30
#: ‏`expand_last` מותח רק כשהיעד גדול מהפריסה ביותר מ-2097152 סקטורים.
EXPAND_THRESHOLD = 1 << 30


def parse_gb(raw: object) -> int | None:
    """ערך הגדרה תקין — מספר שלם 0–500 — או `None`. ‏`bool` אינו מספר כאן,
    ומחרוזת עם אפס מוביל נדחית כמו בסוכן (‏ash קורא ‎020 כאוקטלי)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() \
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 3:
        value = int(raw)
    else:
        return None
    return value if 0 <= value <= MIN_FREE_GB_MAX else None


def min_free_gb(conn: sqlite3.Connection) -> int:
    """ההגדרה, או ברירת המחדל. חסרה = לא נקבעה (ברירת המחדל, בשקט); פגומה
    = ברירת המחדל **עם שורת יומן** — לעולם לא 0, שהוא "הכלל כבוי"."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return MIN_FREE_GB_DEFAULT
    value = parse_gb(raw)
    if value is None:
        log.warning("setting %s=%r is not 0-%d -- using %d", SETTING, raw,
                    MIN_FREE_GB_MAX, MIN_FREE_GB_DEFAULT)
        return MIN_FREE_GB_DEFAULT
    return value


#: ‏#1875: "לא ידוע איזו מחיצה היא C:" — ספק אמיתי, ולא ניחוש. הסוכן מחזיר
#: באותו מצב `ambiguous`, ושניהם אומרים את אותן מילים (‏AMBIGUOUS_WHY).
AMBIGUOUS: dict = {"role": "windows", "ambiguous": True}
AMBIGUOUS_WHY = "לא ידוע איזו מחיצת windows היא C: (אין סימון מהקליטה, ואין אחת גדולה מכולן)"
#: ‏#1899: יותר ממחיצת linux אחת בלי סימון — ‏/boot נפרד. "הראשונה" הייתה /boot.
AMBIGUOUS_LINUX: dict = {"role": "linux", "ambiguous": True}
AMBIGUOUS_LINUX_WHY = "לא ידוע איזו מחיצת לינוקס היא השורש (אין סימון מהקליטה, ויש יותר מאחת)"


def system_partition(manifest: dict) -> dict | None:
    """מחיצת המערכת — התאום של `system_part` ב-`agent/lib/restore/diskfit.sh` (‏#1875).

    ראיה קודם: הקליטה מסמנת `"system": true` על המחיצה שכוורת
    ‏`Windows/System32/config/SYSTEM` יושבת בה. מניפסט בלי הסימון (נקלט לפני
    #1875): מחיצת ה-`windows` **הגדולה ביותר** — מחיצת OEM/כלים קטנה, ‏C:
    מחזיקה את המערכת (‏Vm: ‏3GB לפני 223GB). שתיים בגודל העליון, או אחת בלי
    גודל = ‏`AMBIGUOUS`, שהקוראים מדווחים "לא נבדק" ולא "לא נכנס" שגוי
    (עיקרון 5). אין `windows` — ה-`linux` הראשונה על הפלטה. אין — `None`."""
    parts = [p for p in manifest.get("partitions") or []
             if isinstance(p, dict) and _whole(p.get("start_sector")) is not None]
    wins = [p for p in parts if p.get("system") is True] \
        or [p for p in parts if p.get("role") == "windows"]
    if len(wins) == 1:
        return wins[0]
    if wins:
        doubt = AMBIGUOUS_LINUX if wins[0].get("role") == "linux" else AMBIGUOUS
        sizes = [_whole(p.get("size_bytes")) for p in wins]
        if None in sizes:
            return doubt
        top = [p for p, size in zip(wins, sizes) if size == max(sizes)]
        return top[0] if len(top) == 1 else doubt
    linux = [p for p in parts if p.get("role") == "linux"]
    if len(linux) > 1:
        return AMBIGUOUS_LINUX     # ‏#1899: לא "הראשונה על הדיסק" — היא /boot
    return linux[0] if linux else None


#: ‏#1896: הסימון של המפעיל (`system_source`). סימון בלי השדה — של הקליטה, מראיה (‏#1875).
OPERATOR = "operator"


def system_candidates(manifest: dict) -> list[dict]:
    """‏#1896 (הכרעת נדב 22/10): המחיצות שהמפעיל בוחר ביניהן את C: — אותו ספק
    של `system_partition`, **בלי** הסימון: מחיצות ה-`windows` בגודל העליון (או
    כולן, כשלאחת אין גודל); בלי `windows` — כל מחיצות ה-`linux` (‏#1899, ‏/boot
    נפרד). פחות משתיים = אין בין מה לבחור, `[]`. בסדר שעל הדיסק."""
    parts = [p for p in manifest.get("partitions") or []
             if isinstance(p, dict) and _whole(p.get("start_sector")) is not None]
    wins = [p for p in parts if p.get("role") == "windows"]
    if len(wins) > 1:
        sizes = [_whole(p.get("size_bytes")) for p in wins]
        pool = wins if None in sizes else [p for p, s in zip(wins, sizes) if s == max(sizes)]
    else:
        pool = [] if wins else [p for p in parts if p.get("role") == "linux"]
    return sorted(pool, key=lambda p: _whole(p.get("start_sector"))) if len(pool) > 1 else []


def markable(manifest: dict) -> bool:
    """אפשר לסמן: יש בין מה לבחור, ואין סימון **של הקליטה** (`system: true` בלי
    `system_source`) — ראיה מהדיסק אינה נדרסת. סימון של מפעיל אפשר לשנות."""
    if not system_candidates(manifest):
        return False
    return not any(isinstance(p, dict) and p.get("system") is True and p.get("system_source") != OPERATOR
                   for p in manifest.get("partitions") or [])


def unmarked(manifest: dict) -> bool:
    """‏#1896: "לא נבדק" עד שמסמנים — אפשר לסמן, ועוד לא סומן."""
    return markable(manifest) and not any(
        isinstance(p, dict) and p.get("system") is True for p in manifest.get("partitions") or [])


def candidates_view(manifest: dict) -> list[dict]:
    """מה שהקונסולה, מחשב הבנייה והמשכפל מציגים לבחירה."""
    return [{"index": _whole(p.get("index")), "role": p.get("role"), "fs": p.get("fs") or "",
             "size_bytes": _whole(p.get("size_bytes")), "used_bytes": _whole(p.get("used_bytes")),
             "system": p.get("system") is True}
            for p in system_candidates(manifest)]


def grow_share(manifest: dict, cand: dict, part: dict | None = None) -> tuple[int, int]:
    """‏#1897 (הכרעת נדב 22/10): (גודל המקור של `part` — ברירת המחדל: המועמד —
    סכום גודלי המקור של כל מי שחולק במקום שהשחזור מוסיף). חולקים: המועמד, וכל
    מחיצה על הדיסק שהקליטה כיווצה (`shrunk_from_bytes`) — ‏#1898: גם **לפניו**
    (‏C: לפני שורש הלינוקס). כל אחת מקבלת `share(grow, own, total)`. התאום של
    `share_tail`/`share_head` (‏agent/lib/restore/expandshare.sh) ושל `system_free_after`
    (‏diskfit.sh)."""
    c_own = _whole(cand.get("shrunk_from_bytes")) or _whole(cand.get("size_bytes")) or 0
    others = sum(_whole(p.get("shrunk_from_bytes")) or 0
                 for p in manifest.get("partitions") or []
                 if isinstance(p, dict) and p.get("index") != cand.get("index"))
    if part is None or part.get("index") == cand.get("index"):
        return c_own, c_own + others
    return _whole(part.get("shrunk_from_bytes")) or 0, c_own + others


def shares_room(cand: dict | None, part: dict) -> bool:
    """‏#1898: האם `part` גדלה בשחזור — המועמד עצמו, או מחיצה שהקליטה כיווצה."""
    return cand is not None and (cand.get("index") == part.get("index")
                                 or _whole(part.get("shrunk_from_bytes")) is not None)


def share(grow: int, own: int, total: int) -> int:
    """החלק של המועמד ב-`grow` — ‏floor(grow × (own / total)), בדיוק סדר הפעולות
    של jq בסוכן (שני כפל/חילוק IEEE), כדי שהתאומים יאמרו אותו בייט."""
    return math.floor(grow * (own / total)) if total > 0 else grow


def free_after(manifest: dict, disk_bytes: int) -> tuple[int | None, str] | None:
    """(בייטים פנויים במחיצת המערכת אחרי שחזור לדיסק בגודל `disk_bytes`,
    התפקיד שלה). ‏`(None, role)` = ‏`used_bytes` לא נמדד. ‏`None` = אין מחיצת
    מערכת, והכלל אינו חל. התאום של `system_free_after` בסוכן."""
    sys_part = system_partition(manifest)
    if sys_part is None:
        return None
    used, size = _whole(sys_part.get("used_bytes")), _whole(sys_part.get("size_bytes"))
    need = required_bytes(manifest)
    if used is None or size is None or need is None:
        return None, sys_part["role"]
    cand = expandable_candidate(manifest)
    grow = disk_bytes - need
    grows = shares_room(cand, sys_part) and grow > EXPAND_THRESHOLD
    if not grows:
        return size - used, sys_part["role"]
    own, total = grow_share(manifest, cand, sys_part)
    return size - used + share(grow, own, total), sys_part["role"]


def min_disk_bytes(manifest: dict, gb: int) -> int | None:
    """הדיסק הקטן ביותר שהאימג' נכנס אליו **כולל** `gb` פנויים במחיצת
    המערכת — מה שהקונסולה מציגה כ"נכנס לדיסק מ-X". ‏`None` = לא ניתן לקבוע
    (גיאומטריה חסרה, או `used_bytes` שלא נמדד כשהכלל דלוק)."""
    need = required_bytes(manifest)
    if need is None:
        return None
    sys_part = system_partition(manifest)
    if gb <= 0 or sys_part is None:
        return need
    at_need = free_after(manifest, need)
    if at_need is None or at_need[0] is None:
        return None
    short = gb * GIB - at_need[0]
    if short <= 0:
        return need
    cand = expandable_candidate(manifest)
    if not shares_room(cand, sys_part):
        return None    # המחיצה לא תגדל באף דיסק — אין גודל שבו הוא נכנס
    own, total = grow_share(manifest, cand, sys_part)   # ‏#1897/#1898: היא מקבלת רק את חלקה
    room = -(-short * total // own) if own > 0 and total > 0 else short
    while share(room, own, total) < short:   # עיגול ה-float — לכל היותר צעד או שניים
        room += 1
    return need + max(room, EXPAND_THRESHOLD + 1)


def where(role: str) -> str:
    return "במחיצת הלינוקס הראשית" if role == "linux" else "ב-C:"


def _unchecked_text(gb: int, role: str, ambiguous: bool = False) -> str:
    why = (AMBIGUOUS_LINUX_WHY if role == "linux" else AMBIGUOUS_WHY) if ambiguous \
        else "לא נמדד כמה תפוס בה (used_bytes חסר)"
    return f"לא נבדק אם יישארו {gb}GB פנויים {where(role)} — {why}"


def unchecked(manifest: dict, gb: int) -> str | None:
    """‏#1494: הסיבה שהכלל **אינו ניתן לבדיקה** על האימג' הזה בכל דיסק שהוא —
    ‏`used_bytes` של מחיצת המערכת לא נמדד — או `None`. הסוכן מסרב לזה בכל
    מגירה (`min_free_check`), ולכן הקונסולה משביתה את האימג' וחדר השיכפולים
    אינו פותח לו סבב: סבב שוודאי ייכשל בכל מגירה אינו "לא ידוע". אותה
    מחרוזת של הסוכן."""
    if gb <= 0:
        return None
    sys_part = system_partition(manifest)
    if sys_part is None or _whole(sys_part.get("used_bytes")) is not None:
        return None
    return _unchecked_text(gb, sys_part["role"], bool(sys_part.get("ambiguous")))


def refusal(manifest: dict, disk_bytes: int, gb: int) -> str | None:
    """המחרוזת של הסוכן (‏`min_free_check`) לדיסק בגודל נתון, או `None`
    כשנשאר מספיק. אותה מחרוזת בדיוק, כדי שהמפעיל יראה סיבה אחת."""
    if gb <= 0:
        return None
    result = free_after(manifest, disk_bytes)
    if result is None:
        return None
    free, role = result
    if free is None:
        return _unchecked_text(gb, role, bool((system_partition(manifest) or {}).get("ambiguous")))
    if free >= gb * GIB:
        return None
    return f"לא נכנס — יישארו רק {free / GIB:.1f} GB פנויים {where(role)}, המינימום {gb}GB"
