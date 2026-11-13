"""#2443 — תקרה לכשלונות כניסה **לפי חשבון**, מכל הכתובות יחד.

עד כאן המונה של `login_guard` היה לפי (שם, IP) בלבד: אחרי 10 כשלונות מכתובת A
נעילה, ובאותו רגע מכתובת B הסיסמה הנכונה נכנסה. מי שמנחש סיסמה של admin מכמה
מחשבים (קיוסק ב-8080, או הקונסולה) לא נעצר לעולם.

הכרעות נדב (04/11): MFA למנהלים **נשאר כבוי** כברירת מחדל; אסור שאפשר יהיה לנעול
את המנהל האחרון מחוץ לשרת. לכן הנעילה פגה לבד, והאיפוס הפיזי ב-DCUI מאפס אותה.
הכרעת נדב (05/11): 50 כשלונות ב-15 דקות → החשבון נעול **15 דקות בדיוק, מכל
כתובת** — בלי פטור לכתובת שכבר נכנסה. מי שמגיע לקונסולה נקבע בחומת האש.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import setup_classroom
from server.console import login_guard, users
from server.dcui import actions as dcui_actions
from test_machine_identity import MAC1, identity_server   # noqa: F401

LIMIT = 50
WINDOW = 15 * 60
LOCK = 15 * 60


@pytest.fixture()
def guard_clock(monkeypatch):
    # מתחיל בשעון האמיתי: הכניסות של הפיקסטורה קדמו לשעון המוזרק.
    t = {"now": time.time()}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    return t


def _ip(n: int) -> str:
    return f"10.77.{n // 250}.{n % 250 + 1}"


def _fail_from_many_ips(conn, username: str, count: int = LIMIT) -> None:
    for n in range(count):
        login_guard.record_failure(conn, username, _ip(n))


def _blocked(conn, username: str, ip: str):
    try:
        login_guard.check(conn, username, ip)
    except login_guard.LoginBlocked as exc:
        return exc
    return None


# ---------- login_guard עצמו ----------


def test_failures_from_many_addresses_reach_the_account_cap(server, guard_clock):
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc", LIMIT - 1)
    assert _blocked(conn, "noc", "10.88.0.1") is None
    login_guard.record_failure(conn, "noc", _ip(LIMIT))
    exc = _blocked(conn, "noc", "10.88.0.1")
    assert exc is not None and exc.status == 429
    assert exc.retry_after == LOCK


def test_the_cap_is_per_account_and_case_insensitive(server, guard_clock):
    conn = server["ctx"].conn
    for n in range(LIMIT):
        login_guard.record_failure(conn, "NoC" if n % 2 else "noc", _ip(n))
    assert _blocked(conn, "Noc", "10.88.0.1") is not None
    assert _blocked(conn, "labtech", "10.88.0.1") is None


def test_the_account_lock_expires_by_itself(server, guard_clock):
    """אין נעילה קבועה: 15 דקות, והמנהל האחרון חוזר בלי DCUI."""
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc")
    assert _blocked(conn, "noc", "10.88.0.1") is not None
    guard_clock["now"] += LOCK + 1
    assert _blocked(conn, "noc", "10.88.0.1") is None


def test_the_lock_is_15_minutes_from_the_failure_that_reached_the_cap(server, guard_clock):
    """נדב 05/11: "תנעל את המשתמש ל-15 דקות". כשלונות שנפרסו על 14 דקות אינם
    מקצרים את הנעילה — עד ההכרעה היא נפתחה כשהכשל הראשון יצא מהחלון (כאן: דקה)."""
    conn = server["ctx"].conn
    for n in range(LIMIT):
        if n:
            guard_clock["now"] += 14 * 60 / (LIMIT - 1)
        login_guard.record_failure(conn, "noc", _ip(n))
    guard_clock["now"] += 2 * 60
    exc = _blocked(conn, "noc", "10.88.0.1")
    assert exc is not None and exc.retry_after == LOCK - 2 * 60
    guard_clock["now"] += LOCK - 2 * 60 - 1
    assert _blocked(conn, "noc", "10.88.0.1") is not None
    guard_clock["now"] += 2
    assert _blocked(conn, "noc", "10.88.0.1") is None


def test_after_the_lock_opens_the_count_starts_over(server, guard_clock):
    """הכשלים שגרמו לנעילה אינם נספרים שוב: כשל אחד אחריה אינו נועל מחדש."""
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc")
    guard_clock["now"] += LOCK + 1
    login_guard.record_failure(conn, "noc", "10.88.0.2")
    assert _blocked(conn, "noc", "10.88.0.1") is None


def test_failures_outside_the_window_do_not_add_up(server, guard_clock):
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc", LIMIT - 1)
    guard_clock["now"] += WINDOW + 1
    login_guard.record_failure(conn, "noc", "10.88.0.2")
    assert _blocked(conn, "noc", "10.88.0.1") is None


def test_an_address_that_logged_in_before_is_locked_too(server, guard_clock):
    """נדב 05/11: בלי פטור לכתובת שכבר נכנסה — הנעילה מכל כתובת."""
    conn = server["ctx"].conn
    login_guard.clear(conn, "noc", "10.99.0.5")          # כניסה שהצליחה משם
    _fail_from_many_ips(conn, "noc")
    assert _blocked(conn, "noc", "10.88.0.1") is not None
    assert _blocked(conn, "noc", "10.99.0.5") is not None


def test_an_unknown_username_is_capped_like_a_real_one(server, guard_clock):
    """אחרת התקרה הייתה מגלה אילו שמות קיימים."""
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "ghost")
    assert _blocked(conn, "ghost", "10.88.0.1").status == 429


def test_expired_rows_are_pruned(server, guard_clock):
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc", 5)
    guard_clock["now"] += WINDOW + LOCK + 1
    login_guard.record_failure(conn, "other", "10.88.0.3")
    n_fail = conn.execute("SELECT COUNT(*) FROM login_account_failures").fetchone()[0]
    assert n_fail == 1


def test_reaching_the_cap_is_journaled_once(server, guard_clock):
    conn = server["ctx"].conn
    _fail_from_many_ips(conn, "noc", LIMIT + 3)
    rows = conn.execute(
        "SELECT detail FROM journal WHERE event = 'login_account_lockout'").fetchall()
    assert len(rows) == 1 and "noc" in rows[0]["detail"]


# ---------- הנתיבים ----------


def _distributed_wrong_logins(server, username: str, count: int = LIMIT) -> None:
    """ניחושים דרך `/api/console/login`, כל אחד מכתובת אחרת (כל כתובת: כשל אחד)."""
    for n in range(count):
        c = TestClient(server["app"], client=(_ip(n), 41000 + n))
        r = c.post("/api/console/login", json={"username": username, "password": f"bad-{n}"})
        assert r.status_code == 401, (n, r.text)


def test_console_login_distributed_guessing_is_stopped(server, guard_clock):
    _distributed_wrong_logins(server, "noc")
    fresh = TestClient(server["app"], client=("10.88.0.1", 42000))
    r = fresh.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 429, r.text
    assert int(r.headers["Retry-After"]) > 0
    guard_clock["now"] += LOCK + 1
    r = fresh.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text


def test_console_login_from_the_address_that_logged_in_is_locked_too(server, guard_clock):
    """הפיקסטורה נכנסה כ-noc מהכתובת של `TestClient` הרגיל — גם משם נעול (נדב 05/11)."""
    _distributed_wrong_logins(server, "noc")
    r = server["anon"].post("/api/console/login",
                            json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 429, r.text


def test_me_password_is_capped_for_a_session_from_a_new_address(server, guard_clock):
    _distributed_wrong_logins(server, "noc")
    stolen = TestClient(server["app"], client=("10.88.0.9", 43000))
    stolen.cookies.update(server["admin"].cookies)
    r = stolen.post("/api/console/me/password", json={
        "current_password": "admin-pass-123", "new_password": "Bb12345!"})
    assert r.status_code == 429, r.text
    assert users.verify(server["ctx"].conn, "noc", "admin-pass-123") == "admin"


def test_console_failures_do_not_cap_the_station_login(identity_server, guard_clock):  # noqa: F811
    server = identity_server
    setup_classroom(server)
    _distributed_wrong_logins(server, "labtech")
    r = server["station"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "deploy-pass-1", "mac": MAC1})
    assert r.status_code == 200, r.text


# ---------- #2443 (Fable): רשת ההפצה אינה נועלת את המנהל בקונסולה ----------
# כל הטסטים כאן עוברים דרך HTTP, בלי פרמטר `surface` ישיר: בבקרה השלילית (הקוד
# שלפני התיקון) הם נכשלים על ההתנהגות — קוד תשובה — ולא על חתימת פונקציה.


def test_station_login_failures_never_reach_the_console_cap(identity_server, guard_clock):  # noqa: F811
    """PoC4 של Fable: תחנה עם MAC וחכירה תקינים מנסה את סיסמת המנהל עד הנעילה
    לפי (שם, IP), ויחד איתה 40 כשלונות בקונסולה — 50 על אותו שם, אבל לא בקונסולה."""
    server = identity_server
    for _ in range(login_guard.LOCK_AFTER):
        guard_clock["now"] += 20           # מעבר להשהיה לפי (שם, IP), בתוך החלון
        r = server["station"].post("/api/v1/agent/login", json={
            "username": "noc", "password": "wrong", "mac": MAC1})
        assert r.status_code == 401, r.text
    _distributed_wrong_logins(server, "noc", LIMIT - login_guard.LOCK_AFTER)
    fresh = TestClient(server["app"], client=("10.50.0.77", 42000))
    r = fresh.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 200, r.text


@pytest.fixture()
def split_apps(tmp_path, images_root):
    """הפיצול האמיתי: פורט הסוכן (8080) ופורט הקונסולה (8081) על runtime אחד.
    בלי קובץ חכירות — `identity_gate` אינו חל, ו-`/agent/login` נבדק כאן על הסיסמה."""
    from server.app import create_agent_app, create_console_app, create_runtime
    from test_sender import Recorder
    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        sender_runner=Recorder(block=True))
    users.create(rt.ctx.conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    yield create_agent_app(rt), create_console_app(rt)
    rt.ctx.sender.stop()


GOOD = {"username": "noc", "password": "admin-pass-123"}


def _wrong_logins_on(app, count: int = LIMIT) -> None:
    for n in range(count):
        r = TestClient(app, client=(_ip(n), 41000 + n)).post(
            "/api/console/login", json={"username": "noc", "password": f"bad-{n}"})
        assert r.status_code == 401, (n, r.text)


def _agent_login(app, ip: str):
    return TestClient(app, client=(ip, 42001)).post(
        "/api/v1/agent/login", json={**GOOD, "mac": MAC1})


def test_kiosk_login_on_the_agent_port_does_not_lock_the_console(split_apps, guard_clock):
    """`/api/console/login` חי גם בפורט הסוכן (מחשב הבנייה) — המשטח נקבע לפי הפורט."""
    agent_app, console_app = split_apps
    _wrong_logins_on(agent_app)
    r = TestClient(console_app, client=("10.50.0.77", 42000)).post("/api/console/login", json=GOOD)
    assert r.status_code == 200, r.text


def test_distributed_guessing_on_the_agent_port_is_still_stopped(split_apps, guard_clock):
    """ההגנה בפורט הסוכן נשמרת — בתקרה של המשטח שלו, לכל נתיבי הסיסמה שבו."""
    agent_app, _console_app = split_apps
    _wrong_logins_on(agent_app)
    r = TestClient(agent_app, client=("10.44.0.77", 42001)).post("/api/console/login", json=GOOD)
    assert r.status_code == 429, r.text
    r = _agent_login(agent_app, "10.44.0.78")
    assert r.status_code == 429, r.text
    assert r.json()["code"] == "login_delayed"


def test_console_failures_do_not_cap_the_agent_port(split_apps, guard_clock):
    agent_app, console_app = split_apps
    _wrong_logins_on(console_app)
    r = TestClient(console_app, client=("10.50.0.77", 42000)).post("/api/console/login", json=GOOD)
    assert r.status_code == 429, r.text          # הקונסולה עצמה — חסומה
    r = _agent_login(agent_app, "10.44.0.78")
    assert r.status_code == 200, r.text


def test_the_account_cap_tells_the_admin_what_happened_and_when_it_opens(server, guard_clock):
    """ממצא 4 של Fable: ההתנהגות נשארת; ההודעה אומרת מה קרה ומתי זה נפתח —
    ו-`error` מסמן למסך הכניסה להציג אותה (בלעדיו הוא מציג "החשבון נעול ל-N דקות")."""
    _distributed_wrong_logins(server, "noc")
    fresh = TestClient(server["app"], client=("10.88.0.1", 42000))
    r = fresh.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert r.status_code == 429, r.text
    body = r.json()
    minutes = -(-int(r.headers["Retry-After"]) // 60)
    assert body.get("error") == "login_account_capped", body
    message = body.get("message_he") or ""
    assert f"{LIMIT} ניסיונות כניסה כושלים" in message, message
    assert "החשבון ננעל ל-15 דקות" in message, message
    assert "מכמה כתובות" in message, message
    assert f"בעוד {minutes} דקות" in message, message
    assert body.get("detail") == message      # קורא ישן של `detail` רואה את אותו משפט


# ---------- DCUI ----------


def test_dcui_physical_reset_clears_the_account_cap(server, guard_clock):
    conn = server["ctx"].conn
    assert users.canonical_name(conn, "admin") == "admin"      # ensure_admin של ההתקנה
    _fail_from_many_ips(conn, "admin")
    assert _blocked(conn, "admin", "10.88.0.1") is not None
    result = dcui_actions.physical_reset_admin_password(conn, "New-pass-456!")
    assert result.ok, result
    assert _blocked(conn, "admin", "10.88.0.1") is None
