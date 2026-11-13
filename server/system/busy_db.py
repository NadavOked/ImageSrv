"""מה שבמסד אומר "השרת עסוק" (#1956) — החלק של `auto_reboot.busy_reason` שנקרא מ-DB בלבד.

מודול נפרד וחסר תלויות, כי `backup_job` הוא תהליך נפרד שרץ כל דקה: הוא אינו רואה את מה שבזיכרון
השרת, ואינו טוען את `server.auto_reboot` (ועמו את כל השרת) רק כדי לשאול אם יש סבב. ההגדרה עצמה
נשארת אחת: `busy_reason` קורא לכאן.
"""

from __future__ import annotations

import sqlite3


def reason(conn: sqlite3.Connection) -> str | None:
    if conn.execute("SELECT 1 FROM sessions WHERE state IN ('open','running') LIMIT 1").fetchone():
        return "יש סבב הפצה פתוח או רץ"
    if conn.execute("SELECT 1 FROM room_rounds WHERE state IN ('active','closing') LIMIT 1").fetchone():
        return "יש סבב חדר פתוח או רץ"
    if conn.execute("SELECT 1 FROM tasks WHERE type = 'capture' AND state = 'running' LIMIT 1").fetchone():
        return "קליטת אימג' רצה"
    # #1891 (הכרעת נדב 01/11): שרת שמעביר לשרת אחר — עסוק. queued כלול: התהליכון
    # שלה כבר הופעל (start_transfer/start_pull), ו-restart מפיל אותו.
    if conn.execute("SELECT 1 FROM storage_transfers WHERE state IN ('queued','sending','verifying')"
                    " LIMIT 1").fetchone():
        return "העברת אימג' בין אתרים רצה"
    return None
