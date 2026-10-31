"""המרוץ בתפיסת חריץ — שידור (#103) ומשיכה (#104).

שני הפגמים הם אותו דפוס: קוראים, מחליטים, ואז כותבים — ובין הקריאה
לכתיבה רצות עוד שאילתות. ‏uvicorn מריץ כל `def` רגיל בתהליכון מהמאגר
ו-`db.Database` נותן חיבור לכל תהליכון, ולכן שני פותחים הם שני כותבים
בלי טרנזאקציה משותפת. הכשל אינו איטיות אלא **שתי שורות פעילות**: סבב
רפאים שחוסם את כל הבאים, או משיכה שנייה שאיש לא ידווח אליה.

הבדיקות כאן פותחות את החלון ביד ולא מקוות לתזמון: התהליכון הראשון
נעצר בתוך `get_setting` — בדיוק בין הבדיקה ל-INSERT — והשני מספיק
לסיים. על הקוד שלפני האינדקס הייחודי שתיהן נכשלות.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from server import db as db_module
from server.rounds import pulls, sessions
from server.db import connect, now_iso
from server.rounds.sessions import MULTICAST, UNICAST, SessionError, SessionStore

GROUP = "grp_LAB1"
MAC = "b4:2e:99:07:1a:c4"
OTHER_MAC = "b4:2e:99:07:1a:c5"
IMAGE = "img_7f3a91"
OTHER_IMAGE = "img_2c8e04"

#: ההודעות שהמפעיל רואה, מילה במילה. כתובות כאן כטקסט ולא מיובאות
#: מ-`sessions.TAKEN`: מה שנשמר הוא מה שמופיע על המסך, לא שם הקבוע
#: שמאחוריו — וכך הקובץ הזה רץ, ונכשל, גם על הקוד שלפני התיקון.
BROADCAST_TAKEN = "כבר יש סבב פעיל — לעולם לא יותר מאחד בו-זמנית"
PULL_TAKEN = "התחנה הזו כבר מושכת אימג' — יש לחכות לסיום"


# --- תשתית -------------------------------------------------------------------


def _fresh_db(tmp_path: Path):
    db = connect(tmp_path / "race.db")
    db.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
               (GROUP, "כיתה"))
    for mac, suffix in ((MAC, "05"), (OTHER_MAC, "06")):
        db.execute(
            "INSERT INTO machines (mac, suffix, group_id, added_at)"
            " VALUES (?, ?, ?, ?)", (mac, suffix, GROUP, now_iso()))
    db.commit()
    return db


def _active(db, kind: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT id, roster_json FROM sessions WHERE kind = ?"
        " AND state IN ('open', 'running')", (kind,)
    ).fetchall()


class Gate:
    """עוצר את התהליכון **הראשון** שמגיע לחלון עד שמשחררים אותו ביד.

    בלי זה המרוץ הוא הימור על מתזמן התהליכונים; איתו הוא תרחיש אחד
    ומוגדר, ואפשר לומר מי ניצח ומי הפסיד.
    """

    def __init__(self) -> None:
        self.inside = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._taken = False

    def trip(self) -> None:
        with self._lock:
            first, self._taken = not self._taken, True
        if first:
            self.inside.set()
            assert self.release.wait(30), "השחרור לא הגיע"


def _gate_the_window(monkeypatch, gate: Gate) -> None:
    """‏`get_setting` נקראת בתוך `SessionStore.open`, אחרי הבדיקה ולפני
    ה-INSERT — כלומר בדיוק בחלון שבו המרוץ קורה."""
    real = db_module.get_setting

    def gated(conn, key):
        value = real(conn, key)
        if key == "session_wait_seconds":
            gate.trip()
        return value

    monkeypatch.setattr(sessions, "get_setting", gated)


def _both(first, second, gate: Gate) -> None:
    """מריץ את `first` עד שהוא בתוך החלון, נותן ל-`second` לסיים, ואז
    משחרר. שני תהליכונים, ולכן שני חיבורים."""
    slow = threading.Thread(target=first, name="slow")
    slow.start()
    try:
        assert gate.inside.wait(30), "התהליכון הראשון לא הגיע לחלון"
        fast = threading.Thread(target=second, name="fast")
        fast.start()
        fast.join(timeout=30)
        assert not fast.is_alive(), "התהליכון השני נתקע"
    finally:
        gate.release.set()
        slow.join(timeout=30)
    assert not slow.is_alive(), "התהליכון הראשון נתקע"


# --- ‏#103: חריץ השידור --------------------------------------------------------


def test_two_threads_opening_a_broadcast_leave_exactly_one(tmp_path, monkeypatch):
    """המפסיד מקבל SessionError, ובטבלה יש שורת multicast פעילה אחת.

    בלי האינדקס הייחודי שניהם מצליחים, והשני הופך לסבב רפאים:
    ‏`active_broadcast` מחזירה ‏`LIMIT 1`, אז אף מכונה לא תצטרף אליו,
    וכשהראשון ייסגר הוא יחסום כל פתיחה עתידית.
    """
    db = _fresh_db(tmp_path)
    gate = Gate()
    _gate_the_window(monkeypatch, gate)
    outcome: dict[str, object] = {}

    def opener(name: str):
        def run() -> None:
            store = SessionStore(db)
            try:
                outcome[name] = store.open(GROUP, IMAGE, "LAB1", 2, name)
            except SessionError as exc:
                outcome[name] = exc
        return run

    _both(opener("slow"), opener("fast"), gate)

    assert isinstance(outcome["fast"], str), outcome["fast"]
    loser = outcome["slow"]
    assert isinstance(loser, SessionError), f"שני הפותחים הצליחו: {outcome}"
    # ההודעה היא זו שהמפעיל כבר מכיר — לא "IntegrityError".
    assert str(loser) == BROADCAST_TAKEN
    rows = _active(db, MULTICAST)
    assert len(rows) == 1 and rows[0]["id"] == outcome["fast"]


def test_repeated_broadcast_races_never_leave_two(tmp_path):
    """אותו מרוץ בלי חלון מלאכותי, בחזרות. מרוץ מתגלה בחזרות."""
    db = _fresh_db(tmp_path)
    start = threading.Barrier(2)
    for _ in range(25):
        opened: list[str] = []
        refused: list[SessionError] = []
        broke: list[Exception] = []
        guard = threading.Lock()

        def run() -> None:
            store = SessionStore(db)
            start.wait(30)
            try:
                sid = store.open(GROUP, IMAGE, "LAB1", 2, "noc")
            except SessionError as exc:
                with guard:
                    refused.append(exc)
            except Exception as exc:        # noqa: BLE001 — לא נבלע בתהליכון
                with guard:
                    broke.append(exc)
            else:
                with guard:
                    opened.append(sid)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "תהליכון נתקע"
        assert not broke, f"חריגה שאינה SessionError: {broke[0]!r}"
        assert len(opened) == 1, f"{len(opened)} סבבים נפתחו בו-זמנית"
        assert len(refused) == 1 and str(refused[0]) == BROADCAST_TAKEN
        assert len(_active(db, MULTICAST)) == 1
        SessionStore(db).close(opened[0], "noc")


# --- ‏#104: משיכה לאותה תחנה ---------------------------------------------------


def test_two_threads_pulling_for_one_station_share_one_pull(tmp_path, monkeypatch):
    """ה-retry של curl: אותה בקשה פעמיים. נפתחת משיכה אחת, ושתי
    התשובות נושאות את אותו `id`.

    בלי התיקון נפתחות שתיים: הסוכן ידווח לאחת, והשנייה תישאר `running`
    לנצח ותחסום את התחנה מכל משיכה עתידית.
    """
    db = _fresh_db(tmp_path)
    gate = Gate()
    _gate_the_window(monkeypatch, gate)
    outcome: dict[str, object] = {}

    def puller(name: str):
        def run() -> None:
            store = SessionStore(db)
            try:
                outcome[name] = pulls.open_pull(db, store, MAC, GROUP, IMAGE, name)
            except SessionError as exc:
                outcome[name] = exc
        return run

    _both(puller("slow"), puller("fast"), gate)

    assert isinstance(outcome["fast"], str), outcome["fast"]
    assert isinstance(outcome["slow"], str), outcome["slow"]
    assert outcome["slow"] == outcome["fast"], "נפתחו שתי משיכות שונות"
    rows = _active(db, UNICAST)
    assert len(rows) == 1, f"{len(rows)} משיכות פעילות לאותה תחנה"
    # ראיה חיובית שזה נרשם ולא קרה בשקט.
    assert db.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'pull_retry'"
    ).fetchone()["n"] == 1


def test_repeated_pull_races_never_open_two(tmp_path):
    db = _fresh_db(tmp_path)
    start = threading.Barrier(2)
    for _ in range(25):
        ids: list[str] = []
        broke: list[Exception] = []
        guard = threading.Lock()

        def run() -> None:
            store = SessionStore(db)
            start.wait(30)
            try:
                sid = pulls.open_pull(db, store, MAC, GROUP, IMAGE, "labtech")
            except Exception as exc:        # noqa: BLE001 — לא נבלע בתהליכון
                with guard:
                    broke.append(exc)
            else:
                with guard:
                    ids.append(sid)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "תהליכון נתקע"
        assert not broke, f"בקשה חוזרת נכשלה: {broke[0]!r}"
        assert len(ids) == 2 and len(set(ids)) == 1, f"שתי משיכות שונות: {ids}"
        assert len(_active(db, UNICAST)) == 1
        SessionStore(db).close(ids[0], "noc")


def test_a_pull_of_another_image_is_still_refused(tmp_path):
    """החסימה לא נעלמה: אותה תחנה, אימג' אחר — סירוב עם ההסבר."""
    db = _fresh_db(tmp_path)
    store = SessionStore(db)
    pulls.open_pull(db, store, MAC, GROUP, IMAGE, "labtech")
    with pytest.raises(SessionError) as caught:
        pulls.open_pull(db, store, MAC, GROUP, OTHER_IMAGE, "labtech")
    assert str(caught.value) == PULL_TAKEN
    assert len(_active(db, UNICAST)) == 1


