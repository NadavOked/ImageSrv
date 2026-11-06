"""הגבלת ניסיונות כניסה — #1075 / #1085.

מפתח = שם משתמש (lowercase) + IP. אחרי 5 כשלונות: השהיה 2^(n-5) שניות
(1, 2, 4, …). אחרי 10: נעילה 15 דקות. יומן בנעילה.

#2443 — בנוסף, תקרה **לפי חשבון** מכל הכתובות יחד: ‏50 כשלונות ב-15 דקות
(חלון נע, ‏`login_account_failures`) → ‏429 לכל כתובת. בלעדיה ניחוש מבוזר
(כל כתובת מתחת ל-10) לא נעצר לעולם. בהגעה לתקרה **החשבון ננעל ל-15 דקות
בדיוק, מכל כתובת** (הכרעת נדב 05/11): אין פטור לכתובת שכבר נכנסה בעבר — מי
שמגיע לקונסולה נקבע בחומת האש, לא כאן. הנעילה פגה לבד, והאיפוס הפיזי ב-DCUI
מאפס אותה. שם שאינו קיים נספר בדיוק כמו קיים — אחרת התקרה הייתה מגלה אילו
שמות יש.

‏#2443 (ממצא Fable) — התקרה לפי חשבון נספרת **לכל משטח בנפרד**: ‏`CONSOLE`
(פורט הקונסולה, ‏8081) ו-`AGENT` (פורט הסוכן, ‏8080: ‏`/api/v1/agent/login`,
‏`/agent/pulls`, פתיחת סבב מהתחנה, וכניסת מחשב הבנייה ב-`/api/console/login`
שחיה גם שם). אחרת חמש תחנות ברשת ההפצה נעלו את המנהל מחוץ לקונסולה בתוך
דקה. ניחוש מבוזר בפורט הסוכן עדיין נעצר — בתקרה של המשטח שלו. התקרה לפי
(שם, IP) משותפת לשני המשטחים: הכתובות בשתי הרשתות שונות ממילא.

‏#2459 — ‏`login_attempts` תחומה ב-`MAX_KEYS` מפתחות (‏`_trim_keys`). מתחת לתקרה
שום דבר לא השתנה; הספים, הנעילות והתקרה לפי חשבון — כמו שהם.
"""

from __future__ import annotations

import math
import sqlite3
import time

from server.db import _write_lock, journal, writing

DELAY_AFTER = 5
LOCK_AFTER = 10
LOCK_SECONDS = 15 * 60
ACCOUNT_LIMIT = 50
ACCOUNT_WINDOW = 15 * 60
ACCOUNT_LOCK_SECONDS = 15 * 60
CONSOLE = "console"
AGENT = "agent"
#: ‏#2443: ‏`error` בגוף ה-429 של התקרה לפי חשבון — הקונסולה מציגה את המשפט של השרת.
ACCOUNT_CAPPED = "login_account_capped"
#: ‏#2459: תקרה למספר המפתחות ב-`login_attempts`. כל שם אקראי מוסיף שורה, והניקוי
#: הרגיל (‏housekeeping) הוא 90 יום — בלי תקרה, ניחוש שמות אקראיים מנפח את ה-DB.
#: מעל התקרה מפנים `MAX_KEYS // 10` בבת אחת, כדי שהמיון לא ירוץ בכל כשל.
MAX_KEYS = 10_000


