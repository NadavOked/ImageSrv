"""#2830 — סמני `up=` של ה-initramfs נשמרים לכל אתחול של כל מכונה.

`agent/init` מודד את `/proc/uptime` בנקודות קבועות (`boot.trace.INIT_MARKS`: `init`, `modules`, `nic`,
`link`, `dhcp`, `gui`) ושולח אותן עם הפירור `agent-net`. עד #2830 הן נכתבו רק ליומן של uvicorn, ומי שאין
לו SSH או דיסק של השרת לא יכול היה לקרוא אותן — וזה המספר שמגדיר "גמור" ב-#1702.

**מה נשמר:** שורה אחת לכל פירור `agent-net` שהסמנים שלו תקינים (`boot.trace.parse_marks` — ערך פגום
דוחה את **כל** הרשימה, ולעולם אינו נשמר כ-0). שניות `/proc/uptime` כפי שהמכונה מדדה אותן, NULL = הנקודה
לא הושגה או לא נצפתה (למשל בלי `nic`: ממשק לא הופיע תוך 2 שניות) — אינו 0. זהים לשורה האחרונה של אותה
מכונה (`agent-net` שנשלח פעמיים באותו אתחול) — לא נכתבים שוב. `KEEP` אחרונות למכונה בכל הכנסה.

**מה לא נשמר:** הזמן עד ה-hello. הסוכן אינו שולח אותו ב-`up=`; `boot_step_times` מודד את
`agent-net → agent-start → agent-hello` בשעון השרת.

לא נכתב ליומן הפעולות: אתחול של כיתה שלמה היה שלושים שורות בכל בוקר, והנתון נקרא ממגירת המכונה.
לעולם אינו זורק.
"""

from __future__ import annotations

import logging
import sqlite3

from boot.trace import INIT_MARKS

from server.db import _write_lock, now_iso, writing
from server.learn import boot as learn_boot

log = logging.getLogger("imagesrv.boot_marks")

KEEP = 20
#: השרשרת שמודדים בה "כמה ארך כל שלב". `gui` מחוץ לה: הוא מופעל במקביל, לפני DHCP,
#: ואינו שלב שקדמו לו הקודמים.
CHAIN: tuple[str, ...] = ("init", "modules", "nic", "link", "dhcp")
_COLUMNS = tuple(f"{key}_s" for key in INIT_MARKS)


def record(conn: sqlite3.Connection, mac: str, marks: dict[str, float]) -> bool:
    """שומר אתחול אחד. מחזיר האם נכתבה שורה חדשה — ראיה חיובית, לא היעדר חריגה."""
    if not marks or any(key not in INIT_MARKS for key in marks):
        return False
    values = tuple(marks.get(key) for key in INIT_MARKS)
    try:
        with _write_lock, writing(conn):
            last = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM boot_marks WHERE mac = ?"
                                " ORDER BY rowid DESC LIMIT 1", (mac,)).fetchone()
            if last is not None and tuple(last) == values:
                return False
            conn.execute(f"INSERT INTO boot_marks (mac, at, version, {', '.join(_COLUMNS)})"
                         f" VALUES (?, ?, ?, {', '.join('?' * len(_COLUMNS))})",
                         (mac, now_iso(), learn_boot.version(), *values))
            conn.execute("DELETE FROM boot_marks WHERE mac = ? AND rowid NOT IN (SELECT rowid FROM"
                         " boot_marks WHERE mac = ? ORDER BY rowid DESC LIMIT ?)", (mac, mac, KEEP))
        return True
    except Exception:                  # noqa: BLE001 — אבחון שמפיל פירור הוא נזק
        log.exception("could not store boot marks for %s", mac)
        return False


def _took(marks: dict[str, float | None]) -> dict[str, float | None]:
    """כמה ארך כל שלב בשרשרת: מהסמן הקודם **שהושג**. הסמן הראשון נמדד מהעלאת הקרנל (uptime 0).

    סדר הפוך (שעון שקפץ אחורה) הוא "לא ידוע", לא מספר שלילי."""
    out: dict[str, float | None] = {}
    prev = 0.0
    for key in CHAIN:
        value = marks.get(key)
        if value is None:
            continue
        out[key] = round(value - prev, 3) if value >= prev else None
        prev = value
    return out


def history(conn: sqlite3.Connection, mac: str, limit: int = 10) -> list[dict]:
    """האתחולים האחרונים של מכונה, החדש ראשון. `net_s` = `dhcp` — מתי הרשת עלתה מאז העלאת הקרנל."""
    rows = conn.execute(f"SELECT at, version, {', '.join(_COLUMNS)} FROM boot_marks WHERE mac = ?"
                        " ORDER BY rowid DESC LIMIT ?", (mac, limit)).fetchall()
    out = []
    for row in rows:
        marks = {key: row[f"{key}_s"] for key in INIT_MARKS if row[f"{key}_s"] is not None}
        out.append({"at": row["at"], "version": row["version"], "net_s": marks.get("dhcp"),
                    "marks": marks, "took": _took(marks)})
    return out
