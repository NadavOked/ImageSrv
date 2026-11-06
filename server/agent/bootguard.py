"""שומר לולאת האתחול — עיקרון 1 כשיש סבב פתוח (‏#75).

‏`die_local` בסוכן מסתיים ב-`reboot -f`, בהנחה שהתפריט הבא יחזיר "דיסק
מקומי". ההנחה נכונה רק כשאין סבב. בסבב פתוח התפריט מחזיר
`set default=imagesrv` עם `timeout=0`, ולכן המכונה חוזרת ישר לסוכן
שנכשל שוב — **לולאת אתחול אינסופית**, מחזור כל ~2 דקות, ששוחזרה על
חומרה. עיקרון 1 נשבר בדיוק בתרחיש שבו הוא נחוץ.

אתחול אינו "לרדת לדיסק המקומי" — הוא רק *מקווה* לזה. הראיה החיובית
היחידה שקיימת כאן היא של השרת עצמו: **הוא** מגיש את התפריט, ולכן הוא
היחיד שיכול להבטיח לאן ילך האתחול הבא. לכן מה שנספר כאן הוא מה שהשרת
**עשה** — כמה פעמים כבר שלח את המכונה הזו לסוכן באותו הקשר — ולא מה
שהמכונה דיווחה. מכונה שמתה לפני שהייתה לה רשת אינה מדווחת דבר, וזה
בדיוק המקרה ששוחזר: `hello` לא הגיע מעולם, ובקשת התפריט היא העדות
היחידה שהשרת מקבל ממנה.

**ההקשר חייב להיות תחום בזמן**, אחרת המונה מצטבר לנצח ומכונה תקינה
תיחסם אחרי חודש: `session:<id>` ו-`task:<id>` נגמרים, וכל הקשר חדש
מאפס. מחשבי השיכפול אינם נספרים כלל — ברירת המחדל שלהם היא הסוכן בלי
סבב ובלי משימה (`cloner-wait`), אין להם הקשר תחום, ואין להם מערכת
מקומית שאפשר להפיל אליהם (‏#17).
"""

from __future__ import annotations

import logging
import sqlite3

from boot.grub_menu import AGENT, decide

from server.db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.bootguard")

#: כמה פעמים השרת מוכן לשלוח את אותה מכונה לאותה עבודה. מסלול תקין
#: צורך אתחול אחד: מכונה שהצטרפה נשארת בסוכן עד סוף השחזור, ומכונה
#: שנכשלה אחרי `hello` עוצרת על מסך שגיאה ואינה מאתחלת (‏#64). כל
#: מספר גבוה מ-1 הוא כבר חזרה — שלושה נותנים מקום לניסיון חוזר ידני
#: של טכנאי, ועדיין חוסמים את הלולאה תוך דקות ספורות.
ATTEMPT_LIMIT = 3

#: הערך שנוסף לתשובת השרת כשהתקציב נגמר. ראו `grub_menu.decide`.
EXHAUSTED = "exhausted"


def context_of(answer: dict) -> str | None:
    """לאיזו עבודה המכונה נשלחת — או None כשאין הקשר תחום בזמן."""
    task = answer.get("task")
    if isinstance(task, dict) and task.get("id"):
        return f"task:{task['id']}"
    session = answer.get("session")
    if isinstance(session, dict) and session.get("id"):
        return f"session:{session['id']}"
    return None


def guard(conn: sqlite3.Connection, mac: str, answer: dict) -> dict:
    """סופר את בקשת התפריט הזו, ומחזיר את התשובה — אולי מסומנת.

    נקראת אך ורק ממסלול תפריט האתחול. ‏`hello` אינו עובר כאן: הוא לא
    מחליט מאיפה המכונה עולה, והספירה היא של אתחולים.
    """
    context = _counted_context(answer)
    if context is None:
        return answer                     # ממילא דיסק מקומי, או cloner-wait: אין מה לספור

    attempts = _record(conn, mac, context)
    if attempts is None:
        # לא הצלחנו לספור — וזה לא "נספר אחד". עיקרון 5: פעולה שלא
        # הצליחה לבדוק את עצמה נכשלת, והכיוון הבטוח הוא הדיסק המקומי.
        log.error("boot attempt for %s (%s) was not recorded", mac, context)
        journal(conn, "boot_loop_unverified", f"{mac} {context}", machine=mac)
        return _exhausted(answer)

    if not _over_budget(attempts):
        return answer
    if attempts == ATTEMPT_LIMIT + 1:
        # פעם אחת, ברגע המעבר: אחרי זה המכונה כבר עולה מהדיסק ואין
        # אירוע חדש לדווח עליו.
        journal(conn, "boot_loop_local", f"{mac} {context} attempts={attempts}", machine=mac)
    log.warning("boot loop guard sends %s to the local disk (%s, attempt %d)",
                mac, context, attempts)
    return _exhausted(answer)


def _counted_context(answer: dict) -> str | None:
    """ההקשר שבו בקשת התפריט הזו נספרת — או None כשאינה נספרת בכלל: ‏`decide`
    אומרת דיסק מקומי (אין מה לספור), או שאין הקשר תחום בזמן (`cloner-wait`).
    נקראת גם מ-`guard` וגם מ-`peek`, כדי ששתיהן יסכימו מה נספר."""
    if decide(answer).action != AGENT:
        return None
    return context_of(answer)


