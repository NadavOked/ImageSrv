"""מה מחשב רשום יקבל כשידליקו אותו ברשת ההפצה — תצוגה בקונסולה (‏#2550).

**קריאה בלבד, ולכן ההגבלה העיקרית כאן היא מה שהמודול אינו עושה.** התצוגה אינה
אתחול: היא אינה נספרת בתקציב האתחולים (#75, ‏#2411), אינה מגינה לולאה, אינה
נרשמת כ-`hello` או כ-`net_seen`, אינה כותבת `unknown_mac` (#2498) ואינה מקדמת
סבב. מי שמשנה מצב כשהוא עונה על "מה יקרה" משקר לגבי מה שקרה.

**והיא אינה מחשבת את התשובה בעצמה.** תצוגה שמחשבת "מה היה קורה" בלוגיקה משלה
היא תצוגה שמתרחקת מהאמת ברגע שמישהו משנה את `decide()`. לכן היא עוברת בדיוק
באותם שלושה שלבים שהאתחול האמיתי עובר בהם, בגרסאות הקריאה-בלבד שלהם:

1. `hello.build_answer(..., read_only=True)` — אותה בניית תשובה (משימה, סבב,
   תפקיד) בלי הרישום ובלי קידום הסבב;
2. `bootguard.peek` — מה `guard` היה עושה בבקשה **הבאה**, בלי לספור אותה;
3. `boot.grub_menu.decide` — ההחלטה עצמה, הפונקציה שמייצרת את קובץ ה-GRUB.

**אי-ודאות היא תשובה** (עיקרון 5). ‏`decided: false` — ולא ניחוש — כשבניית
התשובה נכשלה, כשמונה האתחולים לא נקרא, או ש-`decide` החזירה קוד שהתצוגה אינה
מכירה. "לא הצלחנו לבדוק" אינו "יעלה מהדיסק".

**מה שהתצוגה אינה יודעת:** אם הבקשה תעבור את שער הזהות מול חכירת ה-DHCP
(#2411), אם הבקשה תגיע על וילן ההפצה, אם השרת יהיה למעלה, ומה סדר האתחול
בקושחה. היא עונה לשאלה "המחשב הזה ביקש תפריט מהשרת הזה — מה ייתן לו השרת".
סבב חדר השיכפולים מקודם על ידי הרקע (`room.sweep`) ואינו מקודם כאן, ולכן
תצוגה של משכפל יכולה לפגר אחרי הדופק האחרון בכמה שניות.
"""

from __future__ import annotations

import logging
import sqlite3

from boot.grub_menu import AGENT, LOCAL, decide

from server.agent import bootguard
from server.agent.hello import build_answer
from server.db import now_iso
from server.library.images import ImageLibrary
from server.rounds.sessions import SessionStore

log = logging.getLogger("imagesrv.boot_preview")

#: הסוגים שהקונסולה מציירת. סגורים בכוונה: קוד החלטה שאינו כאן הוא "לא ידוע",
#: לא סוג שהקונסולה מנחשת. ‏AGENT → לפי `code`; ‏LOCAL → תפריט או דיסק.
_AGENT_KINDS = {
    "task-assigned": "task",        # יש משימה פתוחה — עולה ל-ImageSrv לבצע אותה
    "session-joinable": "session",  # סבב פתוח או רץ — מצטרף
    "cloner-wait": "wait",          # משכפל בלי סבב — מסך המתנה
    "no-task": "auto",              # בנייה/כיתה בלי משימה — עולה אוטומטית למסך שלה
}

#: למה `decided` הוא false. קודים, לא טקסט: הקונסולה אומרת "לא ידוע" ועוד מילה.
WHY_ANSWER_FAILED = "answer-failed"
WHY_GUARD_UNREADABLE = "guard-unreadable"
WHY_UNKNOWN_CODE = "unknown-code"
WHY_NOT_REGISTERED = "not-registered"


def _undecided(why: str) -> dict:
    return {"decided": False, "why": why}


def _kind(action: str, code: str, show_menu: bool) -> str | None:
    if action == AGENT:
        return _AGENT_KINDS.get(code)
    if action == LOCAL:
        return "menu" if show_menu else "local"
    return None


def _offered(answer: dict) -> tuple[dict | None, dict | None]:
    """המשימה והסבב שהתשובה (לפני השומר) מציעה — **רק מה שהמפעיל רואה**.

    ‏`token` של המשימה לעולם אינו יוצא: הוא הרשאת הכתיבה עליה (#530), והתצוגה
    פתוחה לכל מי שמחובר. הסבב מזוהה ב-`image_id`; את שם האימג' הקונסולה כבר
    מחזיקה (`/images`), ולכן אין כאן סריקת ספרייה לכל מכונה בחדר."""
    task = answer.get("task")
    session = answer.get("session")
    if isinstance(task, dict) and task:
        task = {"type": task.get("type"), "name": task.get("name")}
    else:
        task = None
    if isinstance(session, dict) and session:
        session = {"id": session.get("id"), "state": session.get("state"),
                   "image_id": session.get("image_id")}
    else:
        session = None
    return task, session


def preview(conn: sqlite3.Connection, library: ImageLibrary, store: SessionStore,
            mac: str) -> dict:
    """ההחלטה של האתחול הבא של מכונה **רשומה** אחת. לעולם אינה זורקת ואינה כותבת."""
    try:
        answer = build_answer(conn, library, store, mac, read_only=True)
        if answer.get("known") is not True:
            return _undecided(WHY_NOT_REGISTERED)   # נמחקה בין הרשימה לבדיקה
        checked = bootguard.peek(conn, mac, answer)
        task, session = _offered(answer)
    except Exception:  # noqa: BLE001 — מכונה אחת שנכשלה אינה מפילה את החדר
        log.exception("boot preview of %s failed", mac)
        return _undecided(WHY_ANSWER_FAILED)
    if checked is None:
        return _undecided(WHY_GUARD_UNREADABLE)
    served, sent = checked
    decision = decide(served)
    kind = _kind(decision.action, decision.code, decision.show_menu)
    if kind is None:
        log.warning("boot preview of %s: decide() returned %r/%r, which the preview "
                    "does not know", mac, decision.action, decision.code)
        return _undecided(WHY_UNKNOWN_CODE)
    guarded = served.get("boot_guard") == bootguard.EXHAUSTED
    return {
        "decided": True,
        "kind": kind,
        "code": decision.code,
        "action": decision.action,
        "role": answer.get("role"),
        # מתי ההגנה (#75) תירה בבקשה הבאה, וכמה פעמים כבר נשלחה המכונה לאותה עבודה.
        "guard": {"sent": sent, "limit": bootguard.ATTEMPT_LIMIT} if guarded else None,
        "task": task,
        "session": session,
    }


def preview_all(conn: sqlite3.Connection, library: ImageLibrary,
                store: SessionStore) -> dict:
    """כל המכונות הרשומות בקריאה אחת — ‏`{checked_at, machines: {mac: פריט}}`."""
    macs = [row["mac"] for row in conn.execute("SELECT mac FROM machines ORDER BY mac")]
    return {
        "checked_at": now_iso(),
        "guard_limit": bootguard.ATTEMPT_LIMIT,
        "machines": {mac: preview(conn, library, store, mac) for mac in macs},
    }


__all__ = ["preview", "preview_all"]
