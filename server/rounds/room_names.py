"""‏#2786 (נדב 09/11) — שם מחשב לכל דיסק יעד בסבב חדר השיכפול.

הקונסולה מחשבת את השמות (תבנית או פרטני, לפי סדר המשכפל ואז SATA) ומציגה
אותם בתצוגה המקדימה; מה שנשלח הוא **השם לכל `(mac, port)`**, בדיוק כמו
שהוצג — כך מה שנכתב לדיסק הוא מה שהמפעיל ראה, ודיסק שנכשל שומר את השם
שלו (החריץ שלו, לא מקום ברצף). השרת אינו מחשב תבנית; הוא **מאמת**:
כל שם בכלל של `hostname_rule.computer_name_refusal`, שם לכל דיסק יעד
ורק להם. הבחירה נשמרת ב-`room_rounds.hostnames_json`, כמו `driver_models`.

```json
{"names": [{"mac": "aa:bb:cc:00:00:21", "port": 1, "name": "100-INS"}],
 "os": ["windows"]}
```

חסר / ``null`` = בלי שמות — הסבב בדיוק כמו לפני ה-Issue (המתג כבוי: השם
שבאימג' נשאר). ‏``os`` רק לאימג' שיש בו גם Windows וגם לינוקס; ``null`` =
שתיהן (ברירת המחדל במסך).
"""

from __future__ import annotations

import json
import re
import sqlite3

from server.agent import registry
from server.core import hostname_rule
from server.db import get_setting

#: ההגדרות (לשונית "שמות מחשב"): ברירות המחדל לחלון "התחל סבב".
PAD_SETTING = "clone_name_pad"
INSTRUCTOR_SETTING = "clone_name_instructor"
PAD_DEFAULT, PAD_MIN, PAD_MAX = 2, 1, 4
INSTRUCTOR_DEFAULT = "INS"
INSTRUCTOR_MAX = 8
_INSTRUCTOR_OK = re.compile(rf"[A-Za-z0-9]{{1,{INSTRUCTOR_MAX}}}")

OS_CHOICES = ("windows", "linux")

MULTI_WAVE_HE = ("שמות מחשב ניתנים רק לסבב של גל אחד: מספר הדיסקים בסך הכול גדול "
                 "ממספר הדיסקים שסומנו, ודיסקים של גל נוסף היו מקבלים את אותם שמות")


def parse_pad(raw: object) -> int | None:
    """ריפוד 1–4 ספרות, או ``None``."""
    text = str(raw).strip() if raw is not None else ""
    if not text.isdigit() or not PAD_MIN <= int(text) <= PAD_MAX:
        return None
    return int(text)


def parse_instructor(raw: object) -> str | None:
    """סיומת המרצה: אותיות באנגלית וספרות, 1–8 תווים, או ``None``.

    בלי מקף: היא מצטרפת אחרי ``-`` של התבנית, ושם שנגמר במקף אסור.
    התקרה (8) משאירה לקידומת לפחות 6 תווים מתוך ה-15 (``-`` + 8)."""
    return raw if isinstance(raw, str) and _INSTRUCTOR_OK.fullmatch(raw) else None


def pad(conn: sqlite3.Connection) -> int:
    """הערך שבתוקף — ברירת המחדל כשלא נקבע או כשהשמור פגום."""
    return parse_pad(get_setting(conn, PAD_SETTING)) or PAD_DEFAULT


def instructor(conn: sqlite3.Connection) -> str:
    return parse_instructor(get_setting(conn, INSTRUCTOR_SETTING)) or INSTRUCTOR_DEFAULT


def settings_view(conn: sqlite3.Connection) -> dict:
    """מה שהקונסולה צריכה כדי לחשב תבנית — גם למפעיל הפצה (‏`/settings` הוא admin)."""
    return {"pad": pad(conn), "instructor": instructor(conn),
            "max_len": hostname_rule.HOSTNAME_MAX}


def dual_os(manifest: dict) -> bool:
    """באימג' יש גם מחיצת Windows וגם מחיצת לינוקס — רק אז שואלים לאן לכתוב."""
    roles = {p.get("role") for p in manifest.get("partitions") or [] if isinstance(p, dict)}
    return {"windows", "linux"} <= roles


def validate(raw, selected: list[dict], target_drives: int, manifest: dict) -> dict | None:
    """הבחירה בצורתה הקנונית, ``None`` כשאין שמות, או ``ValueError`` בשמו."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("names"), list):
        raise ValueError("בחירת שמות המחשב אינה תקינה")
    targets = {(item["mac"], port) for item in selected for port in item["ports"]}
    if target_drives > len(targets):
        raise ValueError(MULTI_WAVE_HE)
    chosen: dict[tuple[str, int], str] = {}
    for item in raw["names"]:
        if not isinstance(item, dict):
            raise ValueError("בחירת שמות המחשב אינה תקינה")
        mac = registry.normalize_mac(str(item.get("mac", "")))
        port = item.get("port")
        if mac is None or isinstance(port, bool) or not isinstance(port, int):
            raise ValueError("בחירת שמות המחשב אינה תקינה")
        if (mac, port) in chosen:
            raise ValueError(f"לדיסק SATA {port - 1} ב־{mac} נשלחו שני שמות")
        if (mac, port) not in targets:
            raise ValueError(f"שם מחשב לדיסק SATA {port - 1} ב־{mac}, שאינו בין דיסקי היעד")
        refused = hostname_rule.computer_name_refusal(item.get("name"))
        if refused is not None:
            raise ValueError(refused)
        chosen[(mac, port)] = item["name"]
    missing = sorted(targets - set(chosen))
    if missing:
        mac, port = missing[0]
        raise ValueError(f"חסר שם מחשב ל-{len(missing)} דיסקים (הראשון: SATA {port - 1} ב־{mac})")
    os_raw = raw.get("os")
    if os_raw is not None:
        if not dual_os(manifest):
            raise ValueError("בחירת מערכת ההפעלה לשם המחשב — רק לאימג' עם Windows ולינוקס")
        if (not isinstance(os_raw, list) or not os_raw
                or any(o not in OS_CHOICES for o in os_raw) or len(set(os_raw)) != len(os_raw)):
            raise ValueError("סמנו לפחות מערכת הפעלה אחת לשם המחשב")
        os_raw = [o for o in OS_CHOICES if o in os_raw]
        if os_raw == list(OS_CHOICES):
            os_raw = None      # שתיהן = מה שהסוכן עושה ממילא באימג' כזה (#107)
    return {"names": [{"mac": mac, "port": port, "name": chosen[(mac, port)]}
                      for mac, port in sorted(chosen)],
            "os": os_raw}


def _loaded(round_row: sqlite3.Row | None) -> dict | None:
    if round_row is None or round_row["hostnames_json"] is None:
        return None
    return json.loads(round_row["hostnames_json"])


def view(round_row: sqlite3.Row) -> dict | None:
    return _loaded(round_row)


def for_mac(round_row: sqlite3.Row | None, mac: str) -> dict[str, str] | None:
    """‏`session.hostnames` של המכונה: ‏``{"<port>": name}``. ‏``None`` = הסבב בלי שמות."""
    chosen = _loaded(round_row)
    if chosen is None:
        return None
    return {str(item["port"]): item["name"] for item in chosen["names"] if item["mac"] == mac}


def os_for(round_row: sqlite3.Row | None) -> list[str] | None:
    """‏`session.hostname_os`: ``null`` = כל מערכת שבאימג' (כמו לפני ה-Issue)."""
    chosen = _loaded(round_row)
    return None if chosen is None else chosen.get("os")
