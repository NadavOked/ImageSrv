"""שני תהליכונים על ה-DB — הכשל שנראה בסימולציה כ-500 אקראי.

‏uvicorn מריץ ‎`async def` על לולאת האירועים, אבל ‎`def` רגיל — וגם כל
dependency סינכרוני, כולל ‎`current_user` של הקונסולה — בתהליכון מהמאגר.
כלומר בכל בקשת קונסולה שמתרחשת תוך כדי hello יש שני תהליכונים על ה-DB
באותו רגע. כשהם חלקו חיבור sqlite אחד הם חלקו גם *מצב טרנזאקציה* אחד,
והתוצאה לא הייתה איטיות אלא חריגות אקראיות שיצאו כ-500.

הבדיקות כאן נכשלו על הקוד שלפני ‎db.Database. הן איטיות במכוון: מרוץ
מתגלה בחזרות, לא בקריאה אחת.
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")

from server.db import connect, now_iso

from conftest import hello_body, setup_classroom


def _admin_client(app):
    """לקוח מחובר משלו לכל תהליכון — ‏TestClient אחד משני תהליכונים
    הוא מרוץ בצד הבדיקה, וכאן בודקים את השרת."""
    from fastapi.testclient import TestClient

    from server.console import auth
    client = TestClient(app)
    # לא דרך login: שני תהליכונים באותו חלון TOTP היו נחסמים על קוד כפול (#1085).
    client.cookies.set(auth.COOKIE_NAME, auth.issue(app.state.ctx.conn, "noc", "admin"))
    return client


def _run(workers: int, body) -> list[Exception]:
    """מריץ את `body(index)` בכמה תהליכונים ומחזיר את מה שנפל בהם."""
    failures: list[Exception] = []
    guard = threading.Lock()
    start = threading.Barrier(workers)

    def wrapped(index: int) -> None:
        start.wait()                    # כולם יוצאים יחד — בלי זה אין מרוץ
        try:
            body(index)
        except Exception as exc:        # noqa: BLE001 — זה בדיוק הנאסף
            with guard:
                failures.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,), name=f"w{i}")
               for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "תהליכון נתקע"
    return failures


# --- שכבת ה-DB ---------------------------------------------------------------


def test_each_thread_gets_its_own_connection(tmp_path):
    """אותו אובייקט Database, חיבור אחר בכל תהליכון."""
    db = connect(tmp_path / "t.db")
    # שומרים את אובייקט החיבור עצמו ולא רק את `id()`: ‏id של אובייקט
    # שנאסף לזבל **ממוחזר**, ולכן תהליכון שסיים יכול לתרום id שחיבור
    # חדש יקבל אחריו — ושני תהליכונים ייראו זהים בלי שהיו. הכשל הזה
    # נצפה על main ב-`assert 4 == 5`.
    seen: dict[str, object] = {}
    guard = threading.Lock()

    def collect(index: int) -> None:
        with guard:
            seen[threading.current_thread().name] = db.connection

    _run(4, collect)
    seen["main"] = db.connection
    assert len({id(c) for c in seen.values()}) == len(seen) == 5

    # ואותו תהליכון מקבל את אותו חיבור בחזרה — לא חיבור חדש לכל שאילתה.
    assert db.connection is db.connection


def test_concurrent_writers_do_not_break_each_other(tmp_path):
    """המסלול של hello (net_seen) משלושה תהליכונים במקביל.

    על חיבור משותף זה נפל בכ-15% מהכתיבות: "cannot start a transaction
    within a transaction", "cannot commit - no transaction is active",
    "bad parameter or other API misuse".
    """
    from server.db import net_seen

    db = connect(tmp_path / "t.db")
    rounds = 200

    def writer(index: int) -> None:
        for n in range(rounds):
            net_seen(db, f"aa:bb:cc:{index:02x}:{n // 256:02x}:{n % 256:02x}",
                     "10.44.12.50")

    failures = _run(3, writer)
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    # ראיה חיובית: כל השורות באמת שם, לא רק "לא נזרקה חריגה".
    written = db.execute("SELECT COUNT(*) AS n FROM net_devices").fetchone()["n"]
    assert written == 3 * rounds


def test_journal_survives_concurrent_writers(tmp_path):
    """היומן נכתב גם מתהליכון השידור וגם מהבקשות — ובלי לאבד שורות."""
    from server.db import journal

    db = connect(tmp_path / "t.db")
    rounds = 150

    def writer(index: int) -> None:
        for n in range(rounds):
            journal(db, "sim_event", f"w{index} #{n}")

    failures = _run(3, writer)
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    rows = db.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'sim_event'"
    ).fetchone()["n"]
    assert rows == 3 * rounds


def test_a_rejected_conditional_update_releases_the_write_lock(tmp_path):
    """‏UPDATE שלא תאם אף שורה חייב לשחרר את הנעילה — זה היה השורש.

    ‏sqlite פותח טרנזאקציה על כל DML ואוחז בנעילת הכתיבה מרגע ההצהרה,
    גם כשאף שורה לא תאמה. עם חיבור משותף איש לא הרגיש; עם חיבור לכל
    תהליכון, קורא שהפסיד במרוץ וזרק חריגה השאיר **נעילה יתומה**, וכל
    השאר קיבלו "database is locked" אחרי המתנת busy_timeout שלמה.

    הראיה כאן חיובית ולא היעדר-חריגה: התהליכון השני באמת כותב, והוא
    עושה את זה **מהר** — כישלון היה נראה כהמתנה של שניות ואז חריגה.
    """
    from server.db import update_one

    db = connect(tmp_path / "t.db")
    loser_done = threading.Event()
    release = threading.Event()
    state: dict[str, object] = {}

    def loser() -> None:
        # בדיוק המסלול של המפסיד: כתיבה מותנית שלא תפסה שורה.
        state["changed"] = update_one(
            db, "UPDATE groups SET label = ? WHERE id = ?", ("x", "no_such_group"))
        state["in_transaction"] = db.connection.in_transaction
        loser_done.set()
        release.wait(30)        # מחזיק את החיבור פתוח — כמו תהליכון מהמאגר

    thread = threading.Thread(target=loser, name="loser")
    thread.start()
    try:
        assert loser_done.wait(30), "התהליכון לא סיים"
        assert state["changed"] is False
        assert state["in_transaction"] is False, "נשארה טרנזאקציה פתוחה"

        # ותהליכון אחר כותב עכשיו, בלי להמתין לנעילה.
        started = time.monotonic()
        db.execute("INSERT INTO journal (ts, user, event, detail)"
                   " VALUES (?, '', 'lock_probe', '')", (now_iso(),))
        db.commit()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        thread.join(timeout=30)

    assert elapsed < 1.0, f"הכתיבה המתינה {elapsed:.1f}s — הנעילה עדיין מוחזקת"


# --- דרך ה-API, כמו בסימולציה ------------------------------------------------


def test_console_polling_during_hello_never_500s(server):
    """בדיוק מה שנפל ב-CI: מבט-על נדגם תוך כדי hello של הכיתה.

    ‏`/api/console/overview` הוא ‎`def` (תהליכון מהמאגר) ו-‎`/agent/hello`
    הוא ‎`async def` (לולאת האירועים). שניהם כותבים. כל תשובה שאינה
    ‏200 כאן היא הכשל שהצריך הרצה חוזרת ב-GitHub Actions.
    """
    from fastapi.testclient import TestClient

    ids = setup_classroom(server)
    app = server["app"]
    assert server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 99},
    ).status_code == 200
    consoles = [_admin_client(app)]

    statuses: list[tuple[str, int]] = []
    guard = threading.Lock()

    def record(label: str, status: int) -> None:
        with guard:
            statuses.append((label, status))

    def agent(index: int) -> None:
        mac = ids["mac1"] if index == 0 else ids["mac2"]
        client = TestClient(app)
        for _ in range(40):
            record("hello", client.post("/api/v1/agent/hello",
                                        json=hello_body(mac)).status_code)

    def console(index: int) -> None:
        client = consoles[0]
        for _ in range(40):
            record("overview", client.get("/api/console/overview").status_code)

    failures = _run(3, lambda i: agent(i) if i < 2 else console(i))
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    bad = [entry for entry in statuses if entry[1] != 200]
    assert not bad, f"{len(bad)} תשובות שאינן 200 מתוך {len(statuses)}: {bad[:5]}"


def test_the_session_starts_once_when_two_threads_ripen_it(server):
    """שני מסלולים מגיעים לתנאי ההתחלה יחד — אחד מנצח, איש לא נכשל.

    המעבר open→running הוא UPDATE מותנה, ולכן המפסיד מקבל rowcount=0.
    בהתחלה אוטומטית זה לא כישלון אלא מרוץ שנגמר טוב; הוא מאמת בקריאה
    חוזרת שהסבב אכן רץ, ורק אז ממשיך.
    """
    from fastapi.testclient import TestClient

    ids = setup_classroom(server)
    app = server["app"]
    assert server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).status_code == 200

    statuses: list[int] = []
    guard = threading.Lock()
    # לקוח נפרד לכל תהליכון, גם בצד הבדיקה.
    clients = [TestClient(app), _admin_client(app), _admin_client(app)]

    def ripen(index: int) -> None:
        # ‏hello של מכונה אחת מספיק כדי להבשיל סבב של 1/1; המבט-על
        # והתחנה מגיעים לאותו maybe_start מתהליכונים אחרים.
        client = clients[index]
        for _ in range(30):
            if index == 0:
                status = client.post("/api/v1/agent/hello",
                                     json=hello_body(ids["mac1"])).status_code
            else:
                status = client.get("/api/console/overview").status_code
            with guard:
                statuses.append(status)

    failures = _run(3, ripen)
    assert not failures, f"{len(failures)} כשלים, הראשון: {failures[0]!r}"
    assert all(status == 200 for status in statuses), sorted(set(statuses))

    ctx = server["ctx"]
    assert ctx.store.active()["state"] == "running"
    started = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'session_start_auto'"
    ).fetchone()["n"]
    assert started == 1, "הסבב התחיל יותר מפעם אחת"
