"""#2528 (שלב 1) — הכרעות נדב מ-12/11 על שלוש ההנחות של PR #3042.

1. **התקרה של שאלה פתוחה היא הגדרה** (`interrupt_ask_minutes`, ברירת מחדל 35, טווח 5–240),
   והטווח זהה בשרת ובקונסולה.
2. **מי עונה:** במחשב הבנייה — כל מי שמחובר לקיוסק (מנהל או משתמש הפצה). בקונסולה — מנהל
   בלבד. בלי משתמש מחובר — אין תשובה.
3. **משכפל שחוזר אחרי `lost_wait_seconds`:** אם הסבב עדיין פתוח הוא מצטרף לגל הבא כמו היום,
   עם שמות המחשב שהסבב קבע (#2786). אם הסבב כבר נגמר הוא אינו מוחזר לשום סבב, והדיסקים שלו
   נשארים "לא הושלמו".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_interrupt_question_2528 import (   # noqa: F401 — room_server הוא fixture
    BOOT_A, BOOT_B, CLONER1, CLONER2, ago, age_member, age_question, asks, beat, events, hello, interrupted,
    ledger_rows, post_answer, progress, question, room, room_server, round_row, start,
)
from test_kiosk_app import _login_kiosk, kiosk_env   # noqa: F401 — kiosk_env הוא fixture

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient   # noqa: E402

from server.agent import timing as agent_timing   # noqa: E402
from server.rounds import room as room_module   # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SETTING = "interrupt_ask_minutes"


@pytest.fixture(autouse=True)
def _ports_are_free(monkeypatch):
    """כמו ב-`room_server`: בדיקת הפורטים של השולח מקבלת ראיה מזויפת חיובית (ב-Windows אין
    `/proc/net/udp`, ו-#1126 מפיל אז את הגל בצדק) — גם ב-`kiosk_env`, שאינו מזייף אותה."""
    from server.rounds import sender as sender_module   # noqa: PLC0415
    monkeypatch.setattr(sender_module, "port_holders", lambda _port: [])


# --- 1. התקרה היא הגדרה ----------------------------------------------------------------


def test_the_ceiling_is_35_minutes_until_someone_sets_it(room_server):
    settings = room_server["admin"].get("/api/console/settings").json()
    assert settings.get(SETTING) == "35", f"ההגדרה חסרה או אינה 35: {settings.get(SETTING)!r}"


@pytest.mark.parametrize("value", ["4", "241", "0", "035", "x", "", True, 35.5, -35])
def test_a_ceiling_outside_5_to_240_minutes_is_refused(room_server, value):
    admin = room_server["admin"]
    refused = admin.post("/api/console/settings", json={SETTING: value})
    assert refused.status_code == 400, f"{value!r} התקבל: {refused.text}"
    assert "5–240" in refused.json()["detail"]
    assert admin.get("/api/console/settings").json().get(SETTING) == "35"


@pytest.mark.parametrize("value", ["5", "240", 60])
def test_a_ceiling_inside_the_range_is_kept(room_server, value):
    admin = room_server["admin"]
    assert admin.post("/api/console/settings", json={SETTING: value}).status_code == 200
    assert admin.get("/api/console/settings").json()[SETTING] == str(value)


def test_the_question_expires_at_the_ceiling_that_was_set(room_server):
    """ברירת המחדל (35) הייתה משאירה את השאלה פתוחה אחרי 11 דקות; עם הגדרה של 10 — הסבב נסגר."""
    ctx, admin = room_server["ctx"], room_server["admin"]
    assert admin.post("/api/console/settings", json={SETTING: "10"}).status_code == 200
    interrupted(room_server)
    beat(room_server)
    asked = question(admin)
    assert asked is not None and 0 < asked["expires_in_s"] <= 600, asked
    age_question(ctx, 11 * 60)
    beat(room_server)
    row = round_row(ctx)
    assert row["state"] == "failed", "השאלה לא פקעה בתקרה שהוגדרה"
    assert "תוך 10 דקות" in row["failed_reason"], row["failed_reason"]


def test_a_longer_ceiling_keeps_the_question_past_35_minutes(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    assert admin.post("/api/console/settings", json={SETTING: "120"}).status_code == 200
    interrupted(room_server)
    beat(room_server)
    assert question(admin) is not None
    age_question(ctx, 40 * 60)
    beat(room_server)
    assert round_row(ctx)["state"] == "active" and question(admin) is not None


def test_the_console_row_has_the_servers_range_and_default():
    """לפי ה-gotchas: הטווח בשרת והטווח שהקונסולה מציגה הם אותם מספרים."""
    from server.rounds import interrupt   # noqa: PLC0415 — מודול חדש: לא באיסוף (בקרה שלילית)

    js = (REPO / "server/static/console.js").read_text(encoding="utf-8")
    row = re.search(r'\{ key: "interrupt_ask_minutes",[^\n]*\n[^\n]*\}', js)
    assert row is not None, "אין שורת הגדרה בקונסולה"
    text = row.group(0)
    assert re.search(r"\bmin: (\d+)", text).group(1) == str(interrupt.MINIMUM)
    assert re.search(r"\bmax: (\d+)", text).group(1) == str(interrupt.MAXIMUM)
    assert f"ברירת המחדל {interrupt.DEFAULT}" in text and 'unit: "דקות"' in text
    assert interrupt.LABEL_HE in text
    assert (interrupt.DEFAULT, interrupt.MINIMUM, interrupt.MAXIMUM) == (35, 5, 240)


# --- 2. מי עונה -------------------------------------------------------------------------

KIOSK_CLONER = "aa:bb:cc:00:00:20"


def _kiosk_interrupted(env) -> TestClient:
    """סבב על המשכפל היחיד של `kiosk_env`, שכבה באמצע וחזר — דרך פורט הסוכן."""
    admin, anon = _login_kiosk(env), env["anon"]
    hello(anon, KIOSK_CLONER, ["S1"], BOOT_A)
    opened = admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 1,
        "target_slots": [{"mac": KIOSK_CLONER, "ports": [1]}]})
    assert opened.status_code == 200, opened.text
    for _ in range(3):
        session = hello(anon, KIOSK_CLONER, ["S1"], BOOT_A)["session"]
        if session is not None and session["state"] == "running":
            break
    else:
        raise AssertionError(f"הגל לא יצא: {session}")
    progress(anon, session["id"], KIOSK_CLONER, {"sda": "writing"})
    assert hello(anon, KIOSK_CLONER, ["S1"], BOOT_B)["session"] is None
    assert room_module.sweep(env["rt"].conn, env["rt"].store)
    assert question(admin) is not None, "אין שאלה על הסבב"
    return admin


