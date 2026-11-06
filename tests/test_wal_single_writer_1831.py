"""‏#1831 — באג ה-WAL-reset של SQLite (sqlite.org/wal.html §11), ‏3.46.1 בדביאן 13.

הבאג: checkpoint שמתחיל בדיוק כשחיבור אחר מקמט טרנזאקציה שמאפסת את קובץ
ה-WAL מפספס את האיפוס, וה-checkpoint הבא משחית את הקובץ. התנאי הוא **checkpoint
בו-זמנית עם כתיבה** משני חיבורים — ואצלנו יש חיבור לכל תהליכון, וכל commit
שמגיע ל-1000 דפים מריץ checkpoint אוטומטי על החיבור שלו, בלי שום תיאום.

התיקון: כותבים אינם עושים checkpoint לעולם (‏``wal_autocheckpoint = 0``), וה-checkpoint
היחיד (``db.checkpoint``) רץ כשחיבור אחר מחזיק את נעילת הכתיבה של sqlite
(``BEGIN IMMEDIATE``). כך אין commit שיכול לחפוף לו — מאף חיבור ומאף תהליך.

הטסטים כאן בודקים את שני החצאים בהתנהגות: (1) commit גדול אינו נוגע בקובץ
ה-DB; (2) כשה-checkpoint רץ, כותב מבחוץ ננעל בחוץ — וזה נבדק **בתוך** ה-checkpoint,
ברגע שבו הוא רץ, ולא לפניו או אחריו.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import types

import pytest

from server import db
from server.db import connect, journal, set_setting
from server.system import backup, backup_job

#: ‏~4KB לשורה: 1200 שורות הן יותר מ-1000 דפים, הסף של ה-checkpoint האוטומטי.
BLOB = "x" * 4000


class _CheckpointSpy:
    """חיבור שמתעד כל ``wal_checkpoint`` שעובר בו, ובודק **באותו רגע** אם כותב
    מבחוץ מצליח להיכנס. ‏``timeout=0`` — אין המתנה: או שהנעילה פנויה, או שלא."""

    def __init__(self, conn: sqlite3.Connection, path: str, seen: list[str]):
        self._conn = conn
        self._path = path
        self._seen = seen

    def execute(self, sql, parameters=()):
        if "wal_checkpoint" in sql:
            outsider = sqlite3.connect(self._path, timeout=0)
            try:
                outsider.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('outsider', '1')")
                outsider.commit()
                self._seen.append("writer-got-in")
            except sqlite3.OperationalError as exc:
                self._seen.append(f"writer-locked-out: {exc}")
            finally:
                outsider.rollback()
                outsider.close()
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


@pytest.fixture
def spied(monkeypatch, tmp_path):
    """‏Database על קובץ זמני, כשכל חיבור ש-`db._open` פותח מרגל אחרי checkpoint."""
    path = str(tmp_path / "imagesrv.db")
    seen: list[str] = []
    real_open = db._open
    monkeypatch.setattr(db, "_open", lambda p: _CheckpointSpy(real_open(p), str(p), seen))
    return connect(path), path, seen


def test_a_writer_never_checkpoints_on_its_own(tmp_path):
    """‏commit של יותר מ-1000 דפים: בלי התיקון sqlite מריץ checkpoint אוטומטי על
    החיבור הכותב — בלי תיאום עם שום חיבור אחר — והדפים נכתבים לקובץ ה-DB.
    הראיה היא גודל הקובץ עצמו, שנמדד לפני ואחרי."""
    path = tmp_path / "imagesrv.db"
    conn = connect(path)
    before = os.path.getsize(path)
    with db.writing(conn):
        conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)",
                         [(f"bulk-{i}", BLOB) for i in range(1200)])
    after = os.path.getsize(path)
    assert after == before, (
        f"קובץ ה-DB גדל מ-{before} ל-{after} בתוך commit — הכותב הריץ checkpoint בעצמו")
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'bulk-%'").fetchone()[0] == 1200
    assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0, (
        "החיבור עושה checkpoint אוטומטי — כל כותב הוא גם checkpointer (#1831)")


def test_the_checkpoint_shuts_out_every_writer_while_it_runs(spied, monkeypatch):
    """ה-checkpoint המתוזמן רץ, ובזמן שהוא רץ כותב מחיבור זר **אינו** נכנס.

    בלי התיקון אין checkpoint מתוזמן כלל (sqlite עושה אותו בתוך ה-commit, בלי
    לעבור ב-``execute``), ולכן גם "רץ אחד" נכשל. עם checkpoint בלי ה-``BEGIN
    IMMEDIATE`` — הכותב הזר נכנס, וזה בדיוק המרוץ של §11."""
    conn, path, seen = spied
    monkeypatch.setattr(db, "CHECKPOINT_EVERY_COMMITS", 5, raising=False)
    before = os.path.getsize(path)
    for i in range(12):
        set_setting(conn, f"k{i}", BLOB)
    assert seen, "אף checkpoint לא רץ אחרי 12 commits — אין מי שמרוקן את ה-WAL"
    assert all(s.startswith("writer-locked-out") for s in seen), (
        f"כותב זר נכנס בזמן checkpoint — שני חיבורים כותבים/מקמטים יחד: {seen}")
    assert "locked" in seen[0]
    assert conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'outsider'").fetchone()[0] == 0
    # הריגול רץ **לפני** ה-checkpoint האמיתי, ולכן הוא אינו ראיה שהתבצע. הראיה היא
    # הקובץ: הדפים עברו מה-WAL אל ה-DB. ‏checkpoint שנכשל נרשם ביומן בלבד — ואז כאן נופל.
    assert os.path.getsize(path) > before, "ה-checkpoint רץ ולא העביר דף אחד לקובץ ה-DB"


def test_a_checkpoint_reports_every_frame_it_moved(tmp_path):
    """הראיה החיובית של `db.checkpoint`: ‏busy=0, והכול הועבר. ה-WAL לא ריק לפני."""
    path = tmp_path / "imagesrv.db"
    conn = connect(path)
    set_setting(conn, "k", BLOB)
    busy, log_frames, done = db.checkpoint(path)
    assert busy == 0
    assert log_frames > 0 and done == log_frames, (busy, log_frames, done)


def test_a_checkpoint_gives_up_rather_than_overlap_a_writer(tmp_path, monkeypatch):
    """כותב זר מחזיק את הנעילה: ה-checkpoint **נכשל בקול** ולא רץ לצידו, ואחרי
    שהכותב מקמט — שום דף לא הועבר בזמן שהוא כתב."""
    path = tmp_path / "imagesrv.db"
    conn = connect(path)
    set_setting(conn, "k", BLOB)
    size = os.path.getsize(path)
    monkeypatch.setattr(db, "CHECKPOINT_WAIT_MS", 50)
    writer = sqlite3.connect(path)
    writer.execute("INSERT INTO settings (key, value) VALUES ('w', '1')")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db.checkpoint(path)
        assert os.path.getsize(path) == size, "ה-checkpoint רץ לצד כותב פעיל"
    finally:
        writer.rollback()
        writer.close()


def test_a_failed_checkpoint_does_not_fail_the_commit_before_it(tmp_path, monkeypatch, caplog):
    """ה-commit כבר הצליח. ‏checkpoint שנכשל אחריו אינו הופך אותו לכישלון אצל הקורא
    (‏`writing` היה עושה rollback ומדווח כתיבה שנכשלה) — אבל הוא נאמר ביומן השרת."""
    conn = connect(tmp_path / "imagesrv.db")
    monkeypatch.setattr(db, "CHECKPOINT_EVERY_COMMITS", 1)

    def locked(_path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "checkpoint", locked)
    with caplog.at_level("WARNING", logger="imagesrv.db"):
        set_setting(conn, "k", "1")
    assert conn.execute("SELECT value FROM settings WHERE key = 'k'").fetchone()[0] == "1"
    assert "checkpoint skipped" in caplog.text


def test_writers_and_backups_in_parallel_keep_the_database_whole(spied, monkeypatch):
    """ארבעה תהליכונים כותבים ביומן, תהליכון חמישי מגבה בלולאה (‏backup API),
    וה-checkpoint המתוזמן רץ ביניהם. בסוף: כל השורות שם, ‏``integrity_check`` נקי,
    והיו checkpoints — כולם כשהכותבים נעולים בחוץ."""
    conn, path, seen = spied
    monkeypatch.setattr(db, "CHECKPOINT_EVERY_COMMITS", 10, raising=False)
    writers, rows = 4, 60
    failures: list[BaseException] = []
    stop = threading.Event()
    copies: list[int] = []

    def write(n: int) -> None:
        try:
            for i in range(rows):
                journal(conn, "wal_1831", f"{n}-{i}")
        except BaseException as exc:            # noqa: BLE001 — נאסף ונטען
            failures.append(exc)
        finally:
            conn.close()

    def back_up() -> None:
        try:
            while not stop.is_set():
                copies.append(len(backup.consistent_db_copy(conn)))
        except BaseException as exc:            # noqa: BLE001 — נאסף ונטען
            failures.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
    copier = threading.Thread(target=back_up)
    copier.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    stop.set()
    copier.join(timeout=60)
    assert not any(t.is_alive() for t in threads + [copier]), "תהליכון לא סיים"
    assert not failures, f"כשל בכתיבה או בגיבוי: {failures[0]!r}"
    assert copies, "הגיבוי לא רץ אף פעם"
    count = conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'wal_1831'").fetchone()[0]
    assert count == writers * rows
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert seen, "אף checkpoint לא רץ בזמן הכתיבות"
    assert all(s.startswith("writer-locked-out") for s in seen), seen


def test_the_hourly_backup_opens_the_database_read_only(tmp_path, monkeypatch):
    """‏`backup_job` רץ כתהליך נפרד (טיימר systemd) לצד השרת. הוא אינו עובר
    ב-`db.checkpoint`, ולכן הוא חייב להיות **קורא בלבד**: חיבור ``mode=ro`` אינו
    יכול לכתוב ואינו יכול לעשות checkpoint. שומר — אין כאן שינוי התנהגות."""
    data = tmp_path / "data"
    data.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    conn = connect(data / "imagesrv.db")
    set_setting(conn, "x", "1")

    opened: list[str] = []
    real = sqlite3.connect

    def recording(target, *args, **kwargs):
        opened.append(str(target))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(backup_job, "sqlite3", types.SimpleNamespace(connect=recording))
    backup_job.backup_once(data / "imagesrv.db", dest, db.datetime.now(db.timezone.utc))

    to_db = [t for t in opened if "imagesrv.db" in t and "dest" not in t]
    assert to_db, f"‏backup_once לא פתח את המסד בכלל: {opened}"
    assert all(t.startswith("file:") and t.endswith("?mode=ro") for t in to_db), to_db

    ro = real(f"file:{(data / 'imagesrv.db').as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    finally:
        ro.close()
