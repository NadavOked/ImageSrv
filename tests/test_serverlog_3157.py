"""#3157 (אפשרות ג', נדב 14/11) — יומן בשלוש שכבות.

1. הגדרת לוגינג **אחת** בעליית השרת: root ב-INFO ל-stderr (journald), ולאף לוגר
   של השרת אין handler פרטי (השורה הייתה מופיעה פעמיים).
2. WARNING ומעלה של מודולי השרת נכנס ליומן הקונסולה כאירוע אחד עם מקור; אזהרה
   חוזרת היא שורה אחת עם מונה; INFO אינו נכנס.
3. ביומן: "דורש תשומת לב" (warn/err) בשאילתה, ו"הכול" בלעדיו.
4. שורת שגיאה ביומן לא נמחקת לעולם.
"""

from __future__ import annotations

import ast
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db
from server.console import journal_he
from server.system import serverlog

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"
_ALLOWED = SERVER / "system" / "serverlog.py"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "imagesrv.db")


def _rows(conn, event: str | None = None) -> list[dict]:
    sql = "SELECT id, event, detail FROM journal"
    rows = conn.execute(sql + (" WHERE event = ?" if event else "") + " ORDER BY id",
                        (event,) if event else ()).fetchall()
    return [dict(r) for r in rows]


def _record(name: str, level: int, msg: str, *args) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


def _bridge(conn, clock):
    """הגשר בלי תהליכון: `emit` מכניס לתור, והטסט מעביר מהתור ל-`take` בעצמו."""
    return serverlog.JournalBridge(conn, clock=clock, start=False)


def _drain(bridge) -> None:
    while not bridge._queue.empty():
        bridge.take(*bridge._queue.get_nowait())


# --- 1. הגדרה אחת ------------------------------------------------------------

def _private_handlers() -> list[str]:
    """כל `.addHandler(` או `.propagate = False` בקוד השרת, חוץ מההגדרה היחידה."""
    found = []
    for path in sorted(SERVER.rglob("*.py")):
        if path == _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "addHandler"):
                found.append((path, node.lineno, "addHandler"))
            if (isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and t.attr == "propagate" for t in node.targets)):
                found.append((path, node.lineno, "propagate"))
    # ast.walk הוא לרוחב, לא לפי שורות -- הסדר לפי קובץ ושורה
    return [f"{p.relative_to(REPO).as_posix()}:{n} {what}" for p, n, what in sorted(found)]


def test_no_server_module_gives_a_logger_a_private_handler():
    """#3182 הוסיף handler ל-`imagesrv.touch` כי ה-root לא הוגדר. עכשיו הוא מוגדר,
    ו-handler פרטי = כל שורה פעמיים ב-journald. ההגדרה היחידה היא serverlog.py."""
    assert _private_handlers() == []