def test_a_deploy_user_answers_from_the_build_machines_kiosk(kiosk_env):
    _kiosk_interrupted(kiosk_env)
    deploy = _login_kiosk(kiosk_env, "labtech", "deploy-pass-1")
    answered = post_answer(deploy, "rewrite")
    assert answered.status_code == 200, answered.text
    row = kiosk_env["rt"].conn.execute(
        "SELECT wave_number, state FROM room_rounds ORDER BY rowid DESC LIMIT 1").fetchone()
    assert (row["state"], row["wave_number"]) == ("active", 2)


def test_an_admin_answers_from_the_kiosk_too(kiosk_env):
    admin = _kiosk_interrupted(kiosk_env)
    assert post_answer(admin, "close").status_code == 200


def test_a_deploy_user_is_refused_on_the_console(kiosk_env):
    """#1073: למשתמש הפצה אין קונסולה. העוגייה שהקיוסק הנפיק לו אינה עונה משם."""
    admin = _kiosk_interrupted(kiosk_env)
    deploy_kiosk = _login_kiosk(kiosk_env, "labtech", "deploy-pass-1")
    on_console = TestClient(kiosk_env["console_app"])
    on_console.cookies.update(dict(deploy_kiosk.cookies))
    refused = post_answer(on_console, "rewrite", [KIOSK_CLONER])
    assert refused.status_code == 403, refused.text
    assert question(admin)["answer"] is None, "תשובה שנדחתה נרשמה"


