"""‏#1892 — מחיצת ה-DB מלאה: היומן לא מפיל את hello, והבריאות אדומה.

שני סוגי מחיצה מלאה כאן:

* **אמיתי** — ‏``PRAGMA max_page_count`` על חיבור אמיתי, ושורה שדורשת דפים
  חדשים. זה מה שמוכיח ש-sqlite אכן מחזיר ``sqlite_errorcode == SQLITE_FULL``,
  והזיהוי בקוד נשען על הקוד ולא על נוסח ההודעה.
* **מוזרק** לאפליקציה המלאה — כל כתיבה (‏INSERT/UPDATE/DELETE/REPLACE) זורקת
  את אותה חריגה, וקריאות עוברות. כך נראה דיסק מלא מבחוץ: קוראים, לא כותבים.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login, hello_body

from server import db as db_module
from server.db import connect, journal

UNKNOWN = "aa:bb:cc:00:18:92"
GIB = 1024 ** 3


def _full_error() -> sqlite3.OperationalError:
    exc = sqlite3.OperationalError("database or disk is full")
    exc.sqlite_errorcode = sqlite3.SQLITE_FULL
    return exc


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # מצב הכשל האחרון הוא של התהליך; טסט אחד לא מדליק אדום לאחר.
    if hasattr(db_module, "_last_full"):
        monkeypatch.setattr(db_module, "_last_full", {})


def _really_full(path: Path) -> sqlite3.Connection:
    """חיבור שה-DB שלו לא יכול לגדול בדף אחד נוסף."""
    connect(path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    conn.execute(f"PRAGMA max_page_count = {pages}")
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    return conn


def test_sqlite_reports_a_full_database_by_error_code(tmp_path):
    conn = _really_full(tmp_path / "imagectl.db")
    with pytest.raises(sqlite3.OperationalError) as caught:
        conn.execute("INSERT INTO journal (ts, user, event, detail) VALUES ('t', '', 'x', ?)",
                     ("y" * 65536,))
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_FULL


def test_journal_on_a_full_database_does_not_raise_and_is_recorded(tmp_path):
    conn = _really_full(tmp_path / "imagectl.db")
    journal(conn, "unknown_mac", "y" * 65536)          # לא זורק
    assert not conn.in_transaction                       # ‏rollback — אין נעילה יתומה
    assert db_module.last_disk_full()["what"] == "journal unknown_mac"
    # כתיבה מוצלחת היא הראיה שהמחיצה התפנתה — האדום נמחק.
    journal(conn, "unknown_mac", "short")
    assert db_module.last_disk_full() is None


def test_a_journal_error_that_is_not_disk_full_still_raises(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "x.db"))     # אין טבלת journal
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        journal(conn, "e", "d")


@pytest.fixture()
def app_full(tmp_path: Path, images_root: Path, clock, monkeypatch):
    """אפליקציה מלאה; ‏`state["full"] = True` הופך כל כתיבה ל-SQLITE_FULL."""
    from server import sender as sender_module
    from server import users
    from server.app import create_app

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    state = {"full": False, "free": 50 * GIB}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock,
                     health_hooks={"interfaces": lambda: [],
                                   "db_free": lambda data_dir: state["free"]})
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, ctx.conn, "noc", "admin-pass-123")

    real_execute = db_module.Database.execute

    def execute(self, sql, parameters=()):
        verb = sql.lstrip().split(None, 1)[0].upper()
        if state["full"] and verb in ("INSERT", "UPDATE", "DELETE", "REPLACE"):
            raise _full_error()
        return real_execute(self, sql, parameters)

    monkeypatch.setattr(db_module.Database, "execute", execute)
    yield {"app": app, "ctx": ctx, "admin": admin, "agent": TestClient(app), "state": state}
    ctx.sender.stop()


def _health(admin) -> dict:
    return {r["id"]: r for r in admin.get("/api/console/health").json()}


def test_hello_from_an_unregistered_machine_on_a_full_disk_is_known_false(app_full):
    app_full["state"]["full"] = True
    r = app_full["agent"].post("/api/v1/agent/hello", json=hello_body(UNKNOWN))
    assert r.status_code == 200, r.text
    assert r.json()["known"] is False


def test_a_full_disk_is_red_in_health_until_a_write_succeeds(app_full):
    admin, state = app_full["admin"], app_full["state"]
    assert _health(admin)["db_space"]["state"] == "ok"
    state["full"] = True
    assert app_full["agent"].post("/api/v1/agent/hello",
                                  json=hello_body(UNKNOWN)).status_code == 200
    state["full"] = False
    row = _health(admin)["db_space"]
    assert row["state"] == "bad", row
    assert "המחיצה מלאה" in row["detail"]
    journal(app_full["ctx"].conn, "test", "space freed")
    assert _health(admin)["db_space"]["state"] == "ok"


def test_a_hello_whose_answer_needs_a_write_is_a_declared_503(app_full, monkeypatch):
    from server import api

    def needs_write(*a, **k):
        raise _full_error()

    monkeypatch.setattr(api, "build_answer", needs_write)
    r = app_full["agent"].post("/api/v1/agent/hello", json=hello_body(UNKNOWN))
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "db_full"


@pytest.mark.system_reserve          # הרזרבה האמיתית, ‏10 GiB (conftest מאפס אותה)
def test_db_partition_thresholds_in_health(app_full):
    admin, state = app_full["admin"], app_full["state"]
    state["free"] = 512 * 1024 ** 2
    assert _health(admin)["db_space"]["state"] == "bad"
    state["free"] = 5 * GIB
    assert _health(admin)["db_space"]["state"] == "warn"
    state["free"] = None
    assert _health(admin)["db_space"]["state"] == "unknown"
