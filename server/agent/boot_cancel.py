"""‏#1576 -- משימה שהוזמנה מהגואי של מחשב הבנייה אינה שורדת אתחול שקדם לה.

נדב 17/10: ‏tsk_d58c הוזמנה בגואי ב-08:56 ולא נלקחה (#1575); המכונה אותחלה
ב-09:02, והקליטה **התחילה לבד** -- בלי שם משתמש ובלי סיסמה, מול מסך שאיש
לא עמד מולו. "לא אמור לקרות מקרה כזה".

הכלל: משימה עם `origin=build_gui` שעדיין `pending` -- כלומר הסוכן לא התחיל
אותה -- מבוטלת ברגע שהמכונה מבקשת את תפריט האתחול. **בקשת התפריט היא
האתחול** (‏hello.make_resolver, ‏#75), ולכן זה הרגע, ולא ה-hello הראשון.
משימה מהקונסולה אינה נוגעת כאן: מפעיל שהזמין קליטה ממחשב כבוי מצפה בדיוק
לכך שהאתחול יביא אותה.

הפצה ישירה היא משימה **וגם** סבב חדר עם גל פתוח, שהמשכפלים ממתינים בו:
ביטול המשימה לבדה היה משאיר גל שאיש לא ישדר בו. לכן הסבב נסגר והגל נסגר
באותו מקום -- אותה תוצאה כמו סגירת הסבב מהקונסולה (‏room.close_round),
בלי עצירת משדר: המקור הוא מחשב הבנייה, ואין udp-sender בשרת.
"""

from __future__ import annotations

import logging
import sqlite3

from server.db import _write_lock, journal, now_iso, writing
from server.rounds.direct import DIRECT_SEND
from server.rounds.sessions import SessionError
from server.core.tasks import ORIGIN_BUILD_GUI

log = logging.getLogger("imagesrv.boot_cancel")

#: הסיבה, כפי שהיא נשמרת ב-`tasks.error` ומוצגת לצד המשימה.
REASON = "בוטלה: המכונה אותחלה לפני תחילת המשימה"


def cancel_gui_tasks_on_boot(conn: sqlite3.Connection, store, mac: str) -> list[str]:
    """מבטל את משימות הגואי של ``mac`` שעוד לא התחילו. מחזיר את המזהים שבוטלו."""
    rows = conn.execute(
        "SELECT id, type FROM tasks WHERE mac = ? AND state = 'pending' AND origin = ?",
        (mac, ORIGIN_BUILD_GUI),
    ).fetchall()
    cancelled: list[str] = []
    for row in rows:
        wave = None
        with _write_lock, writing(conn):
            # מותנה ב-pending: סוכן שכבר התחיל (running) אינו נעצר כאן.
            cur = conn.execute(
                "UPDATE tasks SET state = 'cancelled', error = ?, updated_at = ?"
                " WHERE id = ? AND state = 'pending'",
                (REASON, now_iso(), row["id"]),
            )
            if cur.rowcount != 1:
                continue
            if row["type"] == DIRECT_SEND:
                round_row = conn.execute(
                    "SELECT id, wave_session_id FROM room_rounds"
                    " WHERE source_task_id = ? AND state = 'active'", (row["id"],),
                ).fetchone()
                if round_row is not None:
                    conn.execute(
                        "UPDATE room_rounds SET state = 'closed', closed_at = ?"
                        " WHERE id = ? AND state = 'active'", (now_iso(), round_row["id"]),
                    )
                    wave = round_row["wave_session_id"]
        if wave and store is not None:
            try:
                store.close(wave, "")
            except SessionError:
                log.warning("boot cancel of %s: its wave %s was already gone", row["id"], wave)
        journal(conn, "task_boot_cancel", f"{row['id']} {mac} ({row['type']}): {REASON}")
        cancelled.append(row["id"])
    return cancelled