def test_the_kiosk_with_nobody_signed_in_does_not_answer(kiosk_env):
    admin = _kiosk_interrupted(kiosk_env)
    refused = post_answer(kiosk_env["anon"], "rewrite", [KIOSK_CLONER])
    assert refused.status_code in (401, 403), refused.text
    assert question(admin)["answer"] is None


def test_an_admin_answers_on_the_console(kiosk_env):
    _kiosk_interrupted(kiosk_env)
    admin = TestClient(kiosk_env["console_app"])
    assert admin.post("/api/console/login", json={
        "username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert post_answer(admin, "rewrite").status_code == 200


# --- 3. משכפל שחוזר אחרי lost_wait -------------------------------------------------------

NAMES = [{"mac": CLONER1, "port": 1, "name": "100-INS"}, {"mac": CLONER2, "port": 1, "name": "100-01"}]


def test_a_late_cloner_joins_the_next_wave_of_an_open_round_with_its_names(room_server):
    """(א) שני משכפלים. משכפל 1 נעלם ליותר מ-lost_wait; משכפל 2 סיים, ולכן הסבב עדיין פתוח.
    משכפל 1 חוזר: בלי שאלה, מצטרף לגל 2, ושם המחשב שהסבב קבע לדיסק שלו לא אבד."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    hello(anon, CLONER1, ["S1"], BOOT_A, joining=False)
    hello(anon, CLONER2, ["T1"], BOOT_A, joining=False)
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2, "hostnames": {"names": NAMES},
        "target_slots": [{"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [1]}]})
    assert opened.status_code == 200, opened.text
    hello(anon, CLONER1, ["S1"], BOOT_A)
    wave = hello(anon, CLONER2, ["T1"], BOOT_A)["session"]["id"]
    first = hello(anon, CLONER1, ["S1"], BOOT_A)["session"]
    assert first["state"] == "running" and first["hostnames"] == {"1": "100-INS"}, first
    progress(anon, wave, CLONER1, {"sda": "writing"})
    progress(anon, wave, CLONER2, {"sda": "done"}, top="done")
    lost = agent_timing.lost_seconds(ctx.conn) + 30
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
                     (ago(lost), wave, CLONER1))
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (ago(lost), wave))
    ctx.conn.commit()
    beat(room_server)
    beat(room_server)
    view = room(deploy)["round"]
    assert view["state"] == "active" and view["wave_number"] == 2 and view["written_drives"] == 1

    late = hello(anon, CLONER1, ["S1"], BOOT_B)["session"]          # חזר מאוחר, באתחול אחר

    assert question(deploy) is None and "round_interrupted" not in events(ctx)
    assert late is not None and late["id"] != wave, "משכפל שחזר לסבב פתוח לא צורף לגל הבא"
    assert late["target_ports"] == [1]
    assert late["hostnames"] == {"1": "100-INS"}, f"שם המחשב אבד בהצטרפות: {late['hostnames']}"


def test_a_late_cloner_is_not_returned_to_a_round_that_has_ended(room_server):
    """(ב) משכפל יחיד שנעלם ליותר מ-lost_wait: הסבב נגמר. כשהוא חוזר — אין סבב, אין שאלה,
    והדיסקים שלו נשארים לא כתובים, גם אם כבר התחיל לכתוב אותם."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    age_member(ctx, wave, agent_timing.lost_seconds(ctx.conn) + 30)
    beat(room_server)
    beat(room_server)
    ended = round_row(ctx)
    assert ended["state"] != "active", ended["state"]

    for _ in range(3):
        assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None
        beat(room_server)

    row = round_row(ctx)
    assert row["id"] == ended["id"] and row["state"] == ended["state"], "נפתח סבב בשביל המשכפל שחזר"
    assert row["written_drives"] == 0 and row["written_serials"] == "[]"
    assert question(deploy) is None and "round_interrupted" not in events(ctx)


# --- ממצאי הסקירה היריבה על #3042 (12/11) -------------------------------------------------


def test_1a_a_reboot_between_getting_the_wave_and_the_first_report_is_asked(room_server):
    """1-א: החבר עוד `waiting` (לא דיווח), אבל קיבל את הגל באתחול אחר — הוא כבר יכול היה
    להתחיל לכתוב. לפני התיקון הוא קיבל את הגל שוב, והיומן נדרס במזהה החדש."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = start(room_server)                                   # קיבל את הגל באתחול A, בלי דיווח
    answer = hello(anon, CLONER1, ["S1", "S2"], BOOT_B)         # אותחל לפני הדיווח הראשון

    assert answer["session"] is None, f"משכפל שאותחל קיבל שוב את הגל הרץ: {answer['session']}"
    asked = question(deploy)
    assert asked is not None and asked["disks"] == 2, asked
    assert {r["boot_id"] for r in ledger_rows(ctx)} == {BOOT_A}, "היומן נדרס במזהה האתחול החדש"
    state = ctx.conn.execute("SELECT state FROM session_members WHERE session_id = ?",
                             (wave,)).fetchone()["state"]
    assert state == "lost"


def test_1a_the_same_boot_asking_again_before_its_first_report_still_gets_the_wave(room_server):
    """שומר: ה-hello הראשון אחרי שהגל יצא (אותו אתחול) מקבל את הגל, כמו ב-#1728."""
    wave = start(room_server)
    again = hello(room_server["anon"], CLONER1, ["S1", "S2"], BOOT_A)["session"]
    assert again is not None and again["id"] == wave and again["state"] == "running"
    assert question(room_server["deploy"]) is None


def _two_cloners_running(room_server) -> str:
    deploy, anon = room_server["deploy"], room_server["anon"]
    hello(anon, CLONER1, ["S1"], BOOT_A, joining=False)
    hello(anon, CLONER2, ["T1"], BOOT_A, joining=False)
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": [{"mac": CLONER1, "ports": [1]}, {"mac": CLONER2, "ports": [1]}]})
    assert opened.status_code == 200, opened.text
    hello(anon, CLONER1, ["S1"], BOOT_A)
    wave = hello(anon, CLONER2, ["T1"], BOOT_A)["session"]["id"]
    assert hello(anon, CLONER1, ["S1"], BOOT_A)["session"]["state"] == "running"
    progress(anon, wave, CLONER1, {"sda": "writing"})
    progress(anon, wave, CLONER2, {"sda": "writing"})
    return wave


def test_1b_an_answer_covers_only_the_machines_the_screen_showed(room_server):
    """1-ב: המפעיל ראה שאלה על משכפל 1. בינתיים גם משכפל 2 כבה. התשובה שלו חלה על משכפל 1
    בלבד; השאלה על משכפל 2 נשארת פתוחה, והסבב אינו ממשיך לפניה."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    _two_cloners_running(room_server)
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"] is None
    seen = [m["mac"] for m in question(deploy)["machines"]]
    assert seen == [CLONER1]
    assert hello(anon, CLONER2, ["T1"], BOOT_B)["session"] is None       # אחרי שהמסך נקרא

    assert post_answer(deploy, "rewrite", seen).status_code == 200

    by_mac = {a["mac"]: a["answer"] for a in asks(ctx)}
    assert by_mac == {CLONER1: "rewrite", CLONER2: None}, by_mac
    beat(room_server)
    beat(room_server)
    row = round_row(ctx)
    assert row["wave_number"] == 1 and row["wave_session_id"] is None, "הסבב המשיך לפני התשובה השנייה"
    still = question(deploy)
    assert still is not None and still["answer"] is None
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"] is None
    assert post_answer(deploy, "rewrite", [CLONER2]).status_code == 200
    assert round_row(ctx)["wave_number"] == 2


def test_1b_an_answer_about_a_machine_with_no_open_question_is_refused(room_server):
    interrupted(room_server)
    refused = post_answer(room_server["deploy"], "close", [CLONER2])
    assert refused.status_code == 409, refused.text
    assert asks(room_server["ctx"])[0]["answer"] is None


def test_1d_twin_disks_in_one_machine_get_no_ledger_row(room_server):
    """1-ד: שני דיסקים עם אותו serial ואותו גודל אינם ניתנים להבחנה — אין שורה לאף אחד מהם
    (שורה אחת לשניהם הייתה מטעה), והדיסק השלישי נרשם כרגיל."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    hello(anon, CLONER1, ["TWIN", "TWIN", "S3"], BOOT_A, joining=False)
    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 3,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2, 3]}]})
    assert opened.status_code == 200, opened.text
    for _ in range(3):
        session = hello(anon, CLONER1, ["TWIN", "TWIN", "S3"], BOOT_A)["session"]
        if session is not None and session["state"] == "running":
            break
    assert [(r["serial"], r["port"]) for r in ledger_rows(ctx)] == [("S3", 3)]


