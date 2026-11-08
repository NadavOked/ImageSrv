"""תשובה שהמחולל אינו מבין, בכרטיס שהשרת מפיץ בו: מסך הכישלון (‏#2666).

הכרעת נדב (07/11, בסקירת מודל האמון #2578): **כל כשל אימות במכונה שמגיעה לרשת
שהשרת מפיץ בה אימג'ים מסתיים במסך הגרפי** — הסיבה בעברית וכפתור "אתחל עכשיו" —
ולא באתחול שקט מהדיסק. הפער היה `schema` שהמחולל אינו מבין (‏`grub_menu.decide`
‏`bad-schema`): הקובץ היה עולה מהדיסק בלי מילה.

**הכרטיס** — רק על ראיה חיובית שהבקשה הגיעה לכרטיס מפיץ (‏`hello.serves_images`).
רשת שהשרת לא מפיץ בה — בלי מסך, כמו היום (תרחיש 7 בהדגמה). הקורא בודק את זה,
לא המודול הזה.

**התקציב** — זה של #75 (‏`bootguard.ATTEMPT_LIMIT`): הסוכן מציג את המסך ומאתחל
(‏`die_local`), והתפריט הבא שוב היה מגיש אותו — לולאה. השרת סופר את ההגשות
בשורת `boot_attempts` (ההקשר `CONTEXT`, אין טבלה חדשה), ואחרי התקציב הדגל אינו
ניתן — כלומר הדיסק. **המונה תחום בזמן**, כמו כל הקשר ב-`bootguard`: הגשה שבאה
אחרי שקט ארוך מ-`WINDOW_SECONDS` מתחילה תקציב חדש. החלון "זז": גם בקשה שמעבר
לתקציב מחדשת אותו, ולכן לולאה רצופה נשארת בדיסק. ‏`CONTEXT` אינו `session:`/
‏`task:`, ולכן `bootguard.peek`/`repeats` אינם רואים אותו.

**ספק = בלי דגל** (עיקרון 5): ספירה שלא נקראה בחזרה אינה "הוגש פעם אחת", והכיוון
הבטוח הוא הדיסק — אותו כיוון של `bootguard.guard` כשאינו מצליח לספור.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from boot.grub_menu import ANSWER_FAILURE_CODES, ANSWER_FAILURE_FLAG, decide

from server.agent.bootguard import ATTEMPT_LIMIT
from server.db import _write_lock, now_iso, writing

log = logging.getLogger("imagesrv.answer_failure")

#: ההקשר בשורת `boot_attempts`: השרת הגיש למכונה את מסך הכישלון על תשובה לא מובנת.
CONTEXT = "answer-failure"

#: הדגל בתשובה לתפריט — ‏`grub_menu.decide` קורא אותו.
FLAG = ANSWER_FAILURE_FLAG

#: שקט ארוך מזה בין שתי בקשות תפריט = תקציב חדש. מחזור הלולאה (‏POST, ‏PXE, עליית
#: הסוכן, ‏`FAIL_SHOW_S`) הוא דקות בודדות; עשר דקות נותנות מרווח למחשב איטי —
#: אותו מרווח של `unknown_screen.WINDOW_SECONDS`.
WINDOW_SECONDS = 600


def failed(answer: object) -> bool:
    """האם המחולל אינו מבין את התשובה — אותה הכרעה שהקובץ ייכתב לפיה."""
    return decide(answer).code in ANSWER_FAILURE_CODES


def _cutoff(ts: str) -> str:
    """תחילת החלון: הגשה שה-`last_at` שלה לפני זה מתחילה תקציב חדש. נקודה אחת --
    ה-UPSERT של `offer` וה-`peek` של התצוגה (#2682) משתמשים בה, ולא בשני חשבונות."""
    return (datetime.fromisoformat(ts)
            - timedelta(seconds=WINDOW_SECONDS)).isoformat(timespec="seconds")


def _within_budget(attempts: int) -> bool:
    """האם ההגשה ה-`attempts` (כולל הנוכחית) עדיין בתקציב של #75."""
    return attempts <= ATTEMPT_LIMIT


def offer(conn: sqlite3.Connection, mac: str, answer: dict,
          now: str | None = None) -> dict:
    """תשובה לא מובנת לבקשת תפריט בכרטיס מפיץ → עם `FLAG` בתוך התקציב, אחרת כמו שהיא.

    ה-UPSERT סופר באותה כתיבה שבה הוא מאפס (הקשר אחר, או שקט ארוך מהחלון), בלי
    מרוץ קריאה-כתיבה. ההכרעה לפי מה שנקרא בחזרה: ההקשר שלנו והחותמת שנכתבה.
    """
    ts = now or now_iso()
    cutoff = _cutoff(ts)
    try:
        with _write_lock, writing(conn):
            conn.execute(
                "INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                " VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT (mac) DO UPDATE SET"
                "   attempts = CASE WHEN context = excluded.context AND last_at >= ?"
                "                   THEN attempts + 1 ELSE 1 END,"
                "   first_at = CASE WHEN context = excluded.context AND last_at >= ?"
                "                   THEN first_at ELSE excluded.first_at END,"
                "   context = excluded.context,"
                "   last_at = excluded.last_at",
                (mac, CONTEXT, ts, ts, cutoff, cutoff),
            )
        row = conn.execute("SELECT context, attempts, last_at FROM boot_attempts"
                           " WHERE mac = ?", (mac,)).fetchone()
    except sqlite3.Error:
        log.exception("answer-failure count for %s was not written -- the local disk", mac)
        return answer
    if row is None or row["context"] != CONTEXT or row["last_at"] != ts:
        log.error("answer-failure count for %s did not read back -- the local disk", mac)
        return answer
    attempts = int(row["attempts"])
    if not _within_budget(attempts):
        log.warning("%s: the server's answer is not understood, and the failure screen was"
                    " already served %d times -- the local disk (#2666, #75)", mac, ATTEMPT_LIMIT)
        return answer
    log.warning("%s: the server's answer is not understood -- the failure screen"
                " (#2666, attempt %d of %d)", mac, attempts, ATTEMPT_LIMIT)
    return {**answer, FLAG: True}


def peek(conn: sqlite3.Connection, mac: str, answer: dict,
         now: str | None = None) -> dict | None:
    """מה `offer` היה מחזיר לבקשת התפריט **הבאה** -- בלי לספור אותה (#2682).

    התצוגה "באתחול הבא" אינה אתחול, ולכן אינה כותבת; אבל היא צריכה את אותה תשובה
    ואת אותו תקציב, ולא חשבון שני שמתרחק. הכלל כאן הוא הכלל של `offer`: ההגשה הבאה
    היא `attempts + 1` כשהשורה היא ההקשר שלנו והחלון לא פג, אחרת הראשונה; בתקציב
    (`_within_budget`) -- עם `FLAG`, אחרת התשובה כמו שהיא (הדיסק).

    ‏`None` = המונה לא נקרא (טבלה חסרה, שגיאת DB): "לא הצלחנו לבדוק" אינו "יש מסך"
    וגם אינו "הדיסק" (עיקרון 5) -- הקורא מחזיר "לא ידוע"."""
    ts = now or now_iso()
    try:
        row = conn.execute("SELECT context, attempts, last_at FROM boot_attempts"
                           " WHERE mac = ?", (mac,)).fetchone()
    except sqlite3.Error:
        log.exception("answer-failure count for %s was not read", mac)
        return None
    live = row is not None and row["context"] == CONTEXT and row["last_at"] >= _cutoff(ts)
    attempts = int(row["attempts"]) + 1 if live else 1
    return {**answer, FLAG: True} if _within_budget(attempts) else answer


__all__ = ["CONTEXT", "FLAG", "WINDOW_SECONDS", "failed", "offer", "peek"]
