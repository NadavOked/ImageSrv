"""#3125 (L12, U5, U6): משפט שהסוכן ידע והיומן של השרת לא -- מגירה שחזרה, ההחלטה להשלים אותה, ודיסק זר בחריץ.

עד כאן כל אחד מהם היה שורה ביומן של הסוכן בלבד: השרת, הקונסולה והמוח ידעו עליו רק אם זנב היומן הגיע בסוף הסבב. הסוכן
(`agent/lib/restore/roundnote.sh`) שם כל משפט כזה בדיווח ההתקדמות שלו, ``round_notes`` -- **כל** המשפטים של העבודה בכל
דיווח, כי דיווח אחד שאבד אינו אמור לאבד משפט. כאן כל משפט נכנס ליומן **פעם אחת**.

**הצורה** (`well_formed`): רשימה של ``{"kind", "key", "text"}``. ``kind`` מרשימה סגורה (`KINDS`) -- סוג שאינו מוכר נזנח,
כי הוא אירוע ביומן שאין לו תרגום ולא חומרה. ``key`` = איזה מופע (מגירה, מחיצה), ו-``text`` = המשפט. איבר פגום נזנח
**לבדו** (טיפוס, ריק, ארוך מדי); רשימה שאינה רשימה אינה נשמרת. לא יותר מ-`MAX_NOTES` איברים, ולא יותר מ-`MAX_TEXT` תווים
למשפט -- משפט ארוך נחתך, ולא נזנח (עיקרון 5: לא מאבדים מה שנאמר).

**הכפילות.** אותו (גל, מחשב, סוג, מפתח) נכתב ליומן פעם אחת: בזיכרון התהליך, ועבור תהליך שהתחיל מחדש -- בבדיקה אחת ביומן
עצמו (`round_id`, `machine`, `event`, `detail`) לפני הכתיבה הראשונה של כל משפט. דיווח שאינו מוסיף משפט חדש אינו נוגע ב-DB.

**לעולם אינו זורק** אל דיווח ההתקדמות: כשל הוא שורת לוג.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from server.db import journal

log = logging.getLogger("imagesrv.round_notes")

#: kind -> אירוע ביומן. התרגום והחומרה ב-`server/console/journal_he.py`.
KINDS = {
    "drawer_back": "drawer_back",              # L12: מגירה שנשלפה חזרה, והמשכפל משלים אותה
    "drawer_back_part": "drawer_back_part",    # U5: ההחלטה להשלים מחיצה, וכמה טווחים
    "drawer_stranger": "drawer_stranger",      # U6: דיסק אחר הוכנס לחריץ ואינו מצטרף
}
MAX_NOTES = 40
MAX_KEY = 120
MAX_TEXT = 300
#: כמה מפתחות "כבר נכתב" נשמרים בזיכרון (גל אחד ממשי הוא עשרות); מעבר לזה מתחילים מחדש, והבדיקה ביומן מונעת כפילות.
MAX_SEEN = 5000

_lock = threading.Lock()
_seen: set[tuple] = set()


def well_formed(raw: object) -> list[dict]:
    """הרשימה התקנית של ``round_notes`` בדיווח. כל איבר פגום נזנח לבדו."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:MAX_NOTES]:
        if not isinstance(item, dict):
            continue
        kind, key, text = item.get("kind"), item.get("key"), item.get("text")
        if (kind not in KINDS or not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY
                or not isinstance(text, str) or not text.strip()):
            continue
        out.append({"kind": kind, "key": key, "text": "".join(c for c in text if c >= " " or c == "\t")[:MAX_TEXT]})
    return out


def _written(conn: sqlite3.Connection, event: str, session_id: str, mac: str, text: str) -> bool:
    return conn.execute("SELECT 1 FROM journal WHERE event = ? AND round_id = ? AND machine = ? AND detail = ?"
                        " LIMIT 1", (event, session_id, mac, text)).fetchone() is not None


def record(conn: sqlite3.Connection, session_id: str, mac: str, raw: object) -> int:
    """כמה משפטים חדשים נכנסו ליומן. לעולם אינו זורק."""
    try:
        written = 0
        for note in well_formed(raw):
            key = (session_id, mac, note["kind"], note["key"])
            with _lock:
                if key in _seen:
                    continue
            event = KINDS[note["kind"]]
            if not _written(conn, event, session_id, mac, note["text"]):     # אחרת: השרת עלה מחדש, והמשפט כבר ביומן
                if journal(conn, event, note["text"], machine=mac, round_id=session_id) is None:
                    continue                    # הדיסק מלא והשורה לא נכתבה: לא "נאמר" -- הדיווח הבא ינסה שוב
                written += 1
            with _lock:
                if len(_seen) >= MAX_SEEN:
                    _seen.clear()
                _seen.add(key)
        return written
    except Exception:                           # noqa: BLE001 -- משפט שלא נכתב אינו מפיל דיווח
        log.exception("round notes of %s in %s were not kept", mac, session_id)
        return 0