def test_1e_a_wave_held_on_a_question_still_records_what_each_sata_port_measured(room_server, monkeypatch):
    seen: list[str] = []
    real = room_module.learn_ports.record_wave
    monkeypatch.setattr(room_module.learn_ports, "record_wave",
                        lambda conn, wave_id, members: (seen.append(wave_id), real(conn, wave_id, members))[1])
    wave = interrupted(room_server)
    beat(room_server)
    assert seen == [wave], f"הגל שנעצר על שאלה לא נרשם בלמידת החיבורים: {seen}"


def test_1f_the_answer_does_its_sqlite_work_off_the_event_loop():
    """1-ו: `async def` עם sqlite על לולאת האירועים עוצר את כל השרת (#2748). הגוף נקרא על
    הלולאה, והעבודה ב-`run_in_threadpool`."""
    import ast   # noqa: PLC0415

    tree = ast.parse((REPO / "server/rounds/room.py").read_text(encoding="utf-8"))
    handler = next((n for n in ast.walk(tree)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "answer_interrupt"), None)
    assert handler is not None, "נתיב התשובה אינו בראוטר של room.py (זה שנכנס גם לקיוסק)"
    if isinstance(handler, ast.FunctionDef):
        return                                           # a plain def runs in the threadpool by itself
    calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call)]
    assert any(getattr(c.func, "id", "") == "run_in_threadpool" for c in calls)
    touched = [n for n in ast.walk(handler) if isinstance(n, ast.Attribute) and n.attr in ("conn", "store")]
    assert touched == [], "ה-handler נוגע ב-DB על לולאת האירועים"


