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

שני מעברים, שניהם **מצב + יומן בלבד — לא מוחקים דבר**:

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
  משדר: החברים שלא הגיעו למצב סופי → `failed` בשם, הסבב → `closed`
  (מכונת המצבים של `sessions` מכירה `open/running/closed` בלבד), ויומן
  `session_orphaned`. ‏**גל של חדר השיכפולים** הוא סבב כזה בדיוק; הסבב
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


def reconcile_on_start(conn: sqlite3.Connection, library_root: str | Path,
                       sending: dict | None = None) -> dict:
    """מסמן את מה שנשאר `running` מהריצה הקודמת. מחזיר מה סומן, ליומן
    ולבדיקות: ``{"tasks_done": [...], "tasks_failed": [...],
    "tasks_reverify": [...], "sessions_closed": [...]}``. ‏``tasks_reverify``
    (‏#1470) — קליטות `verifying` שהאימות שלהן צריך לרוץ מחדש; מי שקורא
    (‏`create_runtime`) מפעיל אותו.

    ‏``sending`` הוא ``sender.status()`` של המנוע — ‏None בעלייה נקייה.
    סבב שהמנוע מעיד שהוא משדר אותו **עכשיו** אינו יתום; כל השאר כן.
    """
    root = Path(library_root)
    out: dict = {"tasks_done": [], "tasks_failed": [], "tasks_reverify": [],
                 "sessions_closed": []}

    for row in conn.execute(
        "SELECT id, image_id, name, created_by FROM tasks"
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
                    row["created_by"])
        else:
            out["tasks_failed"].append(row["id"])
            journal(conn, "capture_failed", f'{row["id"]} {RESTARTED_TASK}')

    # ‏#1470: קליטה `verifying` — האימות רץ ברקע בתהליך שמת. **לעולם לא
    # מכניסים בלי אימות:** אימג' שכבר בספרייה עבר אותו (ה-`rename` קורה רק
    # אחרי שכל sha256 תאם) → ‏`done`; אזור ביניים עם מניפסט → האימות **רץ
    # מחדש מההתחלה** (‏`tasks_reverify`, ‏`create_runtime` מפעיל); בלי שניהם
    # אין מה לאמת → ‏`failed` בשם.
    for row in conn.execute(
        "SELECT id, image_id, name, created_by FROM tasks"
        " WHERE type = 'capture' AND state = 'verifying'"
    ).fetchall():
        landed = (root / row["image_id"] / "manifest.json").is_file()
        staged = (staging_dir(root, row["id"]) / "manifest.json").is_file()
        if staged and not landed:
            out["tasks_reverify"].append(row["id"])
            journal(conn, "capture_verifying",
                    f'{row["id"]} {row["image_id"]} — the server restarted; verifying again from the start')
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
                    row["created_by"])
        else:
            out["tasks_failed"].append(row["id"])
            journal(conn, "capture_failed", f'{row["id"]} {RESTARTED_VERIFY}')

    live = sending["session_id"] if sending else None
    for row in conn.execute(
        "SELECT id FROM sessions WHERE state = 'running' AND kind = 'multicast'"
    ).fetchall():
        if row["id"] == live:
            continue
        stamp = now_iso()
        # התביעה על הסבב קודם (‏`update_one` — מותנה, ומשחרר את הנעילה
        # כשלא תאם); החברים באותה טרנזאקציה, כדי שסבב `closed` עם חברים
        # שעדיין `writing` לא יהיה מצב שאפשר לקרוא.
        with _write_lock, writing(conn):
            if not update_one(
                conn,
                "UPDATE sessions SET state = 'closed', closed_at = ?"
                " WHERE id = ? AND state = 'running'",
                (stamp, row["id"]),
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
                f'{row["id"]} {RESTARTED_SESSION}; failed={failed}')
    return out
