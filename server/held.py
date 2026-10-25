"""‏#2031 — משכפל שמחזיק את התוצאות עד "כבה" (#2024): "דלוק — מחכה ללחיצה".

אחרי #2024 משכפל שיש בו דיסק עם בעיה נשאר דלוק עד לחיצה על "כבה", **בלי hello**
— בכוונה: hello היה הופך אותו ל"נראה" לגל חדש, וה-WoL החוזר (#1721) לא היה
מעיר ולא מכשיל את הסבב בשמו. בלי שום דיווח הקונסולה ציירה אותו "לא מחובר".

הסוכן (‏`agent/lib/hold.sh`, ‏`held_report`) שולח בזמן ההחזקה דיווח קל בנתיב
ה-progress, **בלי סבב ובלי משימה** — ‏`{"mac", "state": "held", "problems": [...]}`
— בקצב ההמתנה (‏`POLL_MAX`, 15ש'). כאן הוא נרשם ב-`held_at`/`held_json` ובראיית
החיים של progress (‏`progress_seen`, ‏#1839) — **לא** ב-`last_seen`: המשכפל אינו
מצטרף לגל ואינו "נראה" ל-`room._unseen`. התשובה נושאת את `power_action` של
הקונסולה (‏#980) — אותו ערוץ כמו ה-hello, ולכן "כבה" מהקונסולה מגיע גם להחזקה.

"מוחזק" = דיווח held טרי (‏`FRESH_SECONDS`). hello מצטרף (המכונה חזרה לתור)
ומסירת כיבוי מנקים אותו; מכונה שכבתה מהחשמל מתיישנת לבד.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import NET_SEEN_MIN_INTERVAL_SECONDS, _settle, _write_lock, writing

#: ארבע פעימות של 15ש' — דיווח אחד או שניים שאבדו אינם "כבה".
FRESH_SECONDS = 60
#: גבולות קלט: מגירות במשכפל (‏`drawer_count` ≤ 8) עם מרווח, ומשפט סיבה אחד.
MAX_PROBLEMS = 16
MAX_TEXT = 300
LEVELS = ("r", "o")


def well_formed(problems: object) -> list[dict]:
    """רשימת הבעיות כפי שהסוכן כתב אותן ב-`end.problems` (‏`<disk>|<r|o>|<reason>`).
    פריט פגום נזנח; רשימה שאינה נקראת = ריקה — המצב "מוחזק" נשאר, בלי פירוט."""
    if not isinstance(problems, list):
        return []
    out = []
    for item in problems[:MAX_PROBLEMS]:
        if not isinstance(item, dict):
            continue
        disk, level, reason = item.get("disk"), item.get("level"), item.get("reason")
        if not isinstance(disk, str) or level not in LEVELS or not isinstance(reason, str):
            continue
        out.append({"disk": disk[:32], "level": level, "reason": reason[:MAX_TEXT]})
    return out


def _stamp(text: str | None) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return stamp if stamp is not None and stamp.tzinfo is not None else None


def note(conn: sqlite3.Connection, mac: str, problems: list[dict],
         now: datetime | None = None) -> None:
    """רושם "מוחזק". בחניקה של #136: אותן בעיות וחותמת צעירה מ-15ש' — אין כתיבה."""
    now = now or datetime.now(timezone.utc)
    body = json.dumps(problems, ensure_ascii=False)
    row = conn.execute("SELECT held_at, held_json FROM net_devices WHERE mac = ?",
                       (mac,)).fetchone()
    last = _stamp(row["held_at"]) if row else None
    if (row is not None and row["held_json"] == body and last is not None
            and 0 <= (now - last).total_seconds() < NET_SEEN_MIN_INTERVAL_SECONDS):
        return
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("UPDATE net_devices SET held_at = ?, held_json = ? WHERE mac = ?",
                      (now.isoformat(timespec="seconds"), body, mac))


def clear(conn: sqlite3.Connection, mac: str) -> None:
    """המכונה כבר אינה מחזיקה (hello מצטרף / כיבוי נמסר). ‏SELECT קודם — רוב ה-hello אינם כותבים."""
    row = conn.execute("SELECT held_at FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    if row is None or row["held_at"] is None:
        return
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("UPDATE net_devices SET held_at = NULL, held_json = NULL WHERE mac = ?",
                     (mac,))


def view(held_at: str | None, held_json: str | None,
         now: datetime | None = None) -> dict | None:
    """מה שהקונסולה מציגה: ‏None = לא מוחזק (או דיווח ישן/פגום — לא ראיה שהוא דלוק),
    אחרת ‏`{"at", "problems"}`. חותמת מהעתיד אינה טרייה (‏#774)."""
    at = _stamp(held_at)
    if at is None:
        return None
    age = (now or datetime.now(timezone.utc)) - at
    if not timedelta(0) <= age <= timedelta(seconds=FRESH_SECONDS):
        return None
    try:
        problems = well_formed(json.loads(held_json or "[]"))
    except ValueError:
        problems = []
    return {"at": held_at, "problems": problems}


def is_held(conn: sqlite3.Connection, mac: str) -> bool:
    row = conn.execute("SELECT held_at, held_json FROM net_devices WHERE mac = ?",
                       (mac,)).fetchone()
    return row is not None and view(row["held_at"], row["held_json"]) is not None