class LoginBlocked(Exception):
    """הכניסה נחסמה — השהיה או נעילה. status הוא קוד HTTP."""

    def __init__(self, status: int, message: str, retry_after: int | None = None,
                 code: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after
        self.code = code


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


def check(conn: sqlite3.Connection, username: str, ip: str, surface: str = CONSOLE) -> None:
    """דוחה אם המפתח בהשהיה או בנעילה. לא בודק סיסמה."""
    t = now()
    _check_key(conn, username, ip, t)
    _check_account(conn, username, ip, t, surface)


def _check_key(conn: sqlite3.Connection, username: str, ip: str, t: float) -> None:
    """התקרה לפי (שם, IP): השהיה אקספוננציאלית, ואז נעילה."""
    row = _row(conn, _key(username, ip))
    if row is None:
        return
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


def _account(username: str) -> str:
    return (username or "").strip().lower()


def _check_account(conn: sqlite3.Connection, username: str, ip: str, t: float,
                   surface: str) -> None:
    name = _account(username)
    if not name:
        return
    rows = conn.execute(
        "SELECT at FROM login_account_failures WHERE username = ? AND surface = ? AND at > ?"
        " ORDER BY at DESC LIMIT ?",
        (name, surface, t - ACCOUNT_WINDOW - ACCOUNT_LOCK_SECONDS, ACCOUNT_LIMIT),
    ).fetchall()
    # נעול כש-50 הכשלים האחרונים נפלו בתוך 15 דקות: 15 דקות מהכשל שהגיע לתקרה.
    # בזמן הנעילה `check` דוחה לפני הסיסמה, ולכן לא נרשם כשל חדש שיאריך אותה.
    if len(rows) < ACCOUNT_LIMIT or rows[-1]["at"] <= rows[0]["at"] - ACCOUNT_WINDOW:
        return
    wait = math.ceil(rows[0]["at"] + ACCOUNT_LOCK_SECONDS - t)
    if wait <= 0:
        return
    raise LoginBlocked(
        429,
        f"החשבון ננעל ל-{ACCOUNT_LOCK_SECONDS // 60} דקות: {ACCOUNT_LIMIT} ניסיונות כניסה"
        f" כושלים ב-{ACCOUNT_WINDOW // 60} הדקות האחרונות, מכמה כתובות. הנעילה נפתחת לבד"
        f" בעוד {math.ceil(wait / 60)} דקות.",
        retry_after=wait,
        code=ACCOUNT_CAPPED,
    )


def record_failure(conn: sqlite3.Connection, username: str, ip: str,
                   surface: str = CONSOLE) -> None:
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
        capped = _record_account_failure(conn, username, t, surface)
        trimmed = _trim_keys(conn, t) if row is None else 0
    if trimmed:
        journal(conn, "login_attempts_trimmed",
                f"{trimmed} keys — over {MAX_KEYS} (last ip={ip})", username.strip())
    if locked:
        journal(conn, "login_lockout", f"{username.strip()} ip={ip}", username.strip())
    if capped:
        journal(conn, "login_account_lockout",
                f"{username.strip()} — {ACCOUNT_LIMIT} failures in "
                f"{ACCOUNT_WINDOW // 60} min from many addresses via {surface} (last ip={ip})",
                username.strip())


def _record_account_failure(conn: sqlite3.Connection, username: str, t: float,
                            surface: str) -> bool:
    """שורה לכשל, וניקוי כל מה שמעבר לחלון. ‏True רק בכשל שמגיע בדיוק לתקרה."""
    name = _account(username)
    if not name:
        return False
    conn.execute("DELETE FROM login_account_failures WHERE at <= ?",
                 (t - ACCOUNT_WINDOW - ACCOUNT_LOCK_SECONDS,))
    conn.execute("INSERT INTO login_account_failures (username, at, surface) VALUES (?, ?, ?)",
                 (name, t, surface))
    count = conn.execute(
        "SELECT COUNT(*) FROM login_account_failures WHERE username = ? AND surface = ? AND at > ?",
        (name, surface, t - ACCOUNT_WINDOW)).fetchone()[0]
    return count == ACCOUNT_LIMIT


def _trim_keys(conn: sqlite3.Connection, t: float) -> int:
    """‏#2459: מעל `MAX_KEYS` — מפנה את המפתחות שהכי פחות חשובים, עד `MAX_KEYS`
    פחות עשירית. קודם מפתח בלי נעילה בתוקף, ובתוכם מהמעט-כשלונות לרב ומהישן לחדש:
    הצפה בשמות אקראיים (כשלון אחד לכל שם) מפנה את עצמה, ולא מוחקת נעילה של מישהו.
    מתחת לתקרה — לא נוגע בכלום. ‏התקרה לפי חשבון (‏`login_account_failures`) אינה כאן."""
    count = conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
    if count <= MAX_KEYS:
        return 0
    excess = count - (MAX_KEYS - MAX_KEYS // 10)
    cur = conn.execute(
        "DELETE FROM login_attempts WHERE key IN (SELECT key FROM login_attempts"
        " ORDER BY COALESCE(locked_until, 0) > ?, failures, last_at LIMIT ?)",
        (t, excess),
    )
    return cur.rowcount


def clear(conn: sqlite3.Connection, username: str, ip: str) -> None:
    """כניסה שהצליחה: מאפס את המונה של (שם, IP).

    המונה לפי חשבון **אינו** מתאפס כאן: אחרת מי שמצליח להיכנס כל 14 דקות היה
    מאפס את הספירה של מנחש מבוזר. הוא מתנקה רק בפקיעת החלון, או ב-DCUI."""
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM login_attempts WHERE key = ?", (_key(username, ip),))
