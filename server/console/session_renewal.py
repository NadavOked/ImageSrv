"""#2757: חידוש עוגיית הקונסולה בשימוש פעיל (sliding).

עד כאן ה-expiry נקבע פעם אחת בכניסה, ומפעיל שעבד ברציפות נזרק אחרי 12 שעות
באמצע עבודה. עכשיו בקשה **מאומתת** שנותרה לעוגייה שלה פחות ממחצית ה-TTL מקבלת
עוגייה חדשה, באותם דגלים (``auth.cookie_flags``).

שלושה חלקים:

- ``propose`` — נקרא מ-``current_user`` אחרי שהעוגייה עברה את **כל** הבדיקות
  (חתימה, תפוגה, משתמש קיים ופעיל, ``auth_epoch``, ביטול, חוסר פעילות, ``purpose``).
  מנפיק טוקן חדש ורושם אותו ב-scope; לא נוגע בשום מצב.
- ``commit`` — נקרא רק ברגע שהעוגייה יוצאת בפועל: בודק את הישנה **שוב**, מבטל אותה
  (``auth.supersede``) ומעביר אליה את השורה ב-``console_sessions`` ואת גשרי המוניטור.
- ``SessionRenewal`` — ה-middleware שמוסיף את ה-``Set-Cookie`` לתשובה. נדרש כי
  endpoint שמחזיר ``Response`` בעצמו לא ממזג כותרות מ-dependency.

החידוש **נושא את ה-epoch של העוגייה הישנה** ואת התפקיד מהטבלה — אף פעם לא קורא
epoch חדש. בלי זה, ``bump_auth_epoch`` שקרה בין האימות להנפקה היה נעלם.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from server.console import auth, presence, viewer_sessions

log = logging.getLogger(__name__)

RENEW_SCOPE_KEY = "imagesrv.session_renewal"
RENEW_BELOW_SECONDS = auth.TTL_SECONDS // 2

_commit_lock = threading.Lock()


def propose(request, conn: sqlite3.Connection, session: auth.Session, token: str) -> None:
    """רושם ב-scope חידוש ``(ישן, חדש)`` אם מגיע לו. ללא תופעות לוואי אחרות --
    באפליקציה בלי ``SessionRenewal`` (פורט הסוכן) הרישום לא נקרא, ושום דבר לא מבוטל."""
    # מלאה בלבד: קיוסק (פטור מכל מנגנון ה-TTL של הקונסולה), pwchange ו-mfaenroll.
    if session.purpose or auth._is_logout(request) or RENEW_SCOPE_KEY in request.scope:
        return
    if session.expiry - auth.time.time() >= RENEW_BELOW_SECONDS or auth.in_grace(token):
        return
    request.scope[RENEW_SCOPE_KEY] = (
        token, auth.issue(conn, session.username, session.role, epoch=session.epoch))


def commit(conn: sqlite3.Connection, old: str, new: str) -> bool:
    """האם ``new`` יוצא לדפדפן. הישנה נבדקת שוב כאן — ביטול (logout, ניתוק מהמסך,
    שינוי סיסמה, מחיקה) שקרה אחרי ``propose`` לא מקבל עוגייה חדשה."""
    with _commit_lock:
        if auth.in_grace(old) or auth.read_session(conn, old, idle_exempt=True) is None:
            return False
        auth.supersede(conn, old)
    try:
        presence.rekey(conn, old, new)
    except sqlite3.Error as exc:
        # השורה משנית: הבקשה הבאה של החדשה תיצור אחת. הסשן עצמו תקין.
        log.warning("console_sessions: row of a renewed cookie not moved: %s", exc)
    viewer_sessions.rekey(old, new)
    return True


def cookie_header(value: str, tls) -> bytes:
    response = Response()
    response.set_cookie(auth.COOKIE_NAME, value, **auth.cookie_flags(tls))
    return next(v for k, v in response.raw_headers if k == b"set-cookie")


def _sets_session_cookie(headers) -> bool:
    prefix = auth.COOKIE_NAME.encode() + b"="
    return any(k.lower() == b"set-cookie" and v.startswith(prefix) for k, v in headers)


class SessionRenewal:
    """middleware גולמי (כמו ``ConsoleNoStaleCache``): נוגע רק בכותרות תשובה אחת.

    תשובה שכבר קובעת עוגיית session בעצמה — כניסה, החלפת סיסמה, logout — נשארת
    כמות שהיא; החידוש לא דורס אותה."""

    def __init__(self, inner, *, conn: sqlite3.Connection, tls=None):
        self.inner = inner
        self.conn = conn
        self.tls = tls

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return

        async def send_renewed(message):
            pending = scope.get(RENEW_SCOPE_KEY)
            if message["type"] == "http.response.start" and pending is not None:
                headers = list(message.get("headers", []))
                try:
                    if not _sets_session_cookie(headers) and await run_in_threadpool(
                            commit, self.conn, *pending):
                        headers.append((b"set-cookie", cookie_header(pending[1], self.tls)))
                except sqlite3.Error as exc:
                    # לא נמסרה עוגייה חדשה והישנה בתוקף: ננסה שוב בבקשה הבאה.
                    log.warning("console session not renewed: %s", exc)
                message = {**message, "headers": headers}
            await send(message)

        await self.inner(scope, receive, send_renewed)
