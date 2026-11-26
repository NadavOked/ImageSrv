"""#3571 (פרוסה א) / #3563: **הצילום הקפוא** שהמוח מחליט ממנו -- החוזה, ולא ההחלטה.

החלטה אחת מתוך צילום אחד לכל חלון ואירוע. הצילום נקרא פעם אחת מהמונים, נקפא, ונשמר (`memory.save_snapshot`); ההחלטה
נרשמת עם מזהה הצילום (`memory.record(snapshot=...)`), והתוצאה נקשרת אליה אחר כך (`memory.resolve(performed=...,
result_snapshot=...)`). **המודול הזה טהור: אין בו DB, שעון ולא כלל.** הכללים הבאים (פרוסות ב-י) קוראים אותו ואינם נוגעים
באף החלטה קיימת -- הכול בצל.

**הצורה** (``VERSION`` 1, ראו `docs/architecture/interfaces.md`, "הצילום הקפוא של המוח"):

- ``id`` = ``s1-`` + 16 תווים ראשונים של sha256 מהתוכן. אותו תוכן = אותו מזהה, ושמירה כפולה היא שמירה אחת.
- ``rule`` + ``rule_version``, ו-``round_id`` ו-``wave_id`` **בנפרד** (גל אחד שייך לסבב; סבב אחד מכיל גלים), ``stream_id`` כשיש
  כמה זרמים (``None`` = זרם יחיד), ``phase`` (שלב העבודה), ``event`` (מה גרם לצילום: ``tick`` / ``phase`` / שם אירוע).
- ``resources`` = הזהויות שהצילום נוגע בהן: ``{"kind": "machine"|"disk"|"slot", "key": ...}``. מקלט הוא MAC, דיסק הוא serial --
  **לא** אותו דבר, ומדד של דיסק אינו נרשם על המכונה.
- ``metrics`` = שורה לכל (משאב, שם): ``value``, ``unit``, ``source``, ``received_at`` (מתי **נקלט**, לא מתי נמדד), ``age_s``
  (בעת הצילום) ו-``quality``. **נפח שנשלח, התקבל, נכתב, עבר flush ואומת הם חמישה שמות נפרדים** (``VOLUMES``); אין שדה
  "התקדמות" אחד שמקפל אותם.

**איכות** (``quality``) -- ראיה חיובית, לא היעדר סימן כישלון (עיקרון 5): ``ok`` / ``stale`` (הדיווח ישן מ-``max_age_s``) /
``missing`` (לא דווח: ``value`` הוא ``None``, **לא 0**) / ``clock`` (נקלט "אחרי" הצילום: שעון לא אמין, ``age_s`` הוא ``None``).
``value`` של ``stale`` נשמר, אבל אינו מצדיק החלטה.

**`Ledger`** -- מה נחשב התקדמות. כפילות, דיווח ישן ואיפוס מונה באתחול **אינם** התקדמות (אינווריאנט I14, interfaces.md
"סדר הדיווחים"); דיווח כפול אינו מקדם את הערך אבל **כן** מעדכן ``alive_at`` -- אות חיות נפרד.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone

VERSION = 1
#: חמשת הנפחים. כל אחד מדד בפני עצמו; **אסור** למזג שניים (flush אינו אימות, ו"נכתב" אינו "עמיד").
VOLUMES = ("sent", "received", "written", "flushed", "verified")
#: יחידות מותרות. מדד בלי יחידה מוכרת אינו נכנס: ``B/s`` ו-``bit/s`` הם שני דברים, והשוואה ביניהם דורשת יחס דחיסה (#3571).
UNITS = ("B", "B/s", "bit/s", "s", "chunks", "chunks/s", "ratio", "count")
RESOURCE_KINDS = ("machine", "disk", "slot")
#: הערכה (לא נמדדה): דיווח התקדמות מגיע כל ~2 ש'; פי חמישה = ארבע-חמש החמצות. נקבע מחדש עם המדידה (פרוסה ב).
MAX_AGE_S = 10.0


def _when(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return None if parsed is None else (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))


def _iso(value: datetime | str | None) -> str | None:
    moment = _when(value)
    return None if moment is None else moment.isoformat(timespec="milliseconds")


def _number(value: object) -> float | int | None:
    """מספר אמיתי, או ``None``. ``bool`` ו-NaN/אינסוף אינם מספר: "True" כתוב כ-1 היה נראה כמו נפח שהתקבל."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def resource(kind: str, key: str) -> dict:
    if kind not in RESOURCE_KINDS or not isinstance(key, str) or not key:
        raise ValueError(f"snapshot: unknown resource {kind!r}/{key!r}")
    return {"kind": kind, "key": key}


def metric(res: dict, name: str, value: object, unit: str, source: str, received_at: datetime | str | None,
           now: datetime | str, *, max_age_s: float = MAX_AGE_S) -> dict:
    """שורת מדד אחת. ``value`` ``None`` (או לא מספר) = "לא דווח": ``quality="missing"``, ו-``received_at`` נשמר אם ידוע."""
    if unit not in UNITS:
        raise ValueError(f"snapshot: unit {unit!r} of {name!r} is not one of {UNITS}")
    num, stamp, taken = _number(value), _when(received_at), _when(now)
    if taken is None:
        raise ValueError("snapshot: 'now' is not a time")
    age = None if stamp is None else (taken - stamp).total_seconds()
    if num is None:
        quality = "missing"
    elif age is None or age < 0:
        quality, age = "clock", None
    else:
        quality = "ok" if age <= max_age_s else "stale"
    return {"resource": res, "name": name, "value": num, "unit": unit, "source": source, "received_at": _iso(stamp),
            "age_s": None if age is None else round(age, 3), "quality": quality}


