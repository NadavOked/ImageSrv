"""#3180: החיבור של תהליכון שמת נסגר מיד, ולא כשאיסוף הזבל מגיע אליו.

``sqlite3.Connection`` שנזרק בלי ``close`` נשאר פתוח (שלושה fd) עד
``gc.collect()``, כי מטמון ההצהרות שלו מחזיק אותו במעגל. כל תהליכון של מאגר
הבקשות שמת השאיר כך חיבור פתוח, וב-CI של v0.95.0 ה-fd עברו את 1024.
הטסטים כאן מכבים את איסוף הזבל, כדי שרק הסגירה המפורשת תיחשב.
"""

import gc
import sqlite3
import threading

import pytest

from server.db import connect


@pytest.fixture
def no_gc():
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def _is_open(conn: sqlite3.Connection) -> bool:
    try:
        conn.total_changes
    except sqlite3.ProgrammingError:
        return False
    return True


def test_a_dead_threads_connection_is_closed_without_gc(tmp_path, no_gc):
    database = connect(tmp_path / "t.db")
    seen = []

    def work():
        database.execute("SELECT 1").fetchone()
        seen.append(database.connection)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()
    assert len(seen) == 1
    assert _is_open(seen[0]) is False
    # החיבור של התהליכון הראשי אינו נוגע בזה: הוא עדיין פתוח ועובד.
    assert database.execute("SELECT 1").fetchone()[0] == 1
    database.close()


def test_close_closes_the_callers_connection_and_reopens_on_demand(tmp_path, no_gc):
    database = connect(tmp_path / "t.db")
    first = database.connection
    database.close()
    assert _is_open(first) is False
    assert database.execute("SELECT 1").fetchone()[0] == 1
    assert database.connection is not first
    database.close()


def test_dropping_the_database_does_not_close_a_connection_still_in_use(tmp_path, no_gc):
    """הסגירה קשורה לחיי התהליכון, לא לחיי ה-``Database``: הביטוי
    ``connect(p).execute(...).fetchall()`` משחרר את ה-``Database`` לפני
    ה-``fetchall``, ו-``connect(p).connection`` מחזיק חיבור בלי ה-``Database``."""
    connect(tmp_path / "t.db").execute("CREATE TABLE t (a)").connection.close()
    cursor = connect(tmp_path / "t.db").execute("SELECT 1 UNION SELECT 2")
    assert [r[0] for r in cursor.fetchall()] == [1, 2]
    cursor.connection.close()
    conn = connect(tmp_path / "t.db").connection
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    conn.close()
