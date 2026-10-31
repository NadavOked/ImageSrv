"""‏#1008 — פערי ה-API של דפי ההרשאות והיומן (‏#954 גל 6).

1. ‏`GET /users` → ‏`last_login_at` + ‏`last_login_from` לכל משתמש. נכתבים
   ב-`users` בכל כניסה שהנפיקה עוגייה — **לא** מ-`console_sessions` (#1039),
   שנמחקת ביציאה ובתפוגה, ולא מהיומן, ששורת ה-`login` שלו אינה נושאת
   כתובת. ‏null = "לא נרשמה כניסה" (משתמש חדש, או התקנה מלפני #1008) — לא 0.
3. ‏`/journal` → ‏`id` בכל שורה, ‏`before=<id>` לדפדוף, ו-`X-Journal-Total`.
4. ‏`GET /journal.csv` — אותם פרמטרים, אותה הרשאה (admin בלבד).
"""

from __future__ import annotations

import csv
import io

import pytest

pytest.importorskip("fastapi")

from conftest import complete_console_login  # noqa: E402
from server.console import totp as totp_mod  # noqa: E402
from server.console import users  # noqa: E402
from server.app import create_console_app  # noqa: E402
from server.db import journal  # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


def _users(server) -> dict:
    r = server["admin"].get("/api/console/users")
    assert r.status_code == 200, r.text
    return {u["username"]: u for u in r.json()}


# --- 1. כניסה אחרונה ומאיפה ---------------------------------------------------


def test_user_who_never_logged_in_has_null_last_login_not_missing(server):
    users.create(server["ctx"].conn, "fresh", "fresh-pass-123", "admin", by="test",
                 check_policy=False)
    row = _users(server)["fresh"]
    assert "last_login_at" in row and row["last_login_at"] is None
    assert "last_login_from" in row and row["last_login_from"] is None


def test_login_records_when_and_from_where(server):
    """ה-fixture כבר נכנס כ-noc מ-``testclient`` (‏request.client.host)."""
    row = _users(server)["noc"]
    assert row["last_login_from"] == "testclient"
    assert row["last_login_at"] and row["last_login_at"].endswith("+00:00")


def test_failed_login_does_not_move_last_login(server):
    users.create(server["ctx"].conn, "fresh", "fresh-pass-123", "admin", by="test",
                 check_policy=False)
    other = TestClient(server["app"], client=("10.44.10.66", 50000))
    assert other.post("/api/console/login",
                      json={"username": "fresh", "password": "wrong"}).status_code == 401
    row = _users(server)["fresh"]
    assert row["last_login_at"] is None and row["last_login_from"] is None


def test_password_alone_does_not_count_until_the_second_factor(server):
    """משתמש עם MFA: ‏`/login` הוא חצי כניסה (``mfa_required``, בלי עוגייה).
    הכתובת מתעדכנת רק כש-`/login/mfa` מנפיק את העוגייה."""
    # ‏`create_app` המשולב שם את הקיוסק ראשון (בלי MFA) — אפליקציית הקונסולה
    # בנפרד, כמו ב-test_login_mfa.
    conn = server["ctx"].conn
    runtime = server["app"].state.runtime
    # נדב 18/10: החיוב כבוי כברירת מחדל — כאן הוא דלוק, כדי ש-mfa2 יירשם ב-MFA בכניסה הראשונה
    from server.console import console_mfa
    from server.db import set_setting
    set_setting(conn, console_mfa.SETTING, "true")
    users.create(conn, "mfa2", "Aa12345!-x", "admin", by="test", check_policy=False)
    enrol = TestClient(create_console_app(runtime), client=("10.44.10.66", 50000))
    complete_console_login(enrol, conn, "mfa2", "Aa12345!-x")
    assert _users(server)["mfa2"]["last_login_from"] == "10.44.10.66"

    remote = TestClient(create_console_app(runtime), client=("10.44.10.55", 50000))
    first = remote.post("/api/console/login",
                        json={"username": "mfa2", "password": "Aa12345!-x"}).json()
    assert first.get("mfa_required"), first
    assert _users(server)["mfa2"]["last_login_from"] == "10.44.10.66"
    secret = conn.execute("SELECT mfa_secret FROM users WHERE username = 'mfa2'"
                          ).fetchone()["mfa_secret"]
    done = remote.post("/api/console/login/mfa",
                       json={"challenge": first["challenge"],
                             "code": totp_mod.totp_code(secret)})
    assert done.status_code == 200, done.text
    assert _users(server)["mfa2"]["last_login_from"] == "10.44.10.55"


