"""פיוס בעליית השרת (#411) — מה שהיה `running` כשהשרת נפל אינו נשאר תלוי.

**הכרעת נדב, 06/09 (אפשרות 1):** סבב `running` יתום הופך לכישלון בעליית
השרת — לא ממשיך (אפשרות 3 נדחתה: המשך אוטומטי יכול להמשיך מחיקה) ולא
נשאר תלוי. קריסה נראית כמו כישלון, כי היא כישלון.

מה שהיה חסר: אין מי שרץ בעלייה על הטבלאות. ‏`sweep_work_areas` משאיר
`pending/running` במכוון (‏work_areas.py), ‏`SessionStore.__init__` אינו
סורק, ו-`room._resume` רץ בדופק ומניח שהשרת חי. התוצאה שנמדדה:
**מכונה שכבר חברה ולא סיימה קיבלה את הסבב שוב ב-hello, והמתינה
למולטיקאסט שלעולם לא יצא** — המסך אמר "הסבב פעיל", השרת הסכים, ואיש
לא טעה: פשוט לא היה משדר. ומשימות קליטה `running` נשארו כך לנצח.

המעברים כאן הם כולם **מצב + יומן בלבד — לא מוחקים דבר**. ‏(שלישי, ‏#2373:
העברה בין אתרים פעילה → ‏`failed` בשם; רביעי, ‏#2904: סבב חדר
`closing` — "עצור סבב" שנקטע — → ‏`closed`; ראו בגוף הפונקציה.)

* **משימת קליטה `running`** — ההעלאה היא בקשת HTTP אחת שמתה עם
  התהליך, ולכן אין "staging חי" אחרי עלייה. אבל `capture.py` עושה
  `rename` לספרייה **ואז** `UPDATE tasks 'done'` — שני צעדים בלי fsync —
  ולכן משימה שתיקיית האימג' שלה כבר בספרייה (עם מניפסט) היא `done`
  שהרישום שלו אבד, לא כישלון. הראיה החיובית היא הקובץ על הדיסק
  (עיקרון 3): קיים → `done`; אחרת → `failed` **בשם**.
* **סבב שידור `running`** — ‏`sender.start` מחובר רק למעבר **אל**
  `running` (‏sessions.py `on_running`), ומצב המשדר חי בזיכרון בלבד.
  אחרי עלייה אין משדר לאף סבב, אלא אם המנוע אומר במפורש שהוא משדר את
  הסבב הזה (‏`sender.status()` — ראיה חיובית, לא היעדר סימן). סבב בלי
  משדר: החברים שלא הגיעו למצב סופי → `failed` בשם, והסבב → `failed` עם
  `failed_reason` (‏#2907; ה-`closed` שכאן עד #2907 נשא "0/N" בלי סיבה), ויומן
  `session_orphaned`. **גל של חדר השיכפולים** נשאר `closed` (ראו מיד). ‏**גל של חדר השיכפולים** הוא סבב כזה בדיוק; הסבב
  המצטבר (`room_rounds`) נשאר `active` — הדופק הבא (`room._resume`)
  רואה גל סגור תחת סבב פעיל, סופר את המגירות שכן נכתבו (לפי serial)
  ופותח את הגל הבא, כפי שהוא עושה אחרי `_mark_lost`. זה אינו "המשך
  אוטומטי" של השידור שנפל: הגל שנפל נכשל, וגל חדש כותב מגירות טריות
  מההתחלה — המצב שהמפעיל רואה הוא כשל + "ממתין לגל הבא", לא "משדר".

זו אותה נקודת-מעבר ש-#1126 (‏`send_failed` → הסבב נסגר) צריך בזמן
ריצה; כאן נקודת הכניסה היא העלייה. סבב `open` (ממתין למצטרפים) אינו
נוגע: אין לו משדר להפסיד, והטיימר שלו (`last_join_at`) שורד ב-DB.

רץ אחרי `sweep_work_areas` ולפני שהקונסולה עונה: מי שפותח את היומן
אחרי אתחול חייב לראות שם שהסבב שלו לא המשיך.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from server.agent import reports
from server.db import _write_lock, journal, now_iso, update_one, writing
from server.core.tasks import staging_dir

#: הסיבה שנכתבת על המשימה/החבר — מה שהמפעיל קורא, לא קוד.
RESTARTED_TASK = "השרת עלה מחדש — הקליטה אבדה"
RESTARTED_VERIFY = "השרת עלה מחדש — קבצי הקליטה לא נמצאו לאימות; האימג' לא נכנס לספרייה"
RESTARTED_SESSION = "השרת עלה מחדש — המשדר אבד"
RESTARTED_TRANSFER = "נקטעה בהפעלה מחדש של השרת"
RESTARTED_CLOSING = "השרת אותחל באמצע עצירת הסבב; העצירה הושלמה בעלייה"


def reconcile_on_start(conn: sqlite3.Connection, library_root: str | Path,
                       sending: dict | None = None) -> dict:
    """מסמן את מה שנשאר `running` מהריצה הקודמת. מחזיר מה סומן, ליומן
    ולבדיקות: ``{"tasks_done": [...], "tasks_failed": [...],
    "tasks_reverify": [...], "sessions_closed": [...], "transfers_failed": [...],
    "rounds_closed": [...]}`` (‏#2373, ‏#2904). ‏``tasks_reverify``
    (‏#1470) — קליטות `verifying` שהאימות שלהן צריך לרוץ מחדש; מי שקורא
    (‏`create_runtime`) מפעיל אותו.

    ‏``sending`` הוא ``sender.status()`` של המנוע — ‏None בעלייה נקייה.
    סבב שהמנוע מעיד שהוא משדר אותו **עכשיו** אינו יתום; כל השאר כן.
    """
    root = Path(library_root)
    out: dict = {"tasks_done": [], "tasks_failed": [], "tasks_reverify": [],
                 "sessions_closed": [], "transfers_failed": [], "rounds_closed": []}

    for row in conn.execute(
        "SELECT id, mac, image_id, name, created_by FROM tasks"
        " WHERE type = 'capture' AND state = 'running'"
    ).fetchall():
        landed = (root / row["image_id"] / "manifest.json").is_file()
        # ‏#54: כל כתיבה תחת המנעול וב-``writing`` — כישלון בדרך משחרר את
        # הנעילה במקום להשאיר אותה יתומה לכל חיי התהליכון.
        with _write_lock, writing(conn):
            if landed:
                changed = update_one(
                    conn,
                    "UPDATE tasks SET state = 'done', updated_at = ?"
                    " WHERE id = ? AND state = 'running'",
                    (now_iso(), row["id"]),
                )
            else:
                changed = update_one(
                    conn,
                    "UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
                    " WHERE id = ? AND state = 'running'",
                    (RESTARTED_TASK, now_iso(), row["id"]),
                )
        if not changed:
            continue
        if landed:
            out["tasks_done"].append(row["id"])
            journal(conn, "capture_done",
                    f'{row["image_id"]} "{row["name"]}" (reconciled on start)',
                    row["created_by"], machine=row["mac"])
        else:
            out["tasks_failed"].append(row["id"])
            journal(conn, "capture_failed", f'{row["id"]} {RESTARTED_TASK}', machine=row["mac"])

    # ‏#1470: קליטה `verifying` — האימות רץ ברקע בתהליך שמת. **לעולם לא
    # מכניסים בלי אימות:** אימג' שכבר בספרייה עבר אותו (ה-`rename` קורה רק
    # אחרי שכל sha256 תאם) → ‏`done`; אזור ביניים עם מניפסט → האימות **רץ
    # מחדש מההתחלה** (‏`tasks_reverify`, ‏`create_runtime` מפעיל); בלי שניהם
    # אין מה לאמת → ‏`failed` בשם.
    for row in conn.execute(
        "SELECT id, mac, image_id, name, created_by FROM tasks"
        " WHERE type = 'capture' AND state = 'verifying'"
    ).fetchall():
        landed = (root / row["image_id"] / "manifest.json").is_file()
        staged = (staging_dir(root, row["id"]) / "manifest.json").is_file()
        if staged and not landed:
            out["tasks_reverify"].append(row["id"])
            journal(conn, "capture_verifying",
                    f'{row["id"]} {row["image_id"]} — the server restarted; verifying again from the start',
                    machine=row["mac"])
            continue
        with _write_lock, writing(conn):
            if landed:
                changed = update_one(
                    conn,
                    "UPDATE tasks SET state = 'done', updated_at = ?"
                    " WHERE id = ? AND state = 'verifying'",
                    (now_iso(), row["id"]),
                )
            else:
                changed = update_one(
                    conn,
                    "UPDATE tasks SET state = 'failed', error = ?, updated_at = ?"
                    " WHERE id = ? AND state = 'verifying'",
                    (RESTARTED_VERIFY, now_iso(), row["id"]),
                )
        if not changed:
            continue
        if landed:
            out["tasks_done"].append(row["id"])
            journal(conn, "capture_done",
                    f'{row["image_id"]} "{row["name"]}" (verified before the restart; reconciled on start)',
                    row["created_by"], machine=row["mac"])
        else:
            out["tasks_failed"].append(row["id"])
            journal(conn, "capture_failed", f'{row["id"]} {RESTARTED_VERIFY}', machine=row["mac"])

    # ‏#2373: העברה בין אתרים רצה בתהליכון של התהליך שמת (‏`start_transfer` /
    # ‏`start_pull`), ואחרי עלייה אין תהליכון לאף שורה. ‏`failed` בשם, עם
    # ‏`bytes_sent` כפי שהיה — לא `done` (אין ראיה שהמשני קיבל) ולא מחיקה
    # (ההיסטוריה שהקונסולה מציגה). אחרת השורה "רצה" לנצח, חוסמת שליחה חוזרת
    # של אותו אימג', ומאז #1891 גם עדכון ואתחול מתוזמן.
    for row in conn.execute(
        "SELECT id, image_id, bytes_sent, bytes_total FROM storage_transfers"
        " WHERE state IN ('queued', 'sending', 'verifying')"
    ).fetchall():
        with _write_lock, writing(conn):
            changed = update_one(
                conn,
                "UPDATE storage_transfers SET state = 'failed', error = ?, updated_at = ?"
                " WHERE id = ? AND state IN ('queued', 'sending', 'verifying')",
                (RESTARTED_TRANSFER, now_iso(), row["id"]),
            )
        if not changed:
            continue
        out["transfers_failed"].append(row["id"])
        journal(conn, "storage_transfer_failed",
                f'{row["id"]} {row["image_id"]}: {RESTARTED_TRANSFER}'
                f' ({row["bytes_sent"]} מתוך {row["bytes_total"]} בייטים)')

    live = sending["session_id"] if sending else None
    for row in conn.execute(
        "SELECT s.id, g.role FROM sessions s LEFT JOIN groups g ON g.id = s.group_id"
        " WHERE s.state = 'running' AND s.kind = 'multicast'"
    ).fetchall():
        if row["id"] == live:
            continue
        stamp = now_iso()
        # ‏#2907: שידור שאינו של חדר השיכפולים (כיתה) נכשל **בשם** — `failed`
        # עם `failed_reason`, כמו `SessionStore.fail`; בלי זה הסקירה
        # (`latest_failed_broadcast`) לא הראתה כשל, ונשאר רק `session_orphaned`
        # ביומן. גל של חדר נשאר `closed`: `room._resume` הוא שמחליט עליו
        # (גל הבא לסבב מהספרייה, או כישלון הסבב כולו לסבב מדיסק חי).
        room_wave = row["role"] == "cloner"
        # התביעה על הסבב קודם (‏`update_one` — מותנה, ומשחרר את הנעילה
        # כשלא תאם); החברים באותה טרנזאקציה, כדי שסבב `closed` עם חברים
        # שעדיין `writing` לא יהיה מצב שאפשר לקרוא.
        with _write_lock, writing(conn):
            if not update_one(
                conn,
                "UPDATE sessions SET state = ?, closed_at = ?, failed_reason = ?"
                " WHERE id = ? AND state = 'running'",
                ("closed" if room_wave else "failed", stamp,
                 None if room_wave else RESTARTED_SESSION, row["id"]),
            ):
                failed = None
            else:
                # רק מי שלא הגיע למצב סופי: חבר שכבר `done`/`failed`/`lost`
                # נשאר כפי שדיווח — הוא לא איבד דבר בעלייה.
                final = tuple(sorted(reports.FINAL))
                failed = conn.execute(
                    "UPDATE session_members SET state = 'failed', error = ?,"
                    " updated_at = ? WHERE session_id = ?"
                    f" AND state NOT IN ({','.join('?' * len(final))})",
                    (RESTARTED_SESSION, stamp, row["id"], *final),
                ).rowcount
        if failed is None:
            continue
        out["sessions_closed"].append(row["id"])
        journal(conn, "session_orphaned",
                f'{row["id"]} {RESTARTED_SESSION}; failed={failed}', round_id=row["id"])

    # ‏#2904: "עצור סבב" נקטע. ‏`close_round` עושה commit ל-`closing` **לפני** שהוא
    # סוגר את הגל ועוצר את המשדר, ושני המעברים החוצה ממנו רצים רק באותה קריאה —
    # בלי זה הסבב `closing` לנצח, ו-`busy_db` עוצר גיבוי, אתחול מתוזמן ועדכון.
    # ‏`closed` ולא `active` (המפעיל ביקש לעצור) ולא `failed` (שהיה מחזיר אותו
    # למסך כדורש טיפול): אחרי עלייה אין משדר, וזה מה שהעצירה הייתה משיגה.
    # הגל `running` כבר נסגר בלולאה למעלה; גל `open` — ש-#411 משאיר במכוון —
    # נסגר כאן, אחרת הוא נשאר פתוח תחת סבב סגור. גל שהמנוע מעיד שהוא משדר
    # (‏`live`) אינו יתום, ואז גם הסבב שלו אינו נוגע.
    for row in conn.execute(
        "SELECT id, wave_session_id, written_drives, target_drives FROM room_rounds"
        " WHERE state = 'closing'"
    ).fetchall():
        if row["wave_session_id"] and row["wave_session_id"] == live:
            continue
        stamp = now_iso()
        with _write_lock, writing(conn):
            changed = update_one(
                conn,
                "UPDATE room_rounds SET state = 'closed', closed_at = ?"
                " WHERE id = ? AND state = 'closing'",
                (stamp, row["id"]),
            )
            if changed and row["wave_session_id"]:
                conn.execute(
                    "UPDATE sessions SET state = 'closed', closed_at = ?"
                    " WHERE id = ? AND state = 'open'",
                    (stamp, row["wave_session_id"]),
                )
        if not changed:
            continue
        out["rounds_closed"].append(row["id"])
        journal(conn, "room_close",
                f'{row["id"]} written={row["written_drives"]}/{row["target_drives"]}'
                f' — {RESTARTED_CLOSING}', round_id=row["id"])
    return out