def test_1g_a_check_that_could_not_run_does_not_fail_the_hello_and_asks_again(room_server, monkeypatch):
    """1-ז: `database is locked` בבדיקת האתחול אינו 500 על hello, ואינו "לא אותחל": החבר לא
    מסומן `lost` (הגל אינו מסתיים ופותח גל נוסף בלי שאלה), וה-hello הבא שואל שוב."""
    import sqlite3   # noqa: PLC0415

    from server.rounds import interrupt   # noqa: PLC0415 — מודול חדש: לא באיסוף

    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    real = interrupt.on_rejoin

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(interrupt, "on_rejoin", locked)
    answer = hello(anon, CLONER1, ["S1", "S2"], BOOT_B)          # 200, ולא 500
    assert answer["session"] is None
    state = ctx.conn.execute("SELECT state FROM session_members WHERE session_id = ?",
                             (wave,)).fetchone()["state"]
    assert state == "writing", f"בדיקה שלא רצה סימנה את החבר {state}"
    assert "round_interrupt_check_failed" in events(ctx)
    beat(room_server)
    assert round_row(ctx)["wave_number"] == 1, "נפתח גל נוסף בלי שאלה"

    monkeypatch.setattr(interrupt, "on_rejoin", real)
    assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None
    assert question(deploy) is not None, "ה-hello הבא לא שאל שוב"


def test_1g_a_ledger_that_could_not_be_opened_does_not_hand_the_wave(room_server, monkeypatch):
    import sqlite3   # noqa: PLC0415

    from server.rounds import ledger   # noqa: PLC0415

    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    hello(anon, CLONER1, ["S1"], BOOT_A, joining=False)
    assert deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 1,
        "target_slots": [{"mac": CLONER1, "ports": [1]}]}).status_code == 200
    real = ledger.opened

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "opened", locked)
    for _ in range(3):
        refused = hello(anon, CLONER1, ["S1"], BOOT_A)["session"]
        assert refused is None or refused["state"] != "running", f"הגל נמסר בלי יומן: {refused}"
    assert "write_ledger_failed" in events(ctx)
    monkeypatch.setattr(ledger, "opened", real)
    session = hello(anon, CLONER1, ["S1"], BOOT_A)["session"]
    assert session is not None and session["state"] == "running", session
    assert len(ledger_rows(ctx)) == 1