def test_the_scan_sees_a_private_handler(tmp_path, monkeypatch):
    """בקרה לסריקה עצמה: קובץ עם handler פרטי נמצא בשמו."""
    fake = tmp_path / "server"
    (fake / "core").mkdir(parents=True)
    (fake / "core" / "touch.py").write_text(
        'import logging\nlog = logging.getLogger("imagesrv.touch")\n'
        "log.addHandler(logging.StreamHandler())\nlog.propagate = False\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "SERVER", fake)
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert _private_handlers() == ["server/core/touch.py:3 addHandler",
                                   "server/core/touch.py:4 propagate"]


def test_no_imported_server_logger_has_a_handler_after_startup():
    """אותו דבר בזמן ריצה: אחרי ייבוא השרת ו-`configure`, לוגר של השרת בלי handlers."""
    import server.app  # noqa: F401 -- מייבא את רוב השרת
    import server.main  # noqa: F401
    serverlog.configure()
    loggers = logging.root.manager.loggerDict
    names = [n for n, lg in loggers.items() if serverlog.ours(n) and isinstance(lg, logging.Logger)]
    assert len(names) > 20, names   # הבדיקה רואה את מה שנכתבה בשבילו
    with_handlers = {n: loggers[n].handlers for n in names if loggers[n].handlers}
    assert with_handlers == {}


def test_configure_is_root_at_info_once_and_info_reaches_stderr(capsys):
    serverlog.configure()
    serverlog.configure()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert sum(isinstance(h, serverlog._LiveStderr) for h in root.handlers) == 1
    logging.getLogger("imagesrv.touch").info("touch probe: ms=%s", 7)
    err = capsys.readouterr().err
    assert err.count("INFO imagesrv.touch: touch probe: ms=7") == 1, err


def test_main_configures_logging_before_anything_and_bridges_after_the_runtime():
    """ההגדרה היא הדבר הראשון ב-main, והגשר מחובר רק כשיש DB."""
    src = (SERVER / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def main() -> None:"):]
    assert body.index("serverlog.configure()") < body.index("parser.parse_args()")
    assert body.index("create_runtime(") < body.index("serverlog.bridge_to_journal(runtime.conn)")


# --- 2. WARNING ומעלה ליומן, עם מקור ------------------------------------------

def test_a_warning_becomes_one_journal_row_with_its_source(conn):
    bridge = _bridge(conn, _Clock())
    bridge.emit(_record("imagesrv.wol", logging.WARNING, "no magic packet for %s", "aa:bb"))
    bridge.emit(_record("server.net.dhcp_host", logging.ERROR, "dnsmasq restart failed"))
    _drain(bridge)
    assert [(r["event"], r["detail"]) for r in _rows(conn)] == [
        ("server_warning", "wol: no magic packet for aa:bb"),
        ("server_error", "server.net.dhcp_host: dnsmasq restart failed")]
    assert journal_he.severity("server_warning") == "warn"
    assert journal_he.severity("server_error") == "err"


def test_info_and_foreign_loggers_do_not_reach_the_journal(conn):
    bridge = _bridge(conn, _Clock())
    root = logging.getLogger()
    root.addHandler(bridge)
    try:
        logging.getLogger("imagesrv.touch").info("touch cleanup: ms=3")
        logging.getLogger("uvicorn.error").warning("not ours")
        logging.getLogger("asyncio").error("not ours either")
    finally:
        root.removeHandler(bridge)
    _drain(bridge)
    assert _rows(conn) == []


def test_the_exception_type_is_in_the_row(conn):
    bridge = _bridge(conn, _Clock())
    try:
        raise OSError("disk gone")
    except OSError:
        record = logging.LogRecord("imagesrv.vmdisk", logging.ERROR, __file__, 1,
                                   "copy failed", (), sys.exc_info())
    bridge.emit(record)
    _drain(bridge)
    assert _rows(conn)[0]["detail"] == "vmdisk: copy failed -- OSError: disk gone"


def test_the_real_thread_writes_and_does_not_loop_on_its_own_records(conn):
    """עם התהליכון: השורה נכתבת, ומה שנרשם בתוך הכתיבה (מחיצה מלאה) אינו חוזר לתור."""
    bridge = serverlog.JournalBridge(conn)
    root = logging.getLogger()
    root.addHandler(bridge)
    try:
        logging.getLogger("imagesrv.ups").warning("ups on battery")
    finally:
        root.removeHandler(bridge)
        bridge.close()
    assert [r["detail"] for r in _rows(conn)] == ["ups: ups on battery"]
    assert not bridge._thread.is_alive()


# --- 2ב. הגנה מהצפה ------------------------------------------------------------

def test_a_repeating_warning_is_one_row_with_a_counter(conn):
    """אלף פעמים אותה אזהרה (גם עם ארגומנט אחר) = שורה אחת, והמונה בה."""
    clock = _Clock()
    bridge = _bridge(conn, clock)
    for i in range(1000):
        bridge.emit(_record("imagesrv.rogue_dhcp", logging.WARNING, "foreign DHCP from %s", f"10.0.0.{i % 7}"))
        clock.now += 0.1
        _drain(bridge)
        bridge.flush_counts()
    clock.now += serverlog.GROUP_QUIET_S
    bridge.flush_counts()
    rows = _rows(conn)
    assert len(rows) == 1, rows
    assert rows[0]["detail"] == "rogue_dhcp: foreign DHCP from 10.0.0.0 [+999 identical in 99s]"


def test_the_counter_text_is_translated():
    from server.console.journal_he_detail import he_detail
    assert he_detail("server_warning", "wol: x [+5 identical in 120s]") == \
        "wol: x [חזרה עוד 5 פעמים ב-120 השניות שאחריה]"
    assert he_detail("journal_flood", "server_warning: +3 lost, the queue was full") == \
        "server_warning: 3 הודעות לא נרשמו — התור היה מלא"


def test_the_counter_is_written_while_it_repeats_not_only_at_the_end(conn):
    clock = _Clock()
    bridge = _bridge(conn, clock)
    for _ in range(3):
        bridge.take(("imagesrv.wol", False, "x"), "wol: x")
        clock.now += serverlog.FLUSH_S
        bridge.flush_counts()
    assert _rows(conn)[0]["detail"] == f"wol: x [+2 identical in {2 * serverlog.FLUSH_S}s]"


def test_a_quiet_gap_or_an_hour_starts_a_new_row(conn):
    clock = _Clock()
    bridge = _bridge(conn, clock)
    key = ("imagesrv.wol", False, "x")
    bridge.take(key, "wol: x")
    clock.now += serverlog.GROUP_QUIET_S        # שקט מלא — שורה חדשה
    bridge.take(key, "wol: x")
    for _ in range(int(serverlog.GROUP_MAX_S / 60) + 1):   # כל דקה, יותר משעה
        clock.now += 60
        bridge.take(key, "wol: x")
    assert len(_rows(conn)) == 3


def test_different_templates_are_different_rows(conn):
    bridge = _bridge(conn, _Clock())
    bridge.emit(_record("imagesrv.wol", logging.WARNING, "a %s", 1))
    bridge.emit(_record("imagesrv.wol", logging.WARNING, "b %s", 1))
    bridge.emit(_record("imagesrv.ups", logging.WARNING, "a %s", 1))
    bridge.emit(_record("imagesrv.wol", logging.ERROR, "a %s", 1))
    _drain(bridge)
    assert len(_rows(conn)) == 4


def test_more_new_rows_than_the_budget_are_counted_not_lost(conn):
    """תבניות שונות בלי סוף (f-string) — לכל היותר התקרה בשעה, והשאר נספרים."""
    clock = _Clock()
    bridge = _bridge(conn, clock)
    extra = 5
    for i in range(serverlog.NEW_ROWS_PER_HOUR + extra):
        bridge.take(("imagesrv.x", False, f"msg {i}"), f"x: msg {i}")
    assert len(_rows(conn, "server_warning")) == serverlog.NEW_ROWS_PER_HOUR
    clock.now += serverlog.BUDGET_WINDOW_S
    bridge.flush_counts()
    floods = [r["detail"] for r in _rows(conn, "journal_flood")]
    assert floods == [
        f"server_warning: over {serverlog.NEW_ROWS_PER_HOUR} different in {serverlog.BUDGET_WINDOW_S}s"
        " -- the rest are counted, not journaled one by one",
        f"server_warning: +{extra} more different not journaled one by one"
        f" in the last {serverlog.BUDGET_WINDOW_S}s"]


def test_a_full_queue_is_counted_and_said(conn, monkeypatch):
    monkeypatch.setattr(serverlog, "QUEUE_MAX", 2)
    bridge = _bridge(conn, _Clock())
    for i in range(5):
        bridge.emit(_record("imagesrv.wol", logging.WARNING, f"m{i}"))
    _drain(bridge)
    bridge.flush_counts()
    assert [r["detail"] for r in _rows(conn, "journal_flood")] == [
        "server_warning: +3 lost, the queue was full"]


def test_journal_returns_the_row_id_and_journal_detail_rewrites_it(conn):
    row_id = db.journal(conn, "server_warning", "a")
    assert isinstance(row_id, int)
    assert db.journal_detail(conn, row_id, "b") is True
    assert db.journal_detail(conn, row_id + 999, "c") is False
    assert [r["detail"] for r in _rows(conn)] == ["b"]


# --- 3. "דורש תשומת לב" ב-API -------------------------------------------------

def test_attention_is_warn_and_err_only_in_the_query(server):
    pytest.importorskip("fastapi")
    conn = server["ctx"].conn
    for event in ("login", "wol_sent", "server_warning", "server_error", "unknown_mac"):
        db.journal(conn, event, event)
    admin = server["admin"]
    every = admin.get("/api/console/journal?limit=1000").json()
    shown = admin.get("/api/console/journal?level=attention&limit=1000")
    assert shown.status_code == 200
    got = shown.json()
    assert {r["severity"] for r in got} <= {"warn", "err"}
    assert {r["event"] for r in got} >= {"server_warning", "server_error", "unknown_mac"}
    assert not {r["event"] for r in got} & {"login", "wol_sent"}
    # כל שורה warn/err של "הכול" נמצאת גם ב"דורש תשומת לב" — ולהפך, והסך הוא של הסינון
    assert [r["id"] for r in every if r["severity"] in ("warn", "err")] == [r["id"] for r in got]
    assert shown.headers["X-Journal-Total"] == str(len(got))
    csv = admin.get("/api/console/journal.csv?level=attention").text
    assert "server_error" in csv and ",login," not in csv


def test_an_unknown_level_is_refused_not_ignored(server):
    pytest.importorskip("fastapi")
    assert server["admin"].get("/api/console/journal?level=loud").status_code == 400


def test_attention_needs_admin(server):
    pytest.importorskip("fastapi")
    assert server["deploy"].get("/api/console/journal?level=attention").status_code == 403


# --- 4. שגיאה לא נמחקת ----------------------------------------------------------

def test_an_old_error_row_is_kept_and_an_old_warning_is_purged(conn):
    from server.system import housekeeping
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=housekeeping.JOURNAL_DAYS + 30)).isoformat(timespec="seconds")
    for event in ("server_error", "client_failed", "server_warning", "login"):
        conn.execute("INSERT INTO journal (ts, event, detail) VALUES (?, ?, 'old')", (old, event))
    conn.commit()
    housekeeping.purge_tables(conn, now)
    kept = {r["event"] for r in _rows(conn) if r["detail"] == "old"}
    assert kept == {"server_error", "client_failed"}


def test_every_error_event_is_kept_by_the_rule():
    from server.system import housekeeping
    # לפי שם ולא לפי מקום: #3185 הוסיף כלל לפני כלל היומן בכוונה (סיכום הגדרות לפני מחיקה)
    (sql, params), = [(s, p) for n, s, p in housekeeping._rules(datetime.now(timezone.utc)) if n == "journal"]
    assert "NOT IN" in sql
    assert set(params[1:]) == journal_he.events_of("err")
    assert "server_error" in params


# --- 5. התוויות --------------------------------------------------------------

@pytest.mark.parametrize("event", ["server_warning", "server_error"])
def test_the_new_events_have_hebrew_labels(event):
    assert re.search(r"[֐-׿]", journal_he.EVENTS_HE[event])
