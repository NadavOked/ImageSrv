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
`expand_last`. ‏#1897 (הכרעת נדב 04/11): כל מחיצה אחרת שהקליטה כיווצה (‏D:, ‏E:)
חוזרת לגודלה במקור (`returning`), והמועמד לוקח את מה שנשאר (`cand_room`) — כל
המקום הנוסף בדיסק גדול, ובדיסק קטן רק הוא מתפשר. ‏`used_bytes` חסר = **לא
נבדק**, לא "נכנס" (עיקרון 5).
"""

from __future__ import annotations

import logging
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


def returning(manifest: dict, cand: dict | None) -> list[dict]:
    """‏#1897 (הכרעת נדב 04/11): המחיצות שהשחזור מחזיר לגודלן במקור — כל מחיצה
    שהקליטה כיווצה (`shrunk_from_bytes` גדול מ-`size_bytes`), חוץ מהמועמד, שלוקח
    את מה שנשאר. ‏#1898: גם **לפני** המועמד (‏C: לפני שורש הלינוקס) — השרת מחשב
    כמו הסבב המאושר. אין מועמד — אין הרחבה, ואף אחת אינה חוזרת. התאום של `back`
    (‏RETURN_JQ, ‏agent/lib/restore/diskfit.sh)."""
    if cand is None:
        return []
    return [p for p in manifest.get("partitions") or []
            if isinstance(p, dict) and p.get("index") != cand.get("index")
            and _whole(p.get("start_sector")) is not None and p["start_sector"] != cand.get("start_sector")
            and _whole(p.get("size_bytes")) is not None and _whole(p.get("shrunk_from_bytes")) is not None
            and p["shrunk_from_bytes"] > p["size_bytes"]]


def _mib_up(sector: int) -> int:
    return (sector + 2047) // 2048 * 2048


def cand_room(manifest: dict, cand: dict, disk_sectors: int) -> int:
    """‏#1897: כמה סקטורים המועמד מקבל על דיסק בן `disk_sectors`, כשכל מחיצה
    ב-`returning` חוזרת לגודלה במקור — בדיוק מה ש-`expand_last` כותב: הראש גדל
    במקומו ומה שאחריו זז קדימה ומיושר ל-MiB (‏`share_head`), המועמד מתחיל על MiB
    (‏#2016), והזנב נארז מסוף הדיסק פנימה, כל התחלה מיושרת מטה (‏`_move_to_tail`).
    יכול לצאת קטן מגודל המועמד באימג' — אז הדיסק אינו נכנס. התאום של
    `cand_room` (‏RETURN_JQ, ‏diskfit.sh)."""
    back = {p["index"] for p in returning(manifest, cand)}
    parts = [p for p in manifest.get("partitions") or []
             if isinstance(p, dict) and _whole(p.get("start_sector")) is not None]
    start = cand["start_sector"]
    off = 0
    for p in sorted((p for p in parts if p["start_sector"] < start), key=lambda p: p["start_sector"]):
        if off > 0:
            off = _mib_up(p["start_sector"] + off) - p["start_sector"]
        if p.get("index") in back:
            off += (p["shrunk_from_bytes"] - p["size_bytes"]) // 512
    end = disk_sectors - 34
    for p in sorted((p for p in parts if p["start_sector"] > start), key=lambda p: -p["start_sector"]):
        size = p["shrunk_from_bytes"] if p.get("index") in back else p["size_bytes"]
        end = (end - size // 512 + 1) // 2048 * 2048 - 1
    return end - _mib_up(start + off) + 1


def _smallest_disk(manifest: dict, cand: dict, want_bytes: int) -> int:
    """הדיסק הקטן ביותר (בבייטים, כפולה של סקטור) שבו המועמד מקבל `want_bytes`.
    ‏`cand_room` עולה עם הדיסק (היישור מטה רק מתעכב), ולכן חיפוש בינארי."""
    want = -(-want_bytes // 512)
    hi = 1 << 21
    while cand_room(manifest, cand, hi) < want:
        hi *= 2
    lo = 0
    while lo < hi:
        mid = (lo + hi) // 2
        if cand_room(manifest, cand, mid) >= want:
            hi = mid
        else:
            lo = mid + 1
    return lo * 512


def free_after(manifest: dict, disk_bytes: int) -> tuple[int | None, str] | None:
    """(בייטים פנויים במחיצת המערכת אחרי שחזור לדיסק בגודל `disk_bytes`,
    התפקיד שלה). ‏`(None, role)` = ‏`used_bytes` לא נמדד. ‏`None` = אין מחיצת
    מערכת, והכלל אינו חל. התאום של `system_free_after` בסוכן.

    ‏#1897: כשמחיצות מכווצות חוזרות לגודלן במקור, מחיצת המערכת היא המועמד —
    והיא מקבלת את `cand_room` — או אחת מהן, בגודלה במקור. אחרת היא גדלה רק
    כשהיא המועמד, ומעבר לסף של 1GiB, כמו קודם."""
    sys_part = system_partition(manifest)
    if sys_part is None:
        return None
    used, size = _whole(sys_part.get("used_bytes")), _whole(sys_part.get("size_bytes"))
    need = required_bytes(manifest)
    if used is None or size is None or need is None:
        return None, sys_part["role"]
    cand = expandable_candidate(manifest)
    back = returning(manifest, cand)
    is_cand = cand is not None and cand.get("index") == sys_part.get("index")
    if back:
        if is_cand:
            return cand_room(manifest, cand, disk_bytes // 512) * 512 - used, sys_part["role"]
        if any(p.get("index") == sys_part.get("index") for p in back):
            return sys_part["shrunk_from_bytes"] - used, sys_part["role"]
        return size - used, sys_part["role"]
    if is_cand and disk_bytes - need > EXPAND_THRESHOLD:
        return size - used + disk_bytes - need, sys_part["role"]
    return size - used, sys_part["role"]


def min_disk_bytes(manifest: dict, gb: int) -> int | None:
    """הדיסק הקטן ביותר שהאימג' נכנס אליו **כולל** `gb` פנויים במחיצת
    המערכת — מה שהקונסולה מציגה כ"נכנס לדיסק מ-X". ‏`None` = לא ניתן לקבוע
    (גיאומטריה חסרה, או `used_bytes` שלא נמדד כשהכלל דלוק).

    ‏#1897: כשמחיצות מכווצות חוזרות לגודלן במקור, הדיסק צריך להכיל אותן
    בגודלן במקור ואת המועמד בגודלו באימג' (‏`volumes_fit` בסוכן) — וגם את
    `gb` במחיצת המערכת: כשהיא המועמד, היא לבדה מתפשרת; כשהיא אחת מהחוזרות,
    המקום שבה קבוע, ואם הוא קטן מ-`gb` — אין דיסק שבו האימג' נכנס."""
    need = required_bytes(manifest)
    if need is None:
        return None
    sys_part = system_partition(manifest)
    cand = expandable_candidate(manifest)
    if returning(manifest, cand):
        want = _whole(cand.get("size_bytes")) or 0
        if gb > 0 and sys_part is not None:
            fixed = free_after(manifest, need)
            if fixed is None or fixed[0] is None:
                return None
            if cand.get("index") == sys_part.get("index"):
                want = max(want, gb * GIB + sys_part["used_bytes"])
            elif fixed[0] < gb * GIB:
                return None    # מקומה קבוע בכל דיסק — אין גודל שבו הוא נכנס
        return max(need, _smallest_disk(manifest, cand, want))
    if gb <= 0 or sys_part is None:
        return need
    at_need = free_after(manifest, need)
    if at_need is None or at_need[0] is None:
        return None
    short = gb * GIB - at_need[0]
    if short <= 0:
        return need
    if cand is None or cand.get("index") != sys_part.get("index"):
        return None    # המחיצה לא תגדל באף דיסק — אין גודל שבו הוא נכנס
    return need + max(short, EXPAND_THRESHOLD + 1)


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
