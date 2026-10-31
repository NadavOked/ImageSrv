"""‏#356 — שני האתרים שנשארו על מסלול ה-hello מול נעילת הכתיבה של sqlite.

‏#272 מצא שני באגים ב-``net_seen`` ותיקן אותם שם. ‏`agent_loops._count`
ו-`bootguard._record` הם אותה צורה בדיוק, על אותו מסלול, ולא תוקנו:

1. **כתיבה שנכשלה מרעילה את החיבור.** ‏pysqlite מוציא ``BEGIN`` לפני כל
   DML. אם ה-DML נכשל ב-``database is locked`` ואיש אינו עושה
   ``rollback``, החיבור נשאר **בתוך טרנזאקציה**. מכאן והלאה sqlite אינו
   מפעיל את ה-busy handler לשדרוג נעילה בתוך טרנזאקציה פתוחה, וכל כתיבה
   נכשלת **מיד** — גם כשהמחזיק משחרר חצי שנייה אחר כך. תהליכון של
   uvicorn ממוחזר, ולכן אירוע עומס חולף אחד היה משתיק את השומר עד אתחול
   התהליך.

2. **כותבים בתוך התהליך מרעיבים זה את זה.** ‏sqlite אינו מבטיח הוגנות:
   הממתין ישן ב-busy handler ובזמן השינה האחרים כותבים שוב. ``_write_lock``
   הוא התור ההוגן בתוך התהליך, ו-`journal` מאחוריו מאז ומתמיד — שני
   האתרים כאן לא היו.

‏`bootguard.guard` הוא השומר של האתחול, ולכן עיקרון 5 חל עליו במלוא
הכובד: שומר שהפסיק לכתוב אינו "שומר שלא מצא כלום".

הכשל נבנה **דטרמיניסטית** ולא נתלה בדיסק איטי: מחזיק חיצוני תופס את
נעילת הכתיבה, ו-``busy_timeout`` של הקורבן מוקטן.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from server.net import foreign_vlan
from server.agent.agent_loops import note
from server.agent.bootguard import guard
from server.db import _open, connect

MAC = "aa:bb:cc:dd:ee:ff"

#: כתובת מקומית של השרת שאינה וילן ההפצה — כך `foreign_vlan.note` מגיע
#: אל הכתיבה (#518). ה-scope הוא ה-sockname של החיבור, לא כותרת לקוח.
OFF_VLAN_SCOPE = {"server": ("10.10.10.8", 8080)}

#: תשובת שרת שמסתיימת בדיסק מקומי, למכונה שאינה רשומה לקבוצה —
#: ‏`unexplained` מחזיר עליה True בלי לגעת ב-`store` בכלל.
LOCAL_ANSWER = {"known": True, "role": "classroom"}

#: תשובת שרת שמסתיימת בסוכן ויש לה הקשר תחום בזמן — זה מה ש-`guard` סופר.
AGENT_ANSWER = {"known": True, "role": "classroom", "task": {"id": "t1"}}


def _poison(path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """קורבן עם ``busy_timeout`` קצר, ומחזיק שתופס את נעילת הכתיבה.

    המחזיק כותב לטבלה אחרת ואינו עובר דרך הקוד הנבדק — הוא רק עומס.
    """
    victim = _open(str(path))
    victim.execute("PRAGMA busy_timeout = 100")
    holder = _open(str(path))
    holder.execute("INSERT INTO net_devices (mac, ip) VALUES ('holder', '1')")
    return victim, holder


def _hold_briefly(path, seconds: float) -> threading.Thread:
    """תופס את נעילת הכתיבה ל-``seconds`` ומשחרר. חיבור נקי ימתין ויעבור."""
    grabbed = threading.Barrier(2)

    def run() -> None:
        conn = _open(str(path))
        conn.execute("INSERT INTO net_devices (mac, ip) VALUES ('brief', '2')")
        grabbed.wait()
        time.sleep(seconds)
        conn.rollback()
        conn.close()

    thread = threading.Thread(target=run)
    thread.start()
    grabbed.wait()
    return thread


def test_a_failed_agent_loops_note_leaves_the_connection_usable(tmp_path):
    """‏hello שלא נספר אינו רשאי להשתיק את המונה לתמיד.

    הראיה חיובית ולא היעדר-חריגה: אחרי כשל אחד, **אותו חיבור** סופר
    hello חדש תחת עומס חולף, והערך נקרא בחזרה. בלי ה-``rollback``
    החיבור נשאר בטרנזאקציה, sqlite אינו מפעיל את ה-busy handler,
    והספירה נכשלת מיד.
    """
    path = tmp_path / "t.db"
    connect(path)                                  # סכימה
    victim, holder = _poison(path)

    assert note(victim, None, MAC, LOCAL_ANSWER) is None    # הכתיבה נכשלה
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        assert note(victim, None, MAC, LOCAL_ANSWER) == 1
    finally:
        brief.join(timeout=30)

    row = victim.execute(
        "SELECT hits FROM agent_loops WHERE mac = ?", (MAC,)).fetchone()
    assert row is not None and row["hits"] == 1
    victim.close()


def test_a_failed_bootguard_write_leaves_the_connection_usable(tmp_path):
    """שומר האתחול שנכשל פעם אחת חייב לחזור לספור — עיקרון 5.

    שומר שקט אינו שומר: חיבור מורעל פירושו ש-`guard` יסמן
    ``exhausted`` לכל מכונה עד אתחול השרת, כלומר כיתה שלמה שנשלחת
    לדיסק המקומי באמצע סבב.
    """
    path = tmp_path / "t.db"
    connect(path)
    victim, holder = _poison(path)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        guard(victim, MAC, AGENT_ANSWER)
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        assert guard(victim, MAC, AGENT_ANSWER) == AGENT_ANSWER
    finally:
        brief.join(timeout=30)

    row = victim.execute(
        "SELECT attempts, context FROM boot_attempts WHERE mac = ?",
        (MAC,)).fetchone()
    assert row is not None and row["attempts"] == 1 and row["context"] == "task:t1"
    victim.close()


def test_a_failed_foreign_vlan_note_leaves_the_connection_usable(tmp_path):
    """‏hello מרשת זרה שלא נספר אינו רשאי להשתיק את השורה האדומה לתמיד.

    ‏`foreign_vlan._count` היה הכותב היחיד במסלול ה-hello שלא עבר ב-
    ``writing`` (#518): ``conn.execute`` + ``conn.commit`` עירומים, וה-
    ``except`` שב-`note` בולע **בלי** ``rollback``. הכתיבה שנכשלה השאירה
    את החיבור בטרנזאקציה, sqlite הפסיק להפעיל את ה-busy handler, וכל
    hello נוסף מאותו תהליכון נכשל מיד — וכשהאימות של `net_seen` שאחריו
    זרק, ה-hello עצמו החזיר 500. הבליעה "כדי לא להפיל hello" היא שהפילה
    אותו. הראיה חיובית: אחרי כשל אחד, **אותו חיבור** סופר מרשת זרה שוב
    תחת עומס חולף, והערך נקרא בחזרה.
    """
    path = tmp_path / "t.db"
    connect(path)                                  # סכימה
    victim, holder = _poison(path)

    assert foreign_vlan.note(
        victim, MAC, OFF_VLAN_SCOPE, off_vlan=True) is None    # הכתיבה נכשלה
    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    victim.execute("PRAGMA busy_timeout = 5000")
    brief = _hold_briefly(path, 0.5)
    try:
        assert foreign_vlan.note(
            victim, MAC, OFF_VLAN_SCOPE, off_vlan=True) == 1
    finally:
        brief.join(timeout=30)

    row = victim.execute(
        "SELECT hits FROM off_vlan_contacts WHERE mac = ?", (MAC,)).fetchone()
    assert row is not None and row["hits"] == 1
    victim.close()


def _starve(db, work) -> list[Exception]:
    """שלושה תהליכונים כותבים יחד בזמן שכל ``commit`` לוקח 200ms.

    נעילת הכתיבה של sqlite מוחזקת כל אותו זמן, ו-``busy_timeout`` של כל
    תהליכון מוקטן ל-50ms. בלי תור הוגן בתוך התהליך רובם נופלים.
    """
    failures: list[Exception] = []
    guard_lock = threading.Lock()
    start = threading.Barrier(3)
    real_commit = type(db).commit

    def slow_commit(self):
        time.sleep(0.2)
        return real_commit(self)

    def writer(index: int) -> None:
        db.execute("PRAGMA busy_timeout = 50")     # החיבור של התהליכון הזה
        start.wait()
        try:
            for n in range(3):
                work(db, index, n)
        except Exception as exc:                   # noqa: BLE001
            with guard_lock:
                failures.append(exc)

    type(db).commit = slow_commit
    try:
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)
        assert not any(t.is_alive() for t in threads), "תהליכון נתקע"
    finally:
        type(db).commit = real_commit
    return failures


def test_agent_loops_note_does_not_starve_its_own_writers(tmp_path):
    """כיתה שלמה שולחת hello יחד — וכל אחד מהם נספר.

    הראיה חיובית: כל תשע השורות נמצאות. ‏`note` בולע חריגות במכוון
    (ניטור לא מפיל hello), ולכן "לא נזרקה חריגה" אינו אומר כלום כאן —
    מה שנספר הוא מה שנכתב.
    """
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: note(conn, None, f"aa:bb:cc:{i:02x}:00:{n:02x}",
                                    LOCAL_ANSWER))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute("SELECT COUNT(*) AS n FROM agent_loops").fetchone()["n"]
    assert written == 9


def test_bootguard_guard_does_not_starve_its_own_writers(tmp_path):
    """אותו דבר לשומר האתחול — כיתה שמאתחלת יחד היא המקרה הרגיל."""
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: guard(conn, f"aa:bb:cc:{i:02x}:00:{n:02x}",
                                     AGENT_ANSWER))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute("SELECT COUNT(*) AS n FROM boot_attempts").fetchone()["n"]
    assert written == 9


def test_foreign_vlan_note_does_not_starve_its_own_writers(tmp_path):
    """כמה מחשבים מרשת זרה פונים יחד — וכל פנייה שנספרה באמת נכתבה.

    הראיה חיובית: כל תשע השורות נמצאות. ‏`note` בולע חריגות במכוון
    (ניטור לא מפיל hello), ולכן "לא נזרקה חריגה" אינו אומר כלום כאן —
    מה שנספר הוא מה שנכתב.
    """
    db = connect(tmp_path / "t.db")
    failures = _starve(
        db, lambda conn, i, n: foreign_vlan.note(
            conn, f"aa:bb:cc:{i:02x}:00:{n:02x}", OFF_VLAN_SCOPE, off_vlan=True))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute(
        "SELECT COUNT(*) AS n FROM off_vlan_contacts").fetchone()["n"]
    assert written == 9