def test_a_pull_that_already_reported_is_not_reused(tmp_path):
    """משיכה שדיווחה משהו היא עבודה קיימת, לא retry — גם אותו אימג'."""
    db = _fresh_db(tmp_path)
    store = SessionStore(db)
    session_id = pulls.open_pull(db, store, MAC, GROUP, IMAGE, "labtech")
    db.execute(
        "UPDATE session_members SET bytes_written = 4096, updated_at = ?"
        " WHERE session_id = ?", (now_iso(), session_id))
    db.commit()
    with pytest.raises(SessionError) as caught:
        pulls.open_pull(db, store, MAC, GROUP, IMAGE, "labtech")
    assert str(caught.value) == PULL_TAKEN


def test_two_stations_pull_side_by_side(tmp_path):
    """התחנה היא הגבול, לא השרת: שתי תחנות מושכות במקביל (#60)."""
    db = _fresh_db(tmp_path)
    store = SessionStore(db)
    first = pulls.open_pull(db, store, MAC, GROUP, IMAGE, "labtech")
    second = pulls.open_pull(db, store, OTHER_MAC, GROUP, IMAGE, "labtech")
    assert first != second
    assert len(_active(db, UNICAST)) == 2


# --- המיגרציה ----------------------------------------------------------------


def _legacy_row(conn, sid: str, kind: str, created: str, roster: str | None) -> None:
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, roster_json,"
        " kind) VALUES (?, ?, ?, 'LAB1', 1, 300, 'open', 'noc', ?, 0.0, ?, ?)",
        (sid, GROUP, IMAGE, created, roster, kind),
    )


