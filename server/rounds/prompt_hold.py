"""‏#2202 — חבר בגל שעומד על שאלה למפעיל מחזיק את המחיצה הראשונה של השולח.

‏#1581 (ברזל 17/10) עצר את השעון של `udp-sender` רק לשאלת SMART: היא נרשמת
בשרת (‏`smart_asks`, שדה `ask` ב-hello). שאר השאלות של משכפל לפני הבייט
הראשון — חלוקת המקום (#1898) ו-RAID ישן (#1808) — נשאלות על המסך בלבד, ולכן
‏`--start-timeout 180` רץ מתחתן: ברזל 25/10 (‏`ses_ca1fb8e9`), השאלה עלתה
שתי שניות אחרי `send_start`, השולח יצא אחרי 180 ("אף מחשב לא הצטרף"), וכל
המגירות סומנו "לא נכתב".

הכרעה (המתאם בסמכות נדב, 03/11): **בלי שדה חדש.** כל שאלה שהסוכן ממתין
עליה לאדם כבר נוסעת ב-hello כ-`waiting_for: operator` + `prompt` (‏#906),
ונשמרת ב-`net_devices.prompt`; ‏hello בלי השדה מנקה אותה (‏`attended` שולח
אחד כזה כשהשאלה נגמרת). לכן השולח ממתין כל עוד לחבר חי בגל, שאינו במצב
סופי, יש `prompt` — כל שאלה למפעיל, במכוון.

אותן תקרות כמו לשאלת SMART (‏`smart_ask`): עוצרים רק עם ראיה שהמכונה חיה
(‏`last_seen` ב-`ASK_HOLD_ALIVE_SECONDS`), ולכל חבר `ASK_HOLD_MAX_SECONDS`
מתחילת הגל — אחריהן **רק הוא** משתחרר מהגל (‏`failed`, ושורת
`prompt_hold_expired`). חבר עם שאלת SMART פתוחה נשאר של `smart_ask`: היומן,
התקרה והניתוח של #1889 (‏`send_held_smart`) אינם משתנים.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from server.db import journal, now_iso, update_one
from server.disks.smart_ask import ASK_HOLD_ALIVE_SECONDS, ASK_HOLD_MAX_SECONDS, _age

PROMPT_EXPIRED_WHY = ("המכונה עמדה על שאלה למפעיל {minutes} דקות מתחילת הגל — "
                      "המכונה שוחררה מהגל, ושאר הגל ממשיך")

#: חברי הגל שעוד יכולים לקבל ועומדים על שאלה למפעיל, בלי שאלת SMART פתוחה.
_OPEN_SQL = (
    "SELECT m.mac, m.state, s.started_at, n.last_seen"
    " FROM session_members m"
    " JOIN sessions s ON s.id = m.session_id"
    " JOIN net_devices n ON n.mac = m.mac"
    " WHERE m.session_id = ? AND n.prompt IS NOT NULL"
    " AND m.state NOT IN ('done', 'failed', 'partial', 'lost')"
    " AND NOT EXISTS (SELECT 1 FROM smart_asks a WHERE a.mac = m.mac AND a.answer IS NULL)"
    " ORDER BY m.mac")


def open_in_wave(conn: sqlite3.Connection, session_id: str,
                 now: datetime | None = None) -> list[str]:
    """ה-MAC של חברי הגל שממתינים עכשיו לאדם, חיים ובתוך התקרה."""
    now = now or datetime.now(timezone.utc)
    waiting = []
    for row in conn.execute(_OPEN_SQL, (session_id,)).fetchall():
        heard, waited = _age(row["last_seen"], now), _age(row["started_at"], now)
        if heard is None or waited is None:       # אין ראיה — אין עצירה
            continue
        if -5 <= heard <= ASK_HOLD_ALIVE_SECONDS and waited <= ASK_HOLD_MAX_SECONDS:
            waiting.append(row["mac"])
    return waiting


def release_overdue(conn: sqlite3.Connection, session_id: str,
                    now: datetime | None = None) -> list[str]:
    """משחרר מהגל כל חבר שעדיין עומד על שאלה `ASK_HOLD_MAX_SECONDS` אחרי
    תחילת הגל. ‏UPDATE מותנה במצב שנקרא — דיווח שהגיע בינתיים מנצח."""
    now = now or datetime.now(timezone.utc)
    why = PROMPT_EXPIRED_WHY.format(minutes=ASK_HOLD_MAX_SECONDS // 60)
    released = []
    for row in conn.execute(_OPEN_SQL, (session_id,)).fetchall():
        waited = _age(row["started_at"], now)
        if waited is None or waited <= ASK_HOLD_MAX_SECONDS:
            continue
        if update_one(conn,
                      "UPDATE session_members SET state = 'failed', error = ?, updated_at = ?"
                      " WHERE session_id = ? AND mac = ? AND state = ?",
                      (why, now_iso(), session_id, row["mac"], row["state"])):
            journal(conn, "prompt_hold_expired", f"{row['mac']} {session_id}")
            released.append(row["mac"])
    return released


def waiting_in_wave(conn: sqlite3.Connection, session_id: str,
                    now: datetime | None = None) -> list[str]:
    """מה שהשולח שואל בכל סבב המתנה: קודם משחררים את מי שחרג, ואז מי נשאר."""
    release_overdue(conn, session_id, now)
    return open_in_wave(conn, session_id, now)
