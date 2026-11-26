"""#2985 — סבב ישיר שנסגר סוגר את **כל** מה שלו: המשימה של המקור, הגל והסבב.

הקונסולה מציגה "פעיל" לפי שלוש שורות נפרדות: משימת המקור (`pending`/`running`), הגל
(`open`/`running`) ושורת הסבב (`active`). עד #2985 כל נתיב סגר את חלקו בלבד:

  * המקור דיווח `failed` — המשימה `failed`, אבל הגל והסבב נשארו פתוחים (`tick` ממתין
    למניפסט או לדיווחי מקבלים שלא יגיעו). #2906 סגר רק את דחיית המניפסט.
  * הגל נגמר והסבב `closed`, והמקור נעלם בלי לדווח — המשימה `running` לעד.
  * הסבב `failed` בנתיב שלא קרא ל-`cancel_source_task` (כשל הכנת האינדקס, #3137): המשימה נשארה `pending`.

כל אלה נסגרים כאן, מקום אחד, בכל דופק (`room.tick`), ובשם: המשימה מקבלת `error` שאומר למה.
"""

from __future__ import annotations

from datetime import datetime

from server.db import _write_lock, journal, now_iso, SqlConnection, writing
from server.rounds import direct

#: כמה שניות אחרי שהסבב נסגר (`closed` או `failed`) ממתינים לדיווח של המקור לפני שסוגרים את משימתו.
#: המקבלים מדווחים לפני המקור לעיתים קרובות, והמקור שמתבשר על כשל הגל מדווח בעצמו; אחרי התקרה — לא יגיע.
SOURCE_SILENT_S = 120

SOURCE_FAILED = "מחשב הבנייה דיווח כשל במקור"
SOURCE_GONE = "משימת המקור נסגרה בלי שהסבב נסגר"
ROUND_FAILED = "הסבב נכשל, ולכן המקור נעצר"
SOURCE_SILENT = "מחשב הבנייה לא דיווח סיום אחרי שהסבב נסגר"


def _fail_active_rounds(conn: SqlConnection, store) -> None:
    """סבב פעיל שמשימת המקור שלו נסגרה בכשל או בביטול: הגל והסבב נכשלים בשם הסיבה.
    לא נוגעים כשכבר יש חבר שסיים לכתוב — אז הגל נסגר כרגיל והסבב נשאר שלם (`_finish_wave`)."""
    rows = conn.execute(
        "SELECT r.id AS round_id, t.id AS task_id, t.state AS task_state, t.error AS task_error"
        " FROM room_rounds r JOIN tasks t ON t.id = r.source_task_id"
        " WHERE r.source_kind = ? AND r.state = 'active' AND t.state IN ('failed', 'cancelled')",
        (direct.BUILD_DISK,),
    ).fetchall()
    for row in rows:
        round_row = conn.execute("SELECT * FROM room_rounds WHERE id = ?", (row["round_id"],)).fetchone()
        wave = round_row["wave_session_id"]
        if conn.execute("SELECT 1 FROM session_members WHERE session_id = ?"
                        " AND state IN ('done', 'partial') LIMIT 1", (wave,)).fetchone():
            continue
        why = row["task_error"] or (SOURCE_FAILED if row["task_state"] == "failed" else SOURCE_GONE)
        direct.fail_round_of_task(conn, store, row["task_id"], why)


def _close_source_task(conn: SqlConnection, task_id: str, round_row, state: str, why: str) -> None:
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE tasks SET state = ?, error = ?, updated_at = ? WHERE id = ?"
            " AND state IN ('pending', 'running')", (state, why, now_iso(), task_id))
        closed = cur.rowcount == 1
    if closed:
        journal(conn, "direct_source_closed", f"{task_id} {state}: {why}",
                machine=round_row["source_mac"], round_id=round_row["id"])


def _close_source_tasks(conn: SqlConnection, now: datetime) -> None:
    """סבב שנסגר או נכשל ומשימת המקור שלו עוד פתוחה. המקור לומד שהגל נגמר מהמשימה שלו עצמה
    (`task.direct.session_state`, ו-`chunk.error` במנוע החתיכות) ומדווח בעצמו, ולכן סוגרים אותה רק
    אחרי `SOURCE_SILENT_S` מהסגירה: `closed` — `failed` (לא דיווח סיום); `failed` — `cancelled`."""
    rows = conn.execute(
        "SELECT r.*, t.id AS task_id FROM room_rounds r JOIN tasks t ON t.id = r.source_task_id"
        " WHERE r.source_kind = ? AND r.state IN ('failed', 'closed')"
        " AND t.state IN ('pending', 'running')", (direct.BUILD_DISK,),
    ).fetchall()
    for row in rows:
        try:
            closed_at = datetime.fromisoformat(row["closed_at"])
        except (TypeError, ValueError):
            continue    # closed_at חסר או פגום: לא יודעים כמה זמן עבר — אין ראיה שהמקור שותק
        if (now - closed_at).total_seconds() < SOURCE_SILENT_S:
            continue
        if row["state"] == "failed":
            _close_source_task(conn, row["task_id"], row, "cancelled", ROUND_FAILED)
        else:
            _close_source_task(conn, row["task_id"], row, "failed", SOURCE_SILENT)


def settle(conn: SqlConnection, store, now: datetime) -> None:
    _fail_active_rounds(conn, store)
    _close_source_tasks(conn, now)
