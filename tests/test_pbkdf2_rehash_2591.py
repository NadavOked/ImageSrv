"""#2591 — עלות גיבוב עדכנית, ושדרוג שקוף של רשומה ישנה בכניסה מוצלחת.

ההתחייבויות: לא נועלים אף אחד; לא משנים hash בלי הסיסמה; לא מורידים רשומה
חזקה; לא דורסים סיסמה שהוחלפה בין הקריאה לכתיבה; כישלון כתיבה אינו הופך
כניסה נכונה לכושלת.
"""

from __future__ import annotations

import sqlite3

import pytest

from server.console import users
from server.db import connect

PASSWORD = "Aa12345!"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "u.db")
    users.create(c, "nadav", PASSWORD, "admin", by="t")
    return c


def put_hash(conn, iterations: int, password: str = PASSWORD, salt: str = "ab" * 16) -> str:
    stored = users._hash(password, salt, iterations)
    conn.execute("UPDATE users SET pw_hash = ? WHERE username = 'nadav'", (stored,))
    conn.commit()
    return stored


def stored_hash(conn) -> str:
    return conn.execute("SELECT pw_hash FROM users WHERE username = 'nadav'").fetchone()[0]


def test_the_default_cost_is_at_least_the_owasp_figure():
    assert users._ITERATIONS >= 600_000


def test_a_new_password_is_stored_at_the_current_cost(conn):
    assert stored_hash(conn).split("$")[1] == str(users._ITERATIONS)


def test_an_old_record_is_upgraded_on_a_correct_login(conn):
    old = put_hash(conn, 200_000)
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    new = stored_hash(conn)
    assert new != old
    assert new.split("$")[1] == str(users._ITERATIONS)
    assert new.split("$")[2] != old.split("$")[2], "מלח חדש"
    # וגם אחרי השדרוג הסיסמה נבדקת, ושגויה נדחית
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    assert users.verify(conn, "nadav", "Wrong-pass-1!") is None


def test_a_wrong_password_changes_nothing(conn):
    old = put_hash(conn, 200_000)
    assert users.verify(conn, "nadav", "Wrong-pass-1!") is None
    assert stored_hash(conn) == old


def test_a_blocked_user_is_not_upgraded(conn):
    old = put_hash(conn, 200_000)
    conn.execute("UPDATE users SET disabled_at = '2026-01-01T00:00:00' WHERE username = 'nadav'")
    conn.commit()
    assert users.verify(conn, "nadav", PASSWORD) is None
    assert stored_hash(conn) == old


def test_a_stronger_record_is_never_lowered(conn):
    old = put_hash(conn, users._ITERATIONS + 100_000)
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    assert stored_hash(conn) == old


def test_a_record_at_the_current_cost_is_not_rewritten(conn):
    old = stored_hash(conn)
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    assert stored_hash(conn) == old


def test_the_upgrade_keeps_flags_and_sessions(conn):
    """לא `auth_epoch` ולא `must_change_password`: הסיסמה לא השתנתה."""
    conn.execute("UPDATE users SET must_change_password = 1, auth_epoch = 7"
                 " WHERE username = 'nadav'")
    conn.commit()
    put_hash(conn, 200_000)
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    row = conn.execute("SELECT must_change_password, auth_epoch FROM users"
                       " WHERE username = 'nadav'").fetchone()
    assert (row["must_change_password"], row["auth_epoch"]) == (1, 7)


def test_a_password_changed_meanwhile_is_not_overwritten(conn):
    """השומר `WHERE pw_hash = <הישן>`: שורה שנקראה לפני החלפת הסיסמה."""
    put_hash(conn, 200_000)
    stale_row = users.lookup(conn, "nadav")
    users.change_own_password(conn, "nadav", PASSWORD, "Brand-new-1!")
    changed = stored_hash(conn)
    users._upgrade_hash(conn, stale_row, PASSWORD)
    assert stored_hash(conn) == changed
    assert users.verify(conn, "nadav", "Brand-new-1!") == "admin"


def test_a_failed_write_does_not_fail_a_correct_login(conn, monkeypatch, caplog):
    old = put_hash(conn, 200_000)

    def boom(_conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(users, "writing", boom)
    with caplog.at_level("WARNING"):
        assert users.verify(conn, "nadav", PASSWORD) == "admin"
    assert stored_hash(conn) == old
    assert "database is locked" in caplog.text
    monkeypatch.undo()
    assert users.verify(conn, "nadav", PASSWORD) == "admin"          # וחוזר בכניסה הבאה
    assert stored_hash(conn).split("$")[1] == str(users._ITERATIONS)