def _over_budget(attempts: int) -> bool:
    """האם ניסיון מספר `attempts` כבר חורג מהתקציב — הכרעה אחת, לשתי הקריאות."""
    return attempts > ATTEMPT_LIMIT


def peek(conn: sqlite3.Connection, mac: str, answer: dict) -> tuple[dict, int] | None:
    """**קריאה בלבד**: מה `guard` היה עושה בבקשת התפריט **הבאה** של המכונה הזו.

    מחזירה (התשובה כפי שהיא הייתה מוגשת, כמה פעמים השרת כבר שלח את המכונה
    לאותה עבודה), או None כש-**אי אפשר לדעת** — הקריאה נכשלה (עיקרון 5: ‏
    "לא הצלחנו לבדוק" אינו "בדקנו, לא מוגנת"). אינה כותבת דבר, אינה מקדמת
    את המונה ואינה רושמת ליומן: זה בדיוק מה שמבדיל אותה מ-`guard`, ושימושה
    היחיד הוא התצוגה בקונסולה של מה שמחשב יקבל (#2550).

    הספירה הבאה היא `attempts + 1` באותו הקשר ו-`1` בהקשר אחר — אותו כלל
    שה-UPSERT ב-`_record` מיישם; הטסט `test_peek_agrees_with_guard` מריץ את
    שתיהן זו מול זו בכל מצב של המונה."""
    context = _counted_context(answer)
    if context is None:
        return answer, 0
    try:
        row = conn.execute(
            "SELECT context, attempts FROM boot_attempts WHERE mac = ?", (mac,)
        ).fetchone()
    except sqlite3.Error:
        log.exception("boot attempts of %s could not be read for the preview", mac)
        return None
    same = row is not None and row["context"] == context
    already = int(row["attempts"]) if same else 0
    if _over_budget(already + 1):
        return _exhausted(answer), min(already, ATTEMPT_LIMIT)
    return answer, already


def repeats(conn: sqlite3.Connection, context: str,
            minimum: int = 2) -> dict[str, dict]:
    """מי אתחל יותר מפעם אחת לאותה עבודה — לפי MAC, לתצוגה בקונסולה.

    ‏`minimum=2` בכוונה: אתחול שני הוא כבר חזרה, והמפעיל צריך לראות
    אותה לפני שהתקציב נגמר. מחשב שלא מגיע הוא בדיוק מה שנראה כמו
    מחשב שלא נדלק (‏#64).
    """
    return {
        row["mac"]: {
            "attempts": row["attempts"],
            "blocked": row["attempts"] > ATTEMPT_LIMIT,
        }
        for row in conn.execute(
            "SELECT mac, attempts FROM boot_attempts"
            " WHERE context = ? AND attempts >= ? ORDER BY mac",
            (context, minimum),
        )
    }


def _exhausted(answer: dict) -> dict:
    marked = dict(answer)
    marked["boot_guard"] = EXHAUSTED
    return marked


def _record(conn: sqlite3.Connection, mac: str, context: str) -> int | None:
    """מונה את הניסיון ומחזיר את מספרו — או None אם לא ניתן לאשר שנספר.

    ההקשר הוא חלק מהעדכון עצמו: הקשר חדש מאפס את המונה באותה כתיבה,
    בלי מרוץ בין קריאה לכתיבה.

    ‏`_write_lock` ו-`writing` הם אותם שני מנגנונים שתוקנו ב-#272 על
    ‏`net_seen` (#356). כאן זה חמור יותר: זה **השומר של האתחול**.
    כתיבה שנכשלה בלי ``rollback`` משאירה ``BEGIN`` פתוח על החיבור, כל
    כתיבה אחריו נכשלת מיד — ותהליכון uvicorn ממוחזר, כלומר עומס חולף
    אחד היה מחזיר ``None`` מכאן לכל מכונה עד אתחול השרת. ‏`guard`
    מתרגם ``None`` ל-``exhausted``, כלומר כיתה שלמה שנשלחת לדיסק
    המקומי באמצע סבב. שומר שקט אינו שומר (עיקרון 5).

    הנעילה עוטפת את הכתיבה **בלבד**, וקריאת האימות שאחריה מחוצה לה:
    ‏`guard` נוטל את אותה נעילה דרך `journal`, והיא ``Lock`` ולא ``RLock``.
    """
    ts = now_iso()
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
            " VALUES (?, ?, 1, ?, ?)"
            " ON CONFLICT (mac) DO UPDATE SET"
            "   attempts = CASE WHEN context = excluded.context"
            "                   THEN attempts + 1 ELSE 1 END,"
            "   first_at = CASE WHEN context = excluded.context"
            "                   THEN first_at ELSE excluded.first_at END,"
            "   context = excluded.context,"
            "   last_at = excluded.last_at",
            (mac, context, ts, ts),
        )
    # ראיה חיובית: הערך נקרא בחזרה. שורה שאינה שם, או שההקשר בה אינו
    # זה שנכתב, פירושה שהספירה לא קרתה — ולא שהיא יצאה אחת.
    row = conn.execute(
        "SELECT context, attempts FROM boot_attempts WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or row["context"] != context:
        return None
    return int(row["attempts"])


__all__ = ["ATTEMPT_LIMIT", "EXHAUSTED", "context_of", "guard", "peek", "repeats"]