def test_an_existing_db_with_duplicates_still_comes_up(tmp_path):
    """שרת שעולה על DB שכבר מכיל את הכפילויות — יצירת האינדקס הייתה
    נכשלת, והשרת לא היה עולה בכלל. הכפילויות נסגרות, ביומן, והראשון
    (לפי `created_at` — בדיוק הסדר של `active_broadcast`) נשאר."""
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db_module.SCHEMA)
    raw.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
                (GROUP, "כיתה"))
    _legacy_row(raw, "ses_first", MULTICAST, "2026-08-29T08:00:00+00:00", None)
    _legacy_row(raw, "ses_ghost", MULTICAST, "2026-08-29T09:00:00+00:00", None)
    roster = f'["{MAC}"]'
    _legacy_row(raw, "ses_pull1", UNICAST, "2026-08-29T08:00:00+00:00", roster)
    _legacy_row(raw, "ses_pull2", UNICAST, "2026-08-29T09:00:00+00:00", roster)
    raw.commit()
    raw.close()

    db = connect(path)                       # לא נופל

    assert [row["id"] for row in _active(db, MULTICAST)] == ["ses_first"]
    assert [row["id"] for row in _active(db, UNICAST)] == ["ses_pull1"]
    closed = {row["detail"].split(" ")[0] for row in db.execute(
        "SELECT detail FROM journal WHERE event = 'session_dedupe'")}
    assert closed == {"ses_ghost", "ses_pull2"}