def test_1g_a_boot_id_that_could_not_be_recorded_does_not_fail_the_hello(room_server, monkeypatch):
    import sqlite3   # noqa: PLC0415

    from server.rounds import ledger   # noqa: PLC0415

    ctx, anon = room_server["ctx"], room_server["anon"]
    hello(anon, CLONER1, ["S1"], BOOT_A, joining=False)

    def locked(_conn):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "_settle", locked)
    hello(anon, CLONER1, ["S1"], BOOT_B, joining=False)          # 200 (hello() asserts it)
    assert "boot_id_not_recorded" in events(ctx)


# --- 1-ג: השעון נספר מהמאוחר מבין רגע השאלה לסוף הגל (הכרעת נדב 13/11) ----------------------


def test_1c_a_cloner_that_went_down_early_in_a_long_wave_gets_the_whole_time_after_it(room_server):
    """משכפל 1 כבה בדקה 2 של גל ארוך. משכפל 2 כתב עוד 43 דקות. לפני התיקון הסבב נכשל ברגע
    שהגל הסתיים (השאלה כבר בת 43 דקות, התקרה 35). עכשיו: מלוא 35 הדקות מסוף הגל."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = _two_cloners_running(room_server)
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"] is None           # דקה 2: כבה וחזר
    ctx.conn.execute("UPDATE round_interrupts SET raised_at = ?", (ago(43 * 60),))
    ctx.conn.commit()

    during = question(deploy)

    progress(anon, wave, CLONER2, {"sda": "done"}, top="done")               # דקה 45: הגל נגמר
    beat(room_server)
    beat(room_server)

    row = round_row(ctx)
    assert row["state"] == "active", f"הסבב {row['state']} ברגע שהגל הסתיים: {row['failed_reason']}"
    # בזמן הגל השעון עוד לא התחיל: המסך אומר זאת, ומציג את מלוא הזמן
    assert during.get("wave_running") is True and during["expires_in_s"] == 35 * 60, during
    after = question(deploy)
    assert after is not None and after["answer"] is None and after.get("wave_running") is False
    assert 34 * 60 < after["expires_in_s"] <= 35 * 60, after["expires_in_s"]
    assert post_answer(deploy, "rewrite").status_code == 200                # ועדיין אפשר לענות
    assert round_row(ctx)["wave_number"] == 2


def test_1c_the_question_still_expires_the_whole_time_after_the_wave_ended(room_server):
    """שומר: השעון לא נעצר. 36 דקות אחרי סוף הגל, בלי תשובה — הסבב נסגר."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = _two_cloners_running(room_server)
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"] is None
    progress(anon, wave, CLONER2, {"sda": "done"}, top="done")
    beat(room_server)
    beat(room_server)
    assert round_row(ctx)["state"] == "active"
    age_question(ctx, 36 * 60)
    beat(room_server)
    row = round_row(ctx)
    assert row["state"] == "failed" and "תוך 35 דקות" in row["failed_reason"], row


def test_1c_a_question_older_than_its_wave_end_counts_from_the_wave_end(room_server):
    """השאלה בת 60 דקות, הגל נגמר לפני 10 — נשארו 25, לא אפס."""
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    interrupted(room_server)
    beat(room_server)
    ctx.conn.execute("UPDATE round_interrupts SET raised_at = ?", (ago(60 * 60),))
    ctx.conn.execute("UPDATE sessions SET closed_at = ? WHERE id IN (SELECT wave_id FROM round_interrupts)",
                     (ago(10 * 60),))
    ctx.conn.commit()
    beat(room_server)
    assert round_row(ctx)["state"] == "active"
    left = question(deploy)["expires_in_s"]
    assert 24 * 60 < left <= 25 * 60, left
