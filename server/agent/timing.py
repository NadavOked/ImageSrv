"""זמני ההמתנה והכיבוי האוטומטי כהגדרות בקונסולה (#2056, #2059; מוקאפ 05, נדב 24/10).

שלושה ערכים שהיו קבועים בקוד:

- ``lost_wait_seconds`` (#2056) — כמה זמן הסבב מחכה למשכפל שהפסיק לדווח לפני
  שהוא מסומן "אבד". ברירת מחדל 180ש' (``room.LOST_SECONDS``), **מותר רק להגדיל**,
  עד 30 דקות. ערך אחד שולט על השרת (``room._mark_lost``), על ``--retries-until-drop``
  של השולח ועל הסוכן (שמקבל אותו ב-hello).
- ``gui_idle_poweroff_seconds`` (#2059) — כיבוי אוטומטי בגואי הקטן כשאין פעילות.
  ברירת מחדל 300ש' (``IDLE_POWEROFF_S`` ב-``agent/lib/core/idle.sh``), טווח 1–60 דקות.
- ``gui_end_poweroff_seconds`` (#2059) — ספירה לכיבוי בסוף משימה. ברירת מחדל 20ש'
  (``END_POWEROFF_S``), טווח 10–300ש'.

הגדרה אחת לכל סוגי המכונות (משכפל, מחשב בנייה, שחזור) — כמו במוקאפ. "מחשב עם דיסק
בעייתי לא נכבה לבד" (#2024) הוא כלל קבוע בסוכן ואינו כאן.

**ערך שנשמר ולא נקרא בחזרה אינו "הוחל" (עיקרון 5).** הסוכן מחזיר ב-hello את מה שהוא
באמת מחיל (``applied_timing``); השרת שומר את זה לכל מכונה, והקונסולה מראה ליד כל
ערך "הוחל ב-N מתוך M". מכונה שלא דיווחה — לא נספרת כמי שהוחל אצלה.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone

from server.db import _settle, _write_lock, get_setting, writing

log = logging.getLogger("imagesrv.agent_timing")

LOST = "lost_wait_seconds"
IDLE = "gui_idle_poweroff_seconds"
END = "gui_end_poweroff_seconds"
KEYS = (LOST, IDLE, END)

#: (ברירת מחדל, מינימום, מקסימום) בשניות. ‏LOST: התאום של ``room.LOST_SECONDS``;
#: ‏IDLE/END: התאומים של ``IDLE_POWEROFF_S`` / ``END_POWEROFF_S`` ב-``agent/lib/core/idle.sh``.
RANGES: dict[str, tuple[int, int, int]] = {
    LOST: (180, 180, 30 * 60),
    IDLE: (300, 60, 60 * 60),
    END: (20, 10, 300),
}
LABELS = {LOST: "המתנה למחשב שהתנתק באמצע שיכפול",
          IDLE: "כיבוי כשאין פעילות",
          END: "ספירה לכיבוי בסוף משימה"}
#: מי נספר כ"מחובר" לכל ערך: הערך הראשון חל על משכפלים בלבד, האחרים גם על מחשב הבנייה.
ROLES = {LOST: ("cloner",), IDLE: ("cloner", "build"), END: ("cloner", "build")}
#: ‏hello האחרון בן פחות מזה = מחובר (אותו חלון של "נראו ב-90 השניות האחרונות" במוניטור).
CONNECTED_SECONDS = 90

#: ‏``--retries-until-drop`` של 200 הוא כ-190ש' (המדידה ב-``sender.py``, #437). כדי שהשולח
#: לא יזרוק מקבל לפני שהשרת מכריז עליו אבוד, התקרה גדלה באותו יחס.
RETRIES_PER_SECOND = 200 / 190


def parse(key: str, raw: object) -> int | None:
    """שלם בטווח של ``key`` — או ``None``. ‏``bool`` אינו מספר, ואפס מוביל נדחה
    (‏ash קורא ‎020 כאוקטלי; אותו כלל של ``minfree.parse_gb``)."""
    _default, low, high = RANGES[key]
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif (isinstance(raw, str) and raw.isascii() and raw.isdigit()
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 5):
        value = int(raw)
    else:
        return None
    return value if low <= value <= high else None


def seconds(conn: sqlite3.Connection, key: str) -> int:
    """ההגדרה, או ברירת המחדל. חסרה = לא נקבעה (ברירת מחדל, בשקט); פגומה = ברירת
    המחדל **עם שורת יומן** — לעולם לא "אינסוף" ולא 0."""
    default = RANGES[key][0]
    raw = get_setting(conn, key)
    if raw is None:
        return default
    value = parse(key, raw)
    if value is None:
        log.warning("setting %s=%r is outside %d-%d -- using %d", key, raw,
                    RANGES[key][1], RANGES[key][2], default)
        return default
    return value


def lost_seconds(conn: sqlite3.Connection) -> int:
    return seconds(conn, LOST)


def retries_until_drop(base: int, lost: int) -> int:
    """תקרת הנטישה של ``udp-sender``: לפחות ``base``, ובהגדלת ההמתנה — באותו יחס."""
    return max(base, math.ceil(lost * RETRIES_PER_SECOND))


def hello_fields(conn: sqlite3.Connection) -> dict[str, int]:
    """השדות שנוספים לכל תשובת hello של מכונה רשומה (ממשק 3)."""
    return {"lost_wait_seconds": lost_seconds(conn),
            "idle_poweroff_seconds": seconds(conn, IDLE),
            "end_poweroff_seconds": seconds(conn, END)}


#: שם השדה ב-hello/``applied_timing`` לכל מפתח הגדרה.
WIRE = {LOST: "lost_wait_seconds", IDLE: "idle_poweroff_seconds", END: "end_poweroff_seconds"}


def well_formed(raw: object) -> dict[str, int] | None:
    """מה שהסוכן אמר שהוא מחיל. שדה חסר/מחוץ לטווח נזנח (= "לא הוחל"); ``None`` =
    אין מה לרשום (השדה חסר או אינו אובייקט)."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in KEYS:
        value = parse(key, raw.get(WIRE[key]))
        if value is not None:
            out[WIRE[key]] = value
    return out


