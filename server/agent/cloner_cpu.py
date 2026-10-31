"""‏#1885 — מעבד, throttle וטמפרטורה במשכפל בזמן גל. גל עם throttle שעלה לא נכנס ללמידה.

ב-i5-650 (חומרת הייצור, ‏HP 8100) המעבד הוא צוואר הבקבוק כשיש כמה מגירות (‏zstd ו-partclone לכל אחת).
‏`probe.py` דורס את הטמפרטורה בכל דגימה, ו-throttle לא נקרא בכלל.

**מה מגיע** (‏`agent/lib/core/datareport.sh: machine_extra_json`): בדיווח **הסופי** של המכונה בסבב (מצב
‏`done`/`failed`/`partial`) — ‏`"cpu": {"busy_pct", "iowait_pct", "throttle_delta", "temp_max_c", "seconds"}`:
מרגע שהמכונה התחילה לכתוב (‏`state=writing`) ועד הסוף — ‏`/proc/stat` (עסוק = לא idle ולא iowait),
סכום `thermal_throttle/core_throttle_count` של כל הליבות (אחרי − לפני), והטמפרטורה **המרבית** שנדגמה בכל
דיווח (‏thermal zones ו-coretemp/k10temp). מקור שאינו קיים — ‏`null`, לא 0 ("אין throttle" ≠ "לא נקרא").

**בשרת:** ‏`session_members.cpu_json` (‏`well_formed`), ‏`wave_rates.delivered[mac].cpu` (‏`drive_rates.record_wave`),
ו-**`throttled`**: ‏`throttle_delta > 0` — ‏`drive_rates._from_previous` (‏#1842) **מדלג** על הגל הזה למכונה הזו,
ויומן **`cloner_throttled`** (‏"המשכפל התחמם/הואט — בדקו אוורור"). מידע והגנה על הלמידה בלבד — שום גל אינו
נעצר. לעולם אינו זורק.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from server.db import journal

log = logging.getLogger("imagesrv.cloner_cpu")

#: (מפתח, מקסימום סביר). מעל — ‏null (לא נמדד נכון), לא חיתוך.
FIELDS = (("busy_pct", 100), ("iowait_pct", 100), ("throttle_delta", 10 ** 9),
          ("temp_max_c", 150), ("seconds", 10 ** 7))


def well_formed(raw: object) -> dict | None:
    """‏None — השדה חסר/אינו אובייקט. אחרת כל מפתח: מספר שלם בטווח, או ‏None."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key, top in FIELDS:
        v = raw.get(key)
        out[key] = v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= top else None
    return out


def throttled(cpu: object) -> bool:
    """ראיה חיובית בלבד: מונה שעלה. לא נקרא = False (הלמידה ממשיכה כמו לפני #1885)."""
    return isinstance(cpu, dict) and isinstance(cpu.get("throttle_delta"), int) \
        and cpu["throttle_delta"] > 0


def note_final(conn: sqlite3.Connection, mac: str, session_id: str, cpu: dict | None) -> None:
    """במעבר הטרמינלי של המכונה (‏`reports.ingest`): יומן כשה-throttle עלה. לעולם אינו זורק."""
    try:
        if throttled(cpu):
            temp = "לא נמדדה" if cpu.get("temp_max_c") is None else f"{cpu['temp_max_c']}°C"
            journal(conn, "cloner_throttled",
                    f"{mac} in {session_id}: throttle +{cpu['throttle_delta']}, max {temp},"
                    f" busy {cpu.get('busy_pct')}% — המשכפל התחמם/הואט (throttle) — בדקו אוורור;"
                    " הגל לא נכנס ללמידת הקצב")
    except Exception:             # noqa: BLE001
        log.exception("cpu note of %s failed", mac)


def history(conn: sqlite3.Connection, mac: str, limit: int = 5) -> list[dict]:
    """הגלים האחרונים של המכונה שנשאו `cpu` — למגירת המכונה."""
    out = []
    for row in conn.execute("SELECT session_id, updated_at, cpu_json FROM session_members"
                            " WHERE mac = ? AND cpu_json IS NOT NULL ORDER BY updated_at DESC LIMIT ?",
                            (mac, limit)):
        try:
            cpu = json.loads(row["cpu_json"])
        except (TypeError, ValueError):
            continue
        out.append({"session_id": row["session_id"], "at": row["updated_at"], **(cpu or {}),
                    "throttled": throttled(cpu)})
    return out
