"""#2459 — `login_attempts` תחומה במספר מפתחות.

עד כאן כל שם משתמש אקראי הוסיף שורה (מפתח = שם + IP), והיא נשארה עד ה-housekeeping
של 90 יום: ניחוש שמות אקראיים ניפח את ה-DB בלי גבול. עכשיו מעל `MAX_KEYS` מפתח חדש
מפנה עשירית — קודם מפתחות בלי נעילה בתוקף, מהמעט-כשלונות לרב ומהישן לחדש.

מה **לא** השתנה (הכרעת נדב 05/11): הספים לפי (שם, IP), והתקרה לפי חשבון — 50 ב-15
דקות, נעילה ל-15 דקות, בלי פטור לפי כתובת. אלה נבדקים כאן גם תחת הצפה.

בקרה שלילית: `server/console/login_guard.py` מ-origin/main → נופלים `test_random_usernames_do_not_grow_the_table_without_bound`,
`test_a_locked_key_survives_the_flood` ו-`test_the_account_cap_still_holds_under_the_flood`.
"""

from __future__ import annotations

import time

import pytest

from server.console import login_guard

CAP = 20


@pytest.fixture()
def guard(server, monkeypatch):
    t = {"now": time.time()}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    # raising=False: מול הקוד הישן (בלי MAX_KEYS) הטסט נופל על הטענה, לא על monkeypatch.
    monkeypatch.setattr(login_guard, "MAX_KEYS", CAP, raising=False)
    return server["ctx"].conn, t


def _keys(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]


def _flood(conn, t, count: int, ip: str = "10.66.0.1") -> None:
    for n in range(count):
        t["now"] += 1
        login_guard.record_failure(conn, f"rnd-{n:05d}", ip)


def test_random_usernames_do_not_grow_the_table_without_bound(guard):
    conn, t = guard
    _flood(conn, t, CAP * 5)
    assert _keys(conn) <= CAP
    trims = conn.execute(
        "SELECT COUNT(*) FROM journal WHERE event = 'login_attempts_trimmed'").fetchone()[0]
    assert trims >= 1


def test_a_locked_key_survives_the_flood(guard):
    """הצפה בשמות אקראיים אינה פותחת נעילה של (שם, IP) — היא מפנה את עצמה."""
    conn, t = guard
    for _ in range(login_guard.LOCK_AFTER):
        login_guard.record_failure(conn, "noc", "10.66.9.9")
    _flood(conn, t, CAP * 5)
    assert _keys(conn) <= CAP
    with pytest.raises(login_guard.LoginBlocked) as exc:
        login_guard.check(conn, "noc", "10.66.9.9")
    assert exc.value.status == 403


def test_the_oldest_low_count_keys_go_first(guard):
    """מפתח עם כמה כשלונות (בהשהיה שכבר חלפה) נשאר; מפתחות של כשלון אחד מתפנים."""
    conn, t = guard
    for _ in range(login_guard.DELAY_AFTER + 1):
        login_guard.record_failure(conn, "slow", "10.66.8.8")
    t["now"] += 3600
    _flood(conn, t, CAP * 3)
    row = conn.execute("SELECT failures FROM login_attempts WHERE key = 'slow|10.66.8.8'").fetchone()
    assert row is not None and row["failures"] == login_guard.DELAY_AFTER + 1


def test_below_the_cap_nothing_is_removed(guard):
    conn, t = guard
    _flood(conn, t, CAP)
    assert _keys(conn) == CAP
    assert conn.execute(
        "SELECT COUNT(*) FROM journal WHERE event = 'login_attempts_trimmed'").fetchone()[0] == 0


def test_the_account_cap_still_holds_under_the_flood(guard):
    """50 כשלונות מ-50 כתובות נועלים את החשבון מכל כתובת — גם כשהמפתחות שלהם פונו."""
    conn, t = guard
    for n in range(login_guard.ACCOUNT_LIMIT):
        login_guard.record_failure(conn, "noc", f"10.77.0.{n + 1}")
    assert _keys(conn) <= CAP
    with pytest.raises(login_guard.LoginBlocked) as exc:
        login_guard.check(conn, "noc", "10.88.0.1")
    assert exc.value.status == 429
    assert exc.value.code == login_guard.ACCOUNT_CAPPED
    assert exc.value.retry_after == login_guard.ACCOUNT_LOCK_SECONDS
