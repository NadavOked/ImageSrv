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
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

from server.console import viewer_sessions
from server.db import _settle, _write_lock, journal, writing

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
                # ‏#2414: ‏WebSocket (``auth.check``) אינו מעביר כתובת — לא מוחקים את הידועה.
                " ip = CASE WHEN excluded.ip = '' THEN console_sessions.ip ELSE excluded.ip END,"
                " last_seen = excluded.last_seen",
                (key, username, ip, _iso(expires - ttl), _iso(now), expires, epoch),
            )
    except sqlite3.Error as exc:
        # הרישום משני לבקשה עצמה — מסך קונסולה לא נופל בגללו. אבל הוא לא
        # נבלע: הסימון מוסר (הבקשה הבאה מנסה שוב), והיומן אומר. ‏last_seen
        # הישן שנשאר בטבלה נראה כזה ב-/sessions ("נראה לפני 5 דק'").
        with _lock:
            _written.pop(key, None)
        log.warning("console_sessions: last_seen of %s not written: %s", username, exc)


def idle_expired(conn: sqlite3.Connection, token: str, idle_seconds: int,
                 ttl: int) -> bool:
    """‏#2414: האם העוגייה לא נראתה יותר מ-``idle_seconds`` (‏``console_idle_seconds``).

    עד כאן הניתוק בחוסר פעילות היה של הדפדפן בלבד, והעוגייה נשארה תקפה 12
    שעות אחרי שהלשונית נסגרה. הפעילות האחרונה = ‏``last_seen`` של העוגייה, או
    רגע ההנפקה כשאין לה שורה עדיין (עוגייה שהונפקה ולא נשלחה). ‏``last_seen``
    נכתב לכל היותר פעם ב-``TOUCH_SECONDS``, ולכן החסד הוא בדיוק דקה כזו —
    הדפדפן מנתק ב-``idle_seconds``, השרת לכל המוקדם דקה אחריו. ‏0 = כבוי."""
    if idle_seconds <= 0:
        return False
    row = conn.execute(
        "SELECT last_seen FROM console_sessions WHERE token_hash = ?", (_hash(token),),
    ).fetchone()
    if row is not None:
        last = datetime.fromisoformat(row["last_seen"]).timestamp()
    else:
        last = int(token.rpartition("|")[0].split("|")[2]) - ttl
    return _now() - last > idle_seconds + TOUCH_SECONDS


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


#: ‏#2552: מזהה הסשן על המסך = ששת התווים הראשונים של ה-sha256. הטוקן, והגיבוב
#: המלא, אינם יוצאים מהשרת.
SID_LEN = 6


def active(conn: sqlite3.Connection, current_token: str | None = None) -> list[dict]:
    """‏``[{sid, user, role, ip, since, last_seen, current, monitor}]`` — רק
    עוגיות שעדיין תקפות: לא פגו, המשתמש קיים ואינו מושבת, ה-``auth_epoch``
    שבעוגייה לא בוטל מאז (``users.bump_auth_epoch``, ‏#1075), והיא לא בוטלה
    בנפרד (``revoked_tokens`` — יציאה, או ניתוק מהמסך, ‏#2552) — אותם תנאים של
    ``auth.read_session``, כדי ש"מחובר" לא יכלול עוגייה שהשרת כבר דוחה.

    ‏``sid`` — מזהה קצר (``SID_LEN``) לניתוק בודד. ‏``current`` — העוגייה של הבקשה
    הזו. ‏``monitor`` — כמה גשרי מוניטור/VM פתוחים על העוגייה."""
    mine = _hash(current_token) if current_token else None
    bridges = viewer_sessions.open_by_hash()
    return [{"sid": r["token_hash"][:SID_LEN], "user": r["username"], "role": r["role"],
             "ip": r["ip"], "since": r["since"], "last_seen": r["last_seen"],
             "current": r["token_hash"] == mine,
             "monitor": bridges.get(r["token_hash"], 0)} for r in _live(conn)]


def _live(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.token_hash, s.username, u.role, s.ip, s.since, s.last_seen, s.expires_at"
        " FROM console_sessions s"
        " JOIN users u ON u.username = s.username COLLATE NOCASE"
        " WHERE s.expires_at >= ? AND u.disabled_at IS NULL"
        " AND s.auth_epoch >= COALESCE(u.auth_epoch, 0)"
        " AND NOT EXISTS (SELECT 1 FROM revoked_tokens r WHERE r.token_hash = s.token_hash)"
        " ORDER BY s.last_seen DESC, s.username",
        (_now(),),
    ).fetchall()


class SessionRefused(Exception):
    """ניתוק סשן שלא בוצע, עם הקוד ש-HTTP יחזיר ועם משפט למנהל."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def revoke_one(conn: sqlite3.Connection, sid: str, by: str,
               current_token: str | None) -> dict:
    """‏#2552: ניתוק סשן בודד לפי המזהה הקצר: העוגייה שלו נדחית **מיד** (``revoked_tokens``,
    כמו logout), השורה נמחקת מהרשימה, וגשר מוניטור פתוח שלה נסגר.

    לא מנתקים את הסשן שממנו נשלחה הבקשה (למטרה הזו יש יציאה רגילה). מזהה שאינו
    מתאים לסשן פעיל אחד בדיוק הוא כשל גלוי — לא ניתוק של "הראשון שנמצא"."""
    from server.console import auth   # noqa: PLC0415 — כמו ב-``auth._active``: auth טוען אותנו, לא להפך
    if not re.fullmatch(rf"[0-9a-f]{{{SID_LEN}}}", sid or ""):
        raise SessionRefused(400, "מזהה סשן לא תקין")
    mine = _hash(current_token) if current_token else None
    found = [r for r in _live(conn) if r["token_hash"][:SID_LEN] == sid]
    if not found:
        raise SessionRefused(404, "הסשן כבר לא פעיל")
    if len(found) > 1:
        raise SessionRefused(409, "המזהה מתאים ליותר מסשן אחד — רענן את הרשימה ונסה שוב")
    row = found[0]
    if row["token_hash"] == mine:
        raise SessionRefused(409, "זה הסשן הנוכחי שלך — להתנתקות ממנו יש \"יציאה\"")
    auth.revoke_hash(conn, row["token_hash"], float(row["expires_at"]))
    with _lock:
        _written.pop(row["token_hash"], None)
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM console_sessions WHERE token_hash = ?", (row["token_hash"],))
    viewer_sessions.revoke_hash(row["token_hash"])
    journal(conn, "session_revoked", f"{row['username']} {sid} {row['ip']}".strip(), by)
    return {"user": row["username"], "sid": sid}
