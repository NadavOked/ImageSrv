"""‏#1039: מי מחובר לקונסולה — ‏`GET /api/console/sessions`.

העוגייה חתומה ואין sessions בזיכרון (`auth.py`), ולכן "מי מחובר" נרשם
כאן, בטבלת ``console_sessions``: שורה לעוגייה (לפי ה-sha256 שלה — העוגייה
עצמה היא הרשאה ואינה נשמרת), עם הכתובת, מתי נכנסו ומתי נראו.

**השרת אינו כותב ל-DB בכל בקשה** (נדב): ``last_seen`` נכתב לכל היותר פעם
ב-``TOUCH_SECONDS`` לכל עוגייה. מה שמחליט אם לכתוב הוא מילון בזיכרון —
בלי קריאה מה-DB. אחרי אתחול השרת המילון ריק, והבקשה הראשונה של כל
דפדפן כותבת שוב; השורות עצמן שורדות את האתחול, כמו העוגיות.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

from server.db import _settle, _write_lock, writing

TOUCH_SECONDS = 60

#: מוזרק בבדיקות (``monkeypatch``) — כדי ש"פעם בדקה" יימדד ולא יחכה.
_now = time.time
#: token_hash → מתי נכתב לאחרונה. תהליכי בלבד, בכוונה.
_written: dict[str, float] = {}
_lock = threading.Lock()
log = logging.getLogger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def touch(conn: sqlite3.Connection, token: str, username: str, ip: str,
          ttl: int) -> None:
    """נקרא על כל בקשת קונסולה **מאומתת**. כותב רק כשהעוגייה חדשה לתהליך
    או כשעברה דקה מהכתיבה הקודמת שלה.

    ‏``since`` = רגע הנפקת העוגייה (התפוגה שבתוכה פחות ה-TTL) — הכניסה,
    לא הבקשה הראשונה שנרשמה. העוגייה כבר אומתה (``auth.check``), ולכן
    הפענוח כאן אינו בדיקה."""
    key = _hash(token)
    now = _now()
    with _lock:
        last = _written.get(key)
        if last is not None and now - last < TOUCH_SECONDS:
            return
        _written[key] = now
        for stale in [k for k, t in _written.items() if now - t > ttl]:
            del _written[stale]
    fields = token.rpartition("|")[0].split("|")
    expires = int(fields[2])
    # ‏"purpose:epoch" בעוגייה החדשה; עוגייה ישנה (3 מפרידים) = epoch 0 —
    # אותו פענוח כמו `auth.read_session`.
    epoch = int(fields[3].partition(":")[2] or 0) if len(fields) == 4 else 0
    try:
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute("DELETE FROM console_sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO console_sessions"
                " (token_hash, username, ip, since, last_seen, expires_at, auth_epoch)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (token_hash) DO UPDATE SET"
                " ip = excluded.ip, last_seen = excluded.last_seen",
                (key, username, ip, _iso(expires - ttl), _iso(now), expires, epoch),
            )
    except sqlite3.Error as exc:
        # הרישום משני לבקשה עצמה — מסך קונסולה לא נופל בגללו. אבל הוא לא
        # נבלע: הסימון מוסר (הבקשה הבאה מנסה שוב), והיומן אומר. ‏last_seen
        # הישן שנשאר בטבלה נראה כזה ב-/sessions ("נראה לפני 5 דק'").
        with _lock:
            _written.pop(key, None)
        log.warning("console_sessions: last_seen of %s not written: %s", username, exc)


def forget(conn: sqlite3.Connection, token: str | None) -> None:
    """יציאה: העוגייה נמחקת מהדפדפן, והשורה — מכאן."""
    if not token:
        return
    key = _hash(token)
    with _lock:
        _written.pop(key, None)
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM console_sessions WHERE token_hash = ?", (key,))


def active(conn: sqlite3.Connection) -> list[dict]:
    """‏``[{user, ip, since, last_seen}]`` — רק עוגיות שעדיין תקפות: לא פגו,
    המשתמש קיים ואינו מושבת, וה-``auth_epoch`` שבעוגייה לא בוטל מאז
    (``users.bump_auth_epoch``, ‏#1075) — אותם שלושה תנאים של
    ``auth.read_session``, כדי ש"מחובר" לא יכלול עוגייה שהשרת כבר דוחה."""
    rows = conn.execute(
        "SELECT s.username, s.ip, s.since, s.last_seen FROM console_sessions s"
        " JOIN users u ON u.username = s.username COLLATE NOCASE"
        " WHERE s.expires_at >= ? AND u.disabled_at IS NULL"
        " AND s.auth_epoch >= COALESCE(u.auth_epoch, 0)"
        " ORDER BY s.last_seen DESC, s.username",
        (_now(),),
    ).fetchall()
    return [{"user": r["username"], "ip": r["ip"], "since": r["since"],
             "last_seen": r["last_seen"]} for r in rows]
