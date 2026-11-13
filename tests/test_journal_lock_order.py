"""#457 — היפוך סדר נעילה בין נעילת הכתיבה של sqlite ל-``_write_lock``.

שתי נעילות, שני תהליכונים, וכל אחד מחזיק את מה שהשני מחכה לו:

* תהליכון א' סיים ``update_one`` שהצליח — ולכן **הטרנזאקציה פתוחה**
  ונעילת הכתיבה של sqlite בידו — וממשיך ל-`journal`, שם הוא ממתין
  ל-``_write_lock``.
* תהליכון ב' כבר מחזיק את ``_write_lock`` (הוא נכנס ל-`journal` ראשון)
  וממתין לנעילת הכתיבה של sqlite.

זו נעילה משולבת קלאסית, אבל היא **אינה** נראית ככזו: ``busy_timeout``
של 5 שניות שובר אותה מעצמו, והצד שנשבר מדווח ``database is locked``.
מכאן זה נראה כמו עומס חולף, ובדיוק לכן זה כמעט נזנח כרעש.

**מה הטסטים כאן מוכיחים ומה לא.** האזהרה הבודדת שנצפתה בפיתוח
#449+#450 אין לה traceback ואין לה תזמון, ולכן **אי אפשר לקבוע**
שהמנגנון הזה הוא שייצר אותה — הקישור הוא השערה. מה שנבדק כאן הוא
המנגנון עצמו, והוא נבנה **דטרמיניסטית**: ההמתנה של תהליכון ב' נשברת
תמיד, כי היחיד שיכול לשחרר את נעילת sqlite הוא תהליכון א' — והוא ממתין
לנעילה שתהליכון ב' מחזיק. בלי התיקון תהליכון ב' מקבל
``database is locked`` בכל ריצה, לא באחת מני רבות.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from server import db
from server.db import _open, connect, journal, net_seen, set_setting, update_one

#: מפתח שקיים בסכימה (`DEFAULT_SETTINGS`), כדי שה-UPDATE המותנה יתאים
#: שורה — כלומר `update_one` יחזיר True ו**ישאיר את הטרנזאקציה פתוחה**.
KEY = "session_wait_seconds"


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """ממתין לתנאי ולא לשעון. מחזיר האם התנאי התקיים."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_journal_write_survives_a_holder_of_the_sqlite_write_lock(tmp_path):
    """שני הצדדים של הנעילה המשולבת, במקביל — ואף אחד לא נשבר.

    תהליכון ב' הוא **כותב היומן**: זה בדיוק תהליכון הרקע של השידור,
    ש-`SenderEngine._run` פולט ממנו אירועים ל-`db.journal`. תהליכון א'
    הוא הכתיבה המותנית שהצליחה וממשיכה לרשום ביומן.

    הראיה חיובית ולא היעדר-חריגה: שתי שורות היומן נקראות בחזרה, וגם
    ה-UPDATE שתהליכון א' החזיק נמצא בטבלה. בלי התיקון תהליכון ב' זורק
    ``database is locked`` אחרי ``busy_timeout`` מלא.
    """
    path = tmp_path / "t.db"
    connect(path)                                   # סכימה

    conn_a = _open(str(path))
    conn_b = _open(str(path))
    failure: list[BaseException] = []
    entered = threading.Event()

    def background_journal() -> None:
        """תהליכון ב' — כותב היומן של מנוע השידור."""
        entered.set()
        try:
            journal(conn_b, "sender_event", "wave 1 started")
        except BaseException as exc:                # noqa: BLE001 — נאסף ונטען
            failure.append(exc)

    # תהליכון א': כתיבה מותנית שהצליחה. הטרנזאקציה **נשארת פתוחה**,
    # ולכן נעילת הכתיבה של sqlite בידו מכאן ועד ה-commit.
    assert update_one(
        conn_a, "UPDATE settings SET value = ? WHERE key = ?", ("777", KEY)
    ) is True
    assert conn_a.in_transaction is True, (
        "\u200fupdate_one שהצליח כבר אינו משאיר טרנזאקציה פתוחה — "
        "הפיגום כאן אינו בונה את המצב שהוא מתיימר לבנות")

    writer = threading.Thread(target=background_journal, name="sender")
    writer.start()
    entered.wait(timeout=5)
    # ממתינים לתנאי ולא לשעון: ``_write_lock`` תפוס פירושו שתהליכון ב'
    # כבר בפנים, ומכאן הוא יכול רק להיחסם על נעילת sqlite שבידנו.
    assert _wait_until(db._write_lock.locked), "תהליכון ב' לא נטל את _write_lock"

    # וכאן ההיפוך: תהליכון א' מגיע ליומן כשנעילת הכתיבה בידו.
    journal(conn_a, "member_expired", "aa:bb:cc:dd:ee:ff")

    writer.join(timeout=30)
    assert not writer.is_alive(), "כותב היומן לא סיים"
    assert not failure, (
        f"כותב היומן נשבר: {failure[0]!r} — זו הנעילה המשולבת של #457, "
        "שנראית כמו עומס ואינה עומס")

    events = {row["event"] for row in conn_a.execute("SELECT event FROM journal")}
    assert {"sender_event", "member_expired"} <= events, f"שורות חסרות: {events}"
    assert conn_a.execute(
        "SELECT value FROM settings WHERE key = ?", (KEY,)).fetchone()["value"] == "777"
    conn_a.close()
    conn_b.close()