def test_old_state_checks_are_rebuilt_without_losing_rounds_or_members(tmp_path):
    path = tmp_path / "old-state-checks.db"
    legacy = db_module.SCHEMA.replace(
        "('open', 'running', 'failed', 'closed')",
        "('open', 'running', 'closed')",
    ).replace(
        "('active', 'closing', 'failed', 'closed')",
        "('active', 'closed')",
    ).replace(
        "    failed_reason    TEXT,\n",
        "",
    ).replace(
        "    closed_at       TEXT,\n    failed_reason   TEXT\n);",
        "    closed_at       TEXT\n);",
    )
    raw = sqlite3.connect(path)
    raw.executescript(legacy)
    raw.execute(
        "INSERT INTO groups (id, label, role) VALUES (?, ?, 'classroom')",
        (GROUP, "כיתה"),
    )
    _legacy_row(raw, "ses_old", MULTICAST, "2026-09-20T08:00:00+00:00", None)
    raw.execute(
        "INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, ?)",
        ("ses_old", MAC, "2026-09-20T08:01:00+00:00"),
    )
    raw.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state,"
        " wave_session_id, opened_by, created_at)"
        " VALUES ('room_old', ?, 2, 'active', 'ses_old', 'noc', ?)",
        (IMAGE, "2026-09-20T08:00:00+00:00"),
    )
    raw.commit()
    raw.close()

    db = connect(path)
    assert db.execute(
        "SELECT mac FROM session_members WHERE session_id = 'ses_old'"
    ).fetchone()["mac"] == MAC
    db.execute(
        "UPDATE sessions SET state = 'failed', failed_reason = 'sender stopped'"
        " WHERE id = 'ses_old'"
    )
    db.execute(
        "UPDATE room_rounds SET state = 'closing' WHERE id = 'room_old'"
    )
    db.commit()
    assert db.execute(
        "SELECT state, failed_reason FROM sessions WHERE id = 'ses_old'"
    ).fetchone()["state"] == "failed"
    assert db.execute(
        "SELECT state FROM room_rounds WHERE id = 'room_old'"
    ).fetchone()["state"] == "closing"
    db.execute(
        "UPDATE room_rounds SET state = 'failed', failed_reason = 'sender stopped'"
        " WHERE id = 'room_old'"
    )
    db.commit()
    failed_room = db.execute(
        "SELECT state, failed_reason FROM room_rounds WHERE id = 'room_old'"
    ).fetchone()
    assert failed_room["state"] == "failed"
    assert failed_room["failed_reason"] == "sender stopped"
    indexes = {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    )}
    assert {"one_active_broadcast", "one_active_pull_per_station"} <= indexes
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_the_indexes_exist_and_actually_block(tmp_path):
    """ראיה חיובית: לא "לא נזרקה חריגה" אלא האינדקס בסכימה, והוא דוחה."""
    db = _fresh_db(tmp_path)
    names = {row["name"] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"one_active_broadcast", "one_active_pull_per_station"} <= names

    _legacy_row(db, "ses_a", MULTICAST, now_iso(), None)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _legacy_row(db, "ses_b", MULTICAST, now_iso(), None)
    db.rollback()

    roster = f'["{MAC}"]'
    _legacy_row(db, "ses_p1", UNICAST, now_iso(), roster)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _legacy_row(db, "ses_p2", UNICAST, now_iso(), roster)
    db.rollback()
    # תחנה אחרת אינה מתנגשת.
    _legacy_row(db, "ses_p3", UNICAST, now_iso(), f'["{OTHER_MAC}"]')
    db.commit()
