"""#1843 — מתי הגל צפוי להסתיים, בתשובת ה-hello שמחשב השיכפול מתחיל עליה לכתוב.

הקונסולה כבר מציגה "צפוי להסתיים ב-HH:MM" (`round.forecast`, `server/learn/eta.py`).
כאן אותה הערכה נוסעת למשכפל עצמו, ב-`session.forecast`:

    {"ends_at": <epoch>, "why": null,         "clock": true}    יש הערכה מההיסטוריה, והשעון אמין
    {"ends_at": null,    "why": "no_history", "clock": true}    אין עדיין היסטוריה לגל הזה
    {"ends_at": null,    "why": "clock",      "clock": false}   יש הערכה, אך שעון השרת לא הוכח מסונכרן
    {"ends_at": null,    "why": "no_image_size" | "no_drives" | "error", ...}   ההערכה לא נעשתה

`clock` אומר אם מותר לצייר **שעה כלשהי** על המסך — גם את זו שהמשכפל מחשב לעצמו מהקצב
שנמדד (עכשיו + הזמן שנותר). `session.forecast` חסר = שרת ישן, או שאין מה להגיד (גל פתוח,
או לא סבב חדר): הסוכן אינו מצייר שעה. `ends_at` הוא epoch ולא טקסט: סוכן הוא POSIX sh,
והשעון שלו נקבע מ-`now` של אותו hello (`clock_from_server`), כך ששניהם על אותו בסיס.

**שעון השרת** (הכרעת נדב, מוקאפ 07/11): שעה על המסך נשענת על שעון הקיר של השרת. כשהוא
לא מסונכרן (#2144) הוא אולי לא השעה שהמפעיל רואה על השעון שלו — ושעה שגויה גרועה
מהיעדר שעה. `ClockTrust` מחזיר `True` **רק** על ראיה חיובית (`synchronized is True`
מ-chrony, טרייה); מדידה שנכשלה, שלא הוזרקה או שלא נעשתה עדיין — "לא מסונכרן".
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime

from server.rounds import room

log = logging.getLogger("imagesrv.hello_eta")

#: כמה זמן קריאת `chronyc` אחת תקפה (ש'), וכמה זמן ממנה עדיין מאמינים לה אם
#: הרענון הבא עוד לא חזר. hello מגיע כל 2 ש' לכל משכפל; chronyc הוא תהליך.
CLOCK_TTL_S = 60
CLOCK_MAX_AGE_S = 300


class ClockTrust:
    """האם שעון השרת מסונכרן — מהקריאה האחרונה של `status()` (`timesync.status`)."""

    def __init__(self, status: Callable[[], dict] | None, now: Callable[[], float] = time.monotonic):
        self._status, self._now = status, now
        self._lock = threading.Lock()
        self._at: float | None = None
        self._ok = False

    def ok(self) -> bool:
        now = self._now()
        stale = self._at is None or now - self._at >= CLOCK_TTL_S
        # תהליכון אחד מרענן; האחרים (hello של משכפל אחר באותו רגע) קוראים את מה שיש.
        if self._status is not None and stale and self._lock.acquire(blocking=False):
            try:
                try:
                    self._ok = (self._status() or {}).get("synchronized") is True
                except Exception:        # noqa: BLE001 — מדידה שנכשלה היא "לא הוכח"
                    self._ok = False
                self._at = self._now()
            finally:
                self._lock.release()
        return self._at is not None and self._now() - self._at < CLOCK_MAX_AGE_S and self._ok


def _epoch(stamp: object) -> int | None:
    try:
        got = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        return None
    return int(got.timestamp()) if got is not None and got.tzinfo is not None else None


def _forecast(conn: sqlite3.Connection, library, session: dict, trusted: bool) -> dict | None:
    round_row = room.active_round(conn)
    wave = conn.execute("SELECT state, started_at FROM sessions WHERE id = ?", (session["id"],)).fetchone()
    if round_row is None or wave is None or round_row["wave_session_id"] != session["id"]:
        return None
    found = room.wave_forecast(conn, library, round_row, wave, wave["started_at"])
    if found is None:
        return None
    if found["seconds"] is None:
        return {"ends_at": None, "why": found["why"], "clock": trusted}
    ends = _epoch(found["ends_at"])
    if ends is None:
        return {"ends_at": None, "why": "error", "clock": trusted}
    return {"ends_at": ends, "why": None, "clock": True} if trusted else {"ends_at": None, "why": "clock", "clock": False}


def add(conn: sqlite3.Connection, library, answer: dict, trust: ClockTrust) -> None:
    """מוסיף `session.forecast` ל-`answer` של hello — רק למחשב שיכפול בגל חדר **רץ**.
    לעולם אינו מפיל את ה-hello: הערכה שנכשלה היא `why: "error"`, לא 500 למכונה שעומדת לכתוב."""
    session = answer.get("session")
    if answer.get("role") != "cloner" or not isinstance(session, dict) or session.get("prefix") != "ROOM":
        return
    trusted = trust.ok()          # גם בגל פתוח: עד שהוא יוצא הקריאה כבר טרייה
    if session.get("state") != "running":
        return
    try:
        forecast = _forecast(conn, library, session, trusted)
    except Exception:             # noqa: BLE001
        log.exception("hello forecast failed")
        forecast = {"ends_at": None, "why": "error", "clock": trusted}
    if forecast is not None:
        session["forecast"] = forecast
