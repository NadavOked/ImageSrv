"""#272 — מסלול ה-hello מול נעילת הכתיבה של sqlite.

שני כשלים נפרדים, שניהם באותה שורה של ``net_seen``:

1. **כתיבה שנכשלה משאירה טרנזאקציה פתוחה על החיבור.** pysqlite מוציא
   ``BEGIN`` לפני כל DML; אם ה-DML נכשל ב-``database is locked`` איש
   אינו עושה ``rollback``, והחיבור נשאר בתוך טרנזאקציה. מכאן והלאה כל
   כתיבה עליו נכשלת **מיד** — sqlite אינו מפעיל את ה-busy handler
   לשדרוג נעילה בתוך טרנזאקציה פתוחה. תהליכון של uvicorn חי לאורך זמן
   וממוחזר, ולכן אירוע עומס חולף אחד הופך אותו למורעל לצמיתות.

2. **כותבים בתוך התהליך מרעיבים זה את זה.** sqlite אינו מבטיח הוגנות
   בין כותבים: מי שממתין ישן ב-busy handler, ובזמן השינה האחרים כותבים
   שוב ושוב. כשכל commit ארוך (דיסק איטי), ההסתברות שהנעילה תהיה פנויה
   בדיוק ברגע ההתעוררות שואפת לאפס, וההמתנה מתכלה עד ``busy_timeout``
   מלא. `journal` לא סבל מזה מעולם מפני שהוא מסודר מאחורי ``_write_lock``;
   `net_seen` לא היה, וזה בדיוק מה שנפל ב-CI (#272).
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from server.db import _open, connect, net_seen

MAC = "aa:bb:cc:dd:ee:ff"


def test_a_failed_net_seen_leaves_the_connection_usable(tmp_path):
    """כתיבה שנכשלה חייבת להחזיר את החיבור נקי — לא מורעל.

    הראיה חיובית ולא היעדר-חריגה: אחרי שהמחזיק משחרר, **אותו חיבור**
    כותב בהצלחה והשורה נקראת בחזרה. בלי ה-``rollback`` הכתיבה השנייה
    נכשלת מיד, בלי להמתין בכלל.
    """
    path = tmp_path / "t.db"
    connect(path)                      # סכימה

    victim = _open(str(path))
    victim.execute("PRAGMA busy_timeout = 100")   # שלא נחכה 5 שניות בטסט
    holder = _open(str(path))
    # המחזיק תופס את נעילת הכתיבה ולא משחרר — כותב חיצוני, לא דרך net_seen.
    holder.execute("INSERT INTO net_devices (mac, ip) VALUES ('holder', '1')")

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        net_seen(victim, MAC, "10.44.12.50")

    assert victim.in_transaction is False, (
        "נשארה טרנזאקציה פתוחה — מכאן כל כתיבה על החיבור תיכשל מיד")

    holder.rollback()
    holder.close()

    net_seen(victim, MAC, "10.44.12.50")
    row = victim.execute(
        "SELECT ip FROM net_devices WHERE mac = ?", (MAC,)).fetchone()
    assert row is not None and row["ip"] == "10.44.12.50"
    victim.close()


def test_a_slow_write_does_not_starve_the_other_net_seen_writers(tmp_path):
    """כתיבה אחת איטית לא מפילה את שאר הכותבים בתוך התהליך.

    הכשל של #272 תלוי בדיסק: כשכל ``commit`` ארוך, הכותב הממתין מתעורר
    מה-busy handler תמיד לתוך נעילה תפוסה, וההמתנה מתכלה. כאן זה נבנה
    **דטרמיניסטית** במקום להיתלות בדיסק איטי — ה-``commit`` מושהה
    200ms (נעילת הכתיבה של sqlite מוחזקת כל אותו זמן) וה-``busy_timeout``
    של שאר התהליכונים מוקטן ל-50ms. בלי תור הוגן בתוך התהליך, שניים
    מהשלושה נופלים ב-``database is locked``; עם התור הם ממתינים בפייתון
    ועוברים.

    הראיה חיובית: כל השורות באמת נכתבו, לא רק "לא נזרקה חריגה".
    """
    db = connect(tmp_path / "t.db")
    rounds = 3
    failures: list[Exception] = []
    guard = threading.Lock()
    start = threading.Barrier(3)
    real_commit = type(db).commit

    def slow_commit(self):
        time.sleep(0.2)          # נעילת הכתיבה של sqlite מוחזקת כאן
        return real_commit(self)

    def writer(index: int) -> None:
        # ה-PRAGMA חל על החיבור של התהליכון הזה בלבד.
        db.execute("PRAGMA busy_timeout = 50")
        start.wait()
        try:
            for n in range(rounds):
                net_seen(db, f"aa:bb:cc:{index:02x}:00:{n:02x}", "10.44.12.50")
        except Exception as exc:                # noqa: BLE001
            with guard:
                failures.append(exc)

    type(db).commit = slow_commit
    try:
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not any(t.is_alive() for t in threads), "תהליכון נתקע"
    finally:
        type(db).commit = real_commit

    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    written = db.execute(
        "SELECT COUNT(*) AS n FROM net_devices").fetchone()["n"]
    assert written == 3 * rounds
