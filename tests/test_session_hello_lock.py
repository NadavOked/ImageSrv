"""‏#290 — רצף שתי הכתיבות ב-`record_hello` אינו משאיר נעילה יתומה.

זו אותה משפחה של #54, ‏#184 ו-#200, במקום שעוד לא נבדק: **רצף של שתי
כתיבות ואז `commit`**. הכתיבה הראשונה (‏`INSERT INTO session_members`)
תופסת את נעילת הכתיבה; אם השנייה (‏`UPDATE sessions`) זורקת, איש אינו
עושה `rollback`. החיבור הוא של התהליכון (`db.Database`) והוא **נשאר
בחיים**, ולכן הנעילה נשארת תפוסה עד הכתיבה המוצלחת הבאה *באותו
תהליכון* — ובינתיים כל השאר מקבלים ``database is locked``.

זה ה**נתיב החם**: ‏`record_hello` הוא ההצטרפות לסבב, וכיתה שלמה דורכת
עליו במקביל. כשל כזה נראה כמו עומס, והוא בסך הכל מנעול שאיש לא שחרר.

**מה נבדק כאן, ומה לא.** ‏`pytest.raises` על החריגה **אינו** בקרה
שלילית: החריגה מתפשטת בשני המצבים. מה שנשבר בלי `writing()` הוא
**הנעילה**, ולכן הראיה היא **כותב שני על חיבור שני** — כתיבה שהצליחה
וערך שנקרא בחזרה, ולא היעדר סימן כישלון (עיקרון 5). ה-`busy_timeout`
שלו **קצר בכוונה**; הארכתו הייתה הופכת את הבאג לאיטי במקום למנוע אותו.

**מה ש-`test_a_single_failed_write_was_never_the_problem` תופס** הוא
הגבול של הממצא, והוא עובר בשני המצבים בכוונה: משפט **בודד** שנכשל אינו
משאיר נעילה (הוא לא תפס אותה). בלעדיו אפשר היה לחשוב שכל חריגה נועלת,
ולתקן את המקום הלא נכון.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.db import connect
from server.rounds.sessions import SessionStore

GROUP = "grp_LAB1"
IMAGE = "img_7f3a91"
MAC = "aa:bb:cc:dd:ee:01"

#: קצר בכוונה: הכישלון של הבקרה השלילית לוקח חמישית שנייה ולא חמש.
WRITER_TIMEOUT_MS = 200

#: המשפט השני ברצף — זה שנפילתו משאירה את הראשון עם הנעילה.
SECOND_WRITE = "UPDATE sessions SET last_join_at"
#: המשפט הראשון. נפילתו היא הבקרה: אין כתיבה קודמת שתחזיק נעילה.
FIRST_WRITE = "INSERT INTO session_members"


class FailingWrite:
    """חיבור שמתנהג כרגיל עד שהמשפט המסומן מגיע — ואז חריגה שאינה
    ‏`IntegrityError`, בדיוק כמו ``disk I/O error`` שמגיע מהעולם האמיתי
    אל תוך רצף הכתיבות.

    ‏`SessionStore` נוגע בחיבור רק דרך ``execute`` / ``commit`` /
    ``rollback``, ולכן זה כל מה שצריך לעטוף.
    """

    def __init__(self, conn, marker: str):
        self._conn = conn
        self._marker = marker
        self.fired = 0

    def execute(self, sql: str, parameters=()):
        if self._marker in " ".join(sql.split()):
            self.fired += 1
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, parameters)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


@pytest.fixture()
def two_writers(tmp_path: Path):
    """שני חיבורים על אותו קובץ — המצב שבו הבאג חי.

    ‏`db.Database` נותן חיבור לכל תהליכון, ולכן נעילה יתומה של אחד היא
    ``database is locked`` אצל כל השאר. חיבור אחד לא היה מדגים דבר:
    הוא רואה את הטרנזאקציה של עצמו.
    """
    path = tmp_path / "hello.db"
    db = connect(path)
    db.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
               (GROUP, "כיתה"))
    db.commit()

    other = sqlite3.connect(path)
    other.row_factory = sqlite3.Row
    other.execute(f"PRAGMA busy_timeout = {WRITER_TIMEOUT_MS}")
    yield db, other
    other.close()
    db.close()


def _open_round(store: SessionStore) -> sqlite3.Row:
    sid = store.open(GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc")
    return store.conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def _hello_with_failure(db, marker: str):
    """‏hello אחד שנופל על המשפט המסומן. מוחזר ה-store וה-wrapper."""
    store = SessionStore(db)
    session = _open_round(store)
    failing = FailingWrite(db, marker)
    store.conn = failing
    with pytest.raises(sqlite3.OperationalError):
        store.record_hello(session, MAC)
    return store, failing, session


def _second_writer_works(other: sqlite3.Connection, value: str) -> str:
    """כותב שני: כתיבה, ‏commit, וקריאה חזרה — ראיה חיובית ולא היעדר סימן."""
    other.execute("INSERT INTO settings (key, value) VALUES ('probe', ?)"
                  " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (value,))
    other.commit()
    return other.execute(
        "SELECT value FROM settings WHERE key = 'probe'").fetchone()["value"]


def test_a_failed_second_write_releases_the_write_lock(two_writers):
    """הכתיבה השנייה נופלת — והכותב הבא עדיין כותב.

    בלי `writing()` הכתיבה הזו נופלת ב-``database is locked`` אחרי
    ``busy_timeout`` שלם, וזה בדיוק #54 בנתיב ה-hello.
    """
    db, other = two_writers
    _hello_with_failure(db, SECOND_WRITE)

    # קודם כותבים, ואז מאמתים: עם `-O` ה-assert נעלם ואיתו הכתיבה עצמה
    # (‏`py/side-effect-in-assert`), והכתיבה הזו **היא** המדידה.
    read_back = _second_writer_works(other, "1")
    assert read_back == "1", \
        "הכותב השני נחסם — הנעילה של הכתיבה הראשונה נשארה יתומה"


def test_a_failed_hello_leaves_no_half_join(two_writers):
    """שהתקלה נורתה, ושהרצף כולו נסוג — לא רק חלקו.

    בלי הנסיגה ה-`INSERT` נשאר תלוי בטרנזאקציה פתוחה, כלומר מכונה
    ש"הצטרפה" בלי שהטיימר אופס. הקריאה היא מחיבור ה-store עצמו
    בכוונה: טרנזאקציה פתוחה רואה את הכתיבה של עצמה, ולכן בלי ה-rollback
    מוחזר כאן `True` — מהחיבור השני זה היה נראה זהה בשני המצבים.
    """
    db, other = two_writers
    store, failing, session = _hello_with_failure(db, SECOND_WRITE)

    assert failing.fired == 1, "התקלה לא נורתה — הטסט לא בדק דבר"
    store.conn = db
    assert not store.is_member(session["id"], MAC), \
        "המכונה נרשמה כמצטרפת אף שההצטרפות נכשלה"
    assert other.execute(
        "SELECT COUNT(*) AS n FROM session_members").fetchone()["n"] == 0, \
        "נכתבה שורת הצטרפות אף שהרצף נכשל"


def test_a_single_failed_write_was_never_the_problem(two_writers):
    """הבקרה שממקדת את הממצא — עוברת גם לפני התיקון, בכוונה.

    כשהמשפט ה**ראשון** נופל אין כתיבה קודמת שתחזיק נעילה, ולכן הכותב
    השני חופשי גם בלי `writing()`. זה מה שמראה שהסיכון הוא **ברצפים**
    ולא בכל חריגה, ומונע תיקון של המקום הלא נכון.
    """
    db, other = two_writers
    _, failing, _ = _hello_with_failure(db, FIRST_WRITE)

    assert failing.fired == 1, "התקלה לא נורתה — הטסט לא בדק דבר"
    read_back = _second_writer_works(other, "2")
    assert read_back == "2", "משפט בודד שנכשל תפס נעילה — ההנחה של #290 שגויה"
