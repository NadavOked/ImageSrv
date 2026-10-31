"""‏#200 — הבקרה השלילית ל-`except Exception: rollback` ב-`open(replaces=)`.

השומר נכנס ל-main ב-#194 **בלי שאיש ראה אותו נכשל**, וזה בדיוק מה
ש-`CONTRIBUTING.md` פוסל: *"טסט שלא ראית נכשל אינו טסט"*.

**מה נבדק כאן, ומה לא.** ‏`pytest.raises` על החריגה **אינו** בקרה
שלילית: החריגה מתפשטת גם בלי ה-rollback. מה שנשבר בלעדיו הוא **הנעילה**
— ‏`open(replaces=...)` פותח `BEGIN IMMEDIATE` במפורש, ולכן נעילת
הכתיבה נתפסת עוד לפני הכתיבה הראשונה. חריגה שיוצאת מהפונקציה בלי
‏commit/rollback לוקחת אותה איתה, והכותב הבא ממתין `busy_timeout` שלם
ואז מקבל ``database is locked`` — כשל שנראה כמו עומס והוא קורא שכבר
ויתר (#54, ‏#184).

לכן הראיה כאן היא **כותב שני על חיבור שני**: כתיבה שהצליחה וערך שנקרא
בחזרה, ולא היעדר סימן כישלון (עיקרון 5). ה-`busy_timeout` שלו **קצר
בכוונה** — מאתיים אלפיות שנייה — כדי שהכישלון יהיה מהיר וחד; הארכתו
הייתה הופכת את הבאג לאיטי במקום למנוע אותו.

**הבקרה השלילית שהורצה** (‏02/09, תחנת פיתוח ווינדוס, פייתון 3.12):
הסרת ה-``except Exception`` מ-`server/rounds/sessions.py` — ‏`grep -c` על
``self.conn.rollback()`` ירד **מ-2 ל-1** ועל ``except Exception``
**מ-1 ל-0**, כלומר ראיה חיובית שהשומר באמת איננו — ואז::

    E   sqlite3.OperationalError: database is locked
    E   assert 'closed' == 'open'
    2 failed

עם השומר: ``2 passed``. שני הכשלים הם **על התנהגות** — נעילה שנשארה
תפוסה, וסגירה שלא נסוגה — ולא ‏`ImportError` או קובץ חסר.

**ומה שעבר בשני המצבים:** האסרשנים על **מספר הסבבים בטבלה** ועל
``failing.fired`` — הם השומרים מפני תיקון-יתר ומפני מעבר בריק, ולא
הבקרה. ‏`fired` מוודא שהתקלה אכן נורתה פעם אחת (טסט שלא ירה בה היה
"עובר" בלי לבדוק דבר), והספירה מוודאת שאף סבב שני לא נכתב.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.db import connect
from server.rounds.sessions import SessionStore

GROUP = "grp_LAB1"
IMAGE = "img_7f3a91"

#: קצר בכוונה: הכישלון של הבקרה השלילית לוקח חמישית שנייה ולא חמש.
WRITER_TIMEOUT_MS = 200


class FailingWrite:
    """חיבור שמתנהג כרגיל עד שהמשפט המסומן מגיע — ואז חריגה שאינה
    ‏`IntegrityError`, בדיוק כמו ``disk I/O error`` או ``database is
    locked`` שמגיעים מהעולם האמיתי אל תוך הטרנזאקציה המפורשת.

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
    path = tmp_path / "lock.db"
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


def _open_wave(store: SessionStore) -> str:
    return store.open(GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc")


def _second_writer_works(other: sqlite3.Connection, value: str) -> str:
    """כותב שני: כתיבה, ‏commit, וקריאה חזרה — ראיה חיובית ולא היעדר סימן."""
    other.execute("INSERT INTO settings (key, value) VALUES ('probe', ?)"
                  " ON CONFLICT (key) DO UPDATE SET value = excluded.value", (value,))
    other.commit()
    return other.execute(
        "SELECT value FROM settings WHERE key = 'probe'").fetchone()["value"]


def test_a_failure_inside_the_explicit_transaction_releases_the_write_lock(
        two_writers):
    """חריגה שאינה `IntegrityError` בתוך `BEGIN IMMEDIATE` — והכותב הבא כותב.

    בלי ה-`except Exception` הכתיבה הזו נופלת ב-``database is locked``
    אחרי ``busy_timeout`` שלם, וזה בדיוק #54.
    """
    db, other = two_writers
    store = SessionStore(db)
    wave = _open_wave(store)

    store.conn = FailingWrite(db, "INSERT INTO sessions")
    with pytest.raises(sqlite3.OperationalError):
        store.open(GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc",
                   replaces=wave)

    # קודם כותבים, ואז מאמתים: עם `-O` ה-assert נעלם ואיתו הכתיבה עצמה
    # (‏`py/side-effect-in-assert`), והכתיבה הזו **היא** המדידה.
    read_back = _second_writer_works(other, "1")
    assert read_back == "1", \
        "הכותב השני נחסם — הנעילה של הטרנזאקציה המפורשת נשארה יתומה"


def test_the_failed_transaction_leaves_nothing_behind(two_writers):
    """שהתקלה באמת נורתה, ושכל הטרנזאקציה נסוגה — לא רק חלקה.

    בלי זה הטסט הראשון היה יכול לעבור בריק: משפט שלא התאים למסמן,
    חריגה שנזרקה לפני ה-`BEGIN IMMEDIATE`, או סגירה שהספיקה להיכתב.
    """
    db, other = two_writers
    store = SessionStore(db)
    wave = _open_wave(store)

    failing = FailingWrite(db, "INSERT INTO sessions")
    store.conn = failing
    with pytest.raises(sqlite3.OperationalError):
        store.open(GROUP, IMAGE, "LAB", expected_clients=2, opened_by="noc",
                   replaces=wave)

    assert failing.fired == 1, "התקלה לא נורתה — הטסט לא בדק דבר"
    # הסגירה המותנית שקדמה ל-INSERT היא חלק מאותה טרנזאקציה, ולכן
    # נסיגה מלאה משאירה את הגל **פתוח**. הקריאה היא מחיבור ה-store
    # עצמו בכוונה: טרנזאקציה פתוחה רואה את הכתיבה של עצמה, ולכן בלי
    # ה-rollback מוחזר כאן `closed` — מהחיבור השני זה היה נראה זהה
    # בשני המצבים.
    state = db.execute(
        "SELECT state FROM sessions WHERE id = ?", (wave,)).fetchone()["state"]
    assert state == "open", f"הגל שהוחלף נשאר במצב {state!r} אחרי כישלון"
    assert other.execute(
        "SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 1, \
        "נכתב סבב שני אף שהטרנזאקציה נכשלה"