class _ProbeLock:
    """``_write_lock`` שרושם אם לקורא הייתה טרנזאקציה פתוחה בנטילה."""

    def __init__(self, conn):
        self._conn = conn
        self._lock = threading.Lock()
        self.held_write_lock_of_sqlite: list[bool] = []

    def __enter__(self):
        self.held_write_lock_of_sqlite.append(self._conn.in_transaction)
        return self._lock.__enter__()

    def __exit__(self, *exc_info):
        return self._lock.__exit__(*exc_info)

    def locked(self) -> bool:
        return self._lock.locked()


@pytest.mark.parametrize("write", [
    pytest.param(lambda conn: journal(conn, "member_expired", "aa:bb"), id="journal"),
    pytest.param(lambda conn: set_setting(conn, "console_idle_seconds", "60"),
                 id="set_setting"),
    pytest.param(lambda conn: net_seen(conn, "aa:bb:cc:dd:ee:ff", "10.0.0.9"),
                 id="net_seen"),
])
def test_no_write_lock_site_queues_while_holding_the_sqlite_lock(
        tmp_path, monkeypatch, write):
    """התכונה הנצפית: הטרנזאקציה סגורה **לפני** ההמתנה ל-``_write_lock``.

    זו ראיה חלשה יותר מהטסט שלמעלה — היא בודקת את הצורה ולא את הכשל —
    ותפקידה אחר: היא נופלת אם מישהו יחזיר את הסדר הישן באחד משלושת
    האתרים, גם באתר שאין לו היום מסלול ייצור שמגיע אליו עם טרנזאקציה
    פתוחה. שלושתם נבדקים כי כשל באחד אינו מעיד על השאר.
    """
    path = tmp_path / "t.db"
    connect(path)
    conn = _open(str(path))
    probe = _ProbeLock(conn)
    monkeypatch.setattr(db, "_write_lock", probe)

    assert update_one(
        conn, "UPDATE settings SET value = ? WHERE key = ?", ("777", KEY)
    ) is True
    assert conn.in_transaction is True

    write(conn)

    assert probe.held_write_lock_of_sqlite == [False], (
        "\u200f_write_lock נלקח כשנעילת הכתיבה של sqlite כבר ביד — "
        "זה בדיוק ההיפוך של #457, והוא מגיע כ-database is locked")
    conn.close()


def test_a_failed_journal_write_still_reaches_its_caller_with_an_open_transaction(
        tmp_path):
    """`_settle` אינו רשאי לבלוע כשל, גם כשהוא זה שעושה את ה-``commit``.

    `journal` חייב לזרוק ולא לרשום שורה שקטה — עיקרון 5. הטרנזאקציה
    הפתוחה כאן היא ``BEGIN`` בלבד (בלי כתיבה), ולכן ה-``commit`` של
    `_settle` מצליח ואילו הכתיבה עצמה נכשלת מול מחזיק חיצוני — בדיוק
    החלוקה שרוצים לבדוק. חיבור שנשאר בטרנזאקציה אחרי הכשל הוא #272
    בכבודו: משם כל כתיבה עליו נכשלת מיד עד אתחול השרת.
    """
    path = tmp_path / "t.db"
    connect(path)
    victim = _open(str(path))
    victim.execute("PRAGMA busy_timeout = 100")
    victim.execute("BEGIN")
    assert victim.in_transaction is True

    holder = _open(str(path))
    holder.execute("INSERT INTO net_devices (mac, ip) VALUES ('holder', '1')")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            journal(victim, "member_expired", "aa:bb")
        assert victim.in_transaction is False, (
            "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")
    finally:
        holder.rollback()
        holder.close()
        victim.close()