def test_users_is_admin_only(server):
    assert server["deploy"].get("/api/console/users").status_code == 403
    assert server["anon"].get("/api/console/users").status_code == 401


# --- 3. דפדוף ביומן -----------------------------------------------------------


def _seed(conn, n: int) -> None:
    for i in range(n):
        journal(conn, "machine_add", f"aa:bb:cc:dd:ee:{i:02x} name={i}", "noc")


def test_journal_rows_carry_their_id_and_before_pages_backwards(server):
    conn = server["ctx"].conn
    _seed(conn, 5)
    admin = server["admin"]

    def page(**extra):
        r = admin.get("/api/console/journal",
                      params={"event": "machine_add", "limit": 2, **extra})
        assert r.status_code == 200, r.text
        return [row["text"].split(" ")[0] for row in r.json()], r.json()

    macs1, rows1 = page()
    assert macs1 == ["aa:bb:cc:dd:ee:04", "aa:bb:cc:dd:ee:03"]
    assert all(isinstance(r["id"], int) for r in rows1)
    assert rows1[0]["id"] > rows1[1]["id"]
    macs2, rows2 = page(before=rows1[-1]["id"])
    assert macs2 == ["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:01"]
    # שורה חדשה בין שני דפים אינה מזיזה את הדף הבא (סמן, לא offset).
    journal(conn, "machine_add", "aa:bb:cc:dd:ee:99 name=late", "noc")
    macs3, _ = page(before=rows2[-1]["id"])
    assert macs3 == ["aa:bb:cc:dd:ee:00"]


def test_journal_total_counts_every_matching_row_not_the_page(server):
    _seed(server["ctx"].conn, 5)
    r = server["admin"].get("/api/console/journal",
                            params={"event": "machine_add", "limit": 2})
    assert r.headers["X-Journal-Total"] == "5"
    # ‏before מזיז את הדף, לא את הסך.
    last = r.json()[-1]["id"]
    r2 = server["admin"].get("/api/console/journal",
                             params={"event": "machine_add", "limit": 2, "before": last})
    assert r2.headers["X-Journal-Total"] == "5"


def test_journal_total_is_absent_when_the_filter_is_on_translated_text(server):
    """מכונה/חיפוש מסוננים על הטקסט המתורגם, לא בשאילתה — הסך אינו ידוע
    בלי לתרגם את כל היומן. כותרת חסרה = "לא ידוע", לא 0 ולא אורך הדף."""
    _seed(server["ctx"].conn, 3)
    r = server["admin"].get("/api/console/journal", params={"q": "name=1"})
    assert r.status_code == 200
    assert "X-Journal-Total" not in r.headers


# --- 4. ייצוא CSV --------------------------------------------------------------


def test_journal_csv_has_the_same_rows_as_the_screen(server):
    conn = server["ctx"].conn
    _seed(conn, 3)
    journal(conn, "setting_change", 'x="a, b"', "noc")
    admin = server["admin"]
    params = {"event": "machine_add", "limit": 2}
    screen = admin.get("/api/console/journal", params=params).json()
    r = admin.get("/api/console/journal.csv", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="imagesrv-journal.csv"'
    assert r.headers["X-Journal-Total"] == "3"
    text = r.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [(x["id"], x["ts"], x["user"], x["event"], x["label"], x["text"])
            for x in rows] == [(str(s["id"]), s["ts"], s["user"], s["event"],
                                s["label"], s["text"]) for s in screen]


def test_journal_csv_quotes_commas_and_defuses_formulas(server):
    conn = server["ctx"].conn
    # ‏login_failed נושא את השם שהוקלד במסך הכניסה — טקסט של מי שלא נכנס.
    journal(conn, "login_failed", "=HYPERLINK(\"http://x\",\"a, b\")", "")
    r = server["admin"].get("/api/console/journal.csv",
                            params={"event": "login_failed"})
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0]["text"] == "'=HYPERLINK(\"http://x\",\"a, b\")"


def test_journal_csv_is_admin_only(server):
    assert server["deploy"].get("/api/console/journal.csv").status_code == 403
    assert server["anon"].get("/api/console/journal.csv").status_code == 401
    assert server["deploy"].get("/api/console/journal").status_code == 403