def note_applied(conn: sqlite3.Connection, mac: str, raw: object) -> None:
    """רושם את ``applied_timing`` של המכונה. סוכן שלא שלח את השדה (ישן) משאיר את
    הרישום הקודם; סוכן ששלח — דורס. ‏SELECT קודם: רוב ה-hello אינם משנים דבר."""
    applied = well_formed(raw)
    if applied is None:
        return
    body = json.dumps(applied, sort_keys=True)
    row = conn.execute("SELECT applied_timing FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    if row is None or row["applied_timing"] == body:
        return
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("UPDATE net_devices SET applied_timing = ? WHERE mac = ?", (body, mac))


def _recent(stamp: str | None, now: datetime) -> bool:
    try:
        seen = datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return False
    if seen is None or seen.tzinfo is None:
        return False
    return 0 <= (now - seen).total_seconds() <= CONNECTED_SECONDS


def applied_counts(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, dict[str, int]]:
    """לכל הגדרה: ``{"applied": N, "connected": M}`` — M המכונות המחוברות מהתפקידים
    הרלוונטיים, N מביניהן שהחזירו בדיוק את הערך שבתוקף. הערך שבתוקף הוא מה שהשרת
    מגיש עכשיו (כולל ברירת מחדל), כך שמכונה שמחילה את ברירת המחדל נספרת."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT d.last_seen, d.applied_timing, g.role FROM net_devices d"
        " JOIN machines m ON m.mac = d.mac JOIN groups g ON g.id = m.group_id").fetchall()
    live = [r for r in rows if _recent(r["last_seen"], now)]
    out: dict[str, dict[str, int]] = {}
    for key in KEYS:
        want = seconds(conn, key)
        mine = [r for r in live if r["role"] in ROLES[key]]
        done = 0
        for r in mine:
            try:
                got = (json.loads(r["applied_timing"]) if r["applied_timing"] else {}).get(WIRE[key])
            except ValueError:
                got = None
            done += 1 if got == want else 0
        out[key] = {"applied": done, "connected": len(mine)}
    return out