def freeze(*, rule: str, rule_version: int, round_id: str | None, wave_id: str | None, phase: str | None, event: str,
           taken_at: datetime | str, metrics: list[dict], stream_id: str | None = None, window_s: float | None = None,
           ) -> dict:
    """הצילום. ``resources`` נגזר מהמדדים (כל משאב פעם אחת, לפי סדר הופעה); סדר המדדים נשמר."""
    seen, resources = set(), []
    for row in metrics:
        ident = (row["resource"]["kind"], row["resource"]["key"])
        if ident not in seen:
            seen.add(ident)
            resources.append(row["resource"])
    body = {"v": VERSION, "rule": rule, "rule_version": rule_version, "round_id": round_id, "wave_id": wave_id,
            "stream_id": stream_id, "phase": phase, "event": event, "taken_at": _iso(taken_at), "window_s": window_s,
            "resources": resources, "metrics": metrics}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"id": f"s{VERSION}-{digest[:16]}", **body}


def find(snap: dict, kind: str, key: str, name: str) -> dict | None:
    """שורת המדד של (משאב, שם), או ``None``. צילום ``unreadable``/``missing`` מחזיר ``None`` -- לא נחשב "אין מדד"."""
    for row in snap.get("metrics") or ():
        res = row.get("resource") or {}
        if res.get("kind") == kind and res.get("key") == key and row.get("name") == name:
            return row
    return None


def usable(row: dict | None) -> bool:
    """האם המדד מצדיק החלטה: ``ok`` בלבד. ``stale``/``missing``/``clock``/אין שורה = חוסר ראיה."""
    return row is not None and row.get("quality") == "ok" and row.get("value") is not None


class Ledger:
    """מה שהשרת קיבל מכל (משאב, שם), ומה מזה **התקדמות**. בזיכרון בלבד, כמו `durableseq` (המצב הוא של התהליך; הצילום ששמור
    הוא הזיכרון הארוך). אחרי אתחול של השרת הדיווח הראשון הוא הבסיס, ואינו התקדמות.

    ``observe`` מחזיר את הסיווג: ``first`` (בסיס) / ``progress`` (הערך עלה) / ``same`` (אותו ערך, ``seq`` חדש) / ``duplicate``
    (אותו ``seq``) / ``stale`` (``seq`` קטן, או אתחול ישן) / ``reset`` (אתחול חדש של המכונה: בסיס חדש) / ``regress`` (אותו אתחול
    והערך ירד: המונה אופס בלי אתחול -- בסיס חדש). **רק ``progress`` מוסיף ל-``progress``.** ``stale`` אינו נוגע בכלום,
    כולל ``alive_at`` (דיווח ישן אינו ראיה לחיים); כל השאר מעדכן ``alive_at``."""

    BOOTS = 8                                   # כמו השרת ב-I14: זוכר את 8 האתחולים האחרונים של מכונה

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple, dict] = {}

    def observe(self, res: dict, name: str, value: object, *, boot_id: str | None = None, seq: int | None = None,
                at: datetime | str) -> str:
        num, moment = _number(value), _when(at)
        if num is None or moment is None:
            return "stale"                      # לא קראנו אותו: לא התקדמות, ולא ראיה לחיים
        key = (res["kind"], res["key"], name)
        ordered = isinstance(boot_id, str) and type(seq) is int and seq > 0
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                self._rows[key] = {"value": num, "progress": 0, "boot": boot_id if ordered else None,
                                   "boots": [boot_id] if ordered else [], "seq": seq if ordered else None,
                                   "seen_at": moment, "alive_at": moment}
                return "first"
            if ordered and row["boot"] is not None:
                if boot_id != row["boot"]:
                    if boot_id in row["boots"]:
                        return "stale"
                    row["boots"] = [*row["boots"], boot_id][-self.BOOTS:]
                    row.update(boot=boot_id, seq=seq, value=num, seen_at=moment, alive_at=moment)
                    return "reset"
                if seq < row["seq"]:
                    return "stale"
                if seq == row["seq"]:
                    row["alive_at"] = max(row["alive_at"], moment)
                    return "duplicate"
                row["seq"] = seq
            row["alive_at"] = max(row["alive_at"], moment)
            if num > row["value"]:
                row["progress"] += num - row["value"]
                kind = "progress"
            elif num == row["value"]:
                kind = "same"
            else:
                kind = "regress"
            row["value"], row["seen_at"] = num, moment
            return kind

    def progress(self, res: dict, name: str) -> int | float | None:
        """כמה התקדם הערך מאז שהשרת רואה אותו (בלי הבסיס, כפילויות, ישנים ואיפוסים). ``None`` = לא דווח מעולם."""
        row = self._rows.get((res["kind"], res["key"], name))
        return None if row is None else row["progress"]

    def alive_at(self, res: dict, name: str) -> datetime | None:
        row = self._rows.get((res["kind"], res["key"], name))
        return None if row is None else row["alive_at"]

    def metric(self, res: dict, name: str, unit: str, source: str, now: datetime | str, *,
               max_age_s: float = MAX_AGE_S) -> dict:
        """שורת המדד מהערך האחרון שהתקבל. ``received_at`` = מתי הגיע הדיווח האחרון שהתקבל (לא כפול ולא ישן), גם אם הערך זהה לקודם:
        זו תצפית טרייה. ``alive_at`` נפרד. לא דווח מעולם = ``missing``."""
        with self._lock:
            row = self._rows.get((res["kind"], res["key"], name))
            value, stamp = (None, None) if row is None else (row["value"], row["seen_at"])
        return metric(res, name, value, unit, source, stamp, now, max_age_s=max_age_s)
