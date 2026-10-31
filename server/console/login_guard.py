"""הגבלת ניסיונות כניסה — #1075 / #1085.

מפתח = שם משתמש (lowercase) + IP. אחרי 5 כשלונות: השהיה 2^(n-5) שניות
(1, 2, 4, …). אחרי 10: נעילה 15 דקות. יומן בנעילה.
"""

from __future__ import annotations

import sqlite3
import time

from server.db import _write_lock, journal, writing

DELAY_AFTER = 5
LOCK_AFTER = 10
LOCK_SECONDS = 15 * 60


class LoginBlocked(Exception):
    """הכניסה נחסמה — השהיה או נעילה. status הוא קוד HTTP."""

    def __init__(self, status: int, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def now() -> float:
    """נקודת הזרקה לטסטים — לא לישון, להזיז את השעון."""
    return time.time()


def _key(username: str, ip: str) -> str:
    return f"{(username or '').strip().lower()}|{(ip or '?')}"


def _row(conn: sqlite3.Connection, key: str):
    return conn.execute(
        "SELECT failures, locked_until, last_at FROM login_attempts WHERE key = ?",
        (key,),
    ).fetchone()


def check(conn: sqlite3.Connection, username: str, ip: str) -> None:
    """דוחה אם המפתח בהשהיה או בנעילה. לא בודק סיסמה."""
    row = _row(conn, _key(username, ip))
    if row is None:
        return
    t = now()
    locked = row["locked_until"]
    if locked is not None and locked > t:
        wait = max(1, int(locked - t))
        raise LoginBlocked(
            403,
            "החשבון ננעל ל-15 דקות אחרי 10 ניסיונות כושלים",
            retry_after=wait,
        )
    failures = row["failures"]
    if failures >= DELAY_AFTER:
        delay = 2 ** (failures - DELAY_AFTER)
        ready_at = (row["last_at"] or 0) + delay
        if ready_at > t:
            wait = max(1, int(ready_at - t))
            raise LoginBlocked(
                429,
                f"נסה שוב בעוד {wait} שניות",
                retry_after=wait,
            )


def record_failure(conn: sqlite3.Connection, username: str, ip: str) -> None:
    key = _key(username, ip)
    t = now()
    locked = False
    with _write_lock, writing(conn):
        row = _row(conn, key)
        failures = (row["failures"] if row else 0) + 1
        locked_until = t + LOCK_SECONDS if failures >= LOCK_AFTER else None
        conn.execute(
            "INSERT INTO login_attempts (key, failures, locked_until, last_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " failures = excluded.failures,"
            " locked_until = excluded.locked_until,"
            " last_at = excluded.last_at",
            (key, failures, locked_until, t),
        )
        locked = failures == LOCK_AFTER
    if locked:
        journal(conn, "login_lockout", f"{username.strip()} ip={ip}", username.strip())


def clear(conn: sqlite3.Connection, username: str, ip: str) -> None:
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (_key(username, ip),))
