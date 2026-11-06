"""#2550 — מה מחשב יקבל באתחול הבא: תצוגה בקונסולה, **קריאה בלבד**.

שלוש טענות, וכל אחת נבדקת מול האתחול האמיתי ולא מול עצמה:

1. **התצוגה לא משקרת.** מה שהקונסולה אומרת הוא מה שהקובץ שמוגש ל-GRUB אומר
   (`# decision: <code>`), בכל מצב של מונה האתחולים.
2. **התצוגה אינה אתחול.** קריאה אינה מקדמת מונה, אינה מגינה לולאה, אינה נרשמת
   כ-hello או כ-`net_seen`, ואינה מתחילה סבב או מקדמת את חדר השיכפולים. לולאה של
   תצוגות בין שני אתחולים אמיתיים לא מזיזה את הנקודה שבה ההגנה יורה.
3. **ספק אינו ניחוש** (עיקרון 5): מה שהשרת לא הצליח לבדוק מוחזר `decided: false`.
"""

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom                 # noqa: E402

from boot.grub_menu import AGENT, Decision, LOCAL                # noqa: E402
from server.agent import boot_preview, bootguard                 # noqa: E402
from server.agent.bootguard import ATTEMPT_LIMIT                 # noqa: E402

URL = "/api/console/machines/boot-preview"
BUILD = "b4:2e:99:07:1a:aa"
CLONER = "aa:bb:cc:00:00:21"

#: טבלאות ההזדהות של הקונסולה נוגעות בעוגיית המנהל עצמה (`last_seen`), לא במכונות.
_AUTH_TABLES = {"users", "mfa_backup_codes", "login_attempts", "login_account_failures",
                "trusted_browsers", "mfa_challenges", "console_sessions", "revoked_tokens"}


# --- עזרים --------------------------------------------------------------------


def snapshot(conn) -> dict:
    """כל הטבלאות של השרת חוץ מהזדהות המנהל — כל שורה, כל עמודה."""
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in names if name not in _AUTH_TABLES}


def menu(server, mac: str) -> str:
    response = server["anon"].get(f"/boot/menu?mac={mac}")
    assert response.status_code == 200
    return response.text


def decision_code(text: str) -> str:
    return re.search(r"^# decision: (\S+)$", text, re.M).group(1)


def preview(server, who: str = "admin") -> dict:
    response = server[who].get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def open_session(server, expected: int = 2) -> dict:
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": expected})
    assert response.status_code == 200, response.text
    ids["session"] = response.json()["id"]
    return ids


def register(server, mac: str, group: str, name: str) -> None:
    assert server["admin"].post("/api/console/machines", json={
        "mac": mac, "name": name, "group_id": group}).status_code == 200


def capture_task(server, mac: str = BUILD) -> None:
    assert server["admin"].post("/api/console/tasks/capture", json={
        "mac": mac, "disk": "sda", "name": "Windows 11 Base", "folder": "Office"}
    ).status_code == 200


# --- 1. התצוגה אומרת מה שהאתחול האמיתי אומר ----------------------------------


def test_each_kind_is_what_the_real_boot_file_says(server):
    """חמש התשובות של מחשב רשום, כל אחת מול `# decision:` בקובץ שמוגש ל-GRUB."""
    ids = open_session(server)
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    register(server, CLONER, "grp_CLONERS", "shich-1")
    register(server, "b4:2e:99:07:1a:bb", "grp_BUILD", "BUILD-2")
    capture_task(server)                        # BUILD: משימה; BUILD-2: אין

    shown = preview(server)["machines"]
    served = {mac: menu(server, mac) for mac in shown}

    assert {mac: s["kind"] for mac, s in shown.items()} == {
        ids["mac1"]: "session", ids["mac2"]: "session",
        BUILD: "task", "b4:2e:99:07:1a:bb": "auto", CLONER: "wait"}
    for mac, item in shown.items():
        assert item["decided"] is True
        assert item["code"] == decision_code(served[mac]), mac
        assert item["guard"] is None
    # ומה שהמפעיל צריך כדי להסביר: איזו משימה, איזה סבב.
    assert shown[BUILD]["task"]["name"] == "Windows 11 Base"
    assert shown[BUILD]["task"]["type"] == "capture"
    assert shown[ids["mac1"]]["session"] == {
        "id": ids["session"], "state": "open", "image_id": "img_7f3a91"}


@pytest.mark.parametrize("boots_so_far", range(ATTEMPT_LIMIT + 3))
def test_the_preview_agrees_with_the_real_boot_at_every_counter(server, boots_so_far):
    """במצב נתון של המונה, מה שהתצוגה אומרת על האתחול הבא הוא מה שהאתחול הבא
    עושה: ההגנה יורה בדיוק באתחול ה-(ATTEMPT_LIMIT+1), לא לפני ולא אחרי."""
    mac = open_session(server)["mac1"]
    for _ in range(boots_so_far):
        menu(server, mac)
    shown = preview(server)["machines"][mac]
    served = menu(server, mac)

    guarded = decision_code(served) == "boot-loop-guard"
    assert guarded == (boots_so_far >= ATTEMPT_LIMIT)
    assert (shown["guard"] is not None) == guarded
    assert shown["code"] == decision_code(served)
    assert shown["kind"] == ("menu" if guarded else "session")
    if guarded:
        assert shown["guard"] == {"sent": ATTEMPT_LIMIT, "limit": ATTEMPT_LIMIT}
        # ומה שההגנה לקחה: ההצעה עצמה עדיין מוצגת, כדי להסביר "למה".
        assert shown["session"]["state"] == "open"


def test_a_guarded_cloner_still_waits_it_has_no_disk_to_fall_back_to(server, monkeypatch):
    """משכפל שההגנה ירתה בו אינו מקבל תפריט: אין לו דיסק מקומי (#17), ו-`decide`
    נותן לו `cloner-wait`. התצוגה אומרת את זה ("מחכה", עם ההגנה מסומנת) — לא
    "תפריט בחירה"."""
    register(server, CLONER, "grp_CLONERS", "shich-1")
    answer = {"schema": 1, "known": True, "role": "cloner", "task": None,
              "session": {"id": "ses_x", "state": "open", "image_id": "img_7f3a91"}}
    monkeypatch.setattr(boot_preview, "build_answer", lambda *args, **kwargs: dict(answer))
    conn = server["ctx"].conn
    conn.execute("INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                 " VALUES (?, 'session:ses_x', ?, 't', 't')", (CLONER, ATTEMPT_LIMIT))
    conn.commit()
    shown = preview(server)["machines"][CLONER]
    assert shown["kind"] == "wait" and shown["code"] == "cloner-wait"
    assert shown["guard"] == {"sent": ATTEMPT_LIMIT, "limit": ATTEMPT_LIMIT}


def test_peek_agrees_with_guard_in_every_counter_state(server):
    """הקריאה-בלבד והאמיתית, זו מול זו: אותה תשובה (כולל דגל ההגנה) בכל מצב של
    המונה ובכל הקשר. כאן נחשף ה-UPSERT ב-`_record` אם הכלל שלו ו-`peek` נפרדים."""
    conn = server["ctx"].conn
    answer = {"schema": 1, "known": True, "role": "classroom", "task": None,
              "session": {"id": "ses_a", "state": "open", "image_id": "i"}}
    for stored_context in ("session:ses_a", "session:ses_other", None):
        for stored in range(0, ATTEMPT_LIMIT + 3):
            conn.execute("DELETE FROM boot_attempts")
            if stored_context:
                conn.execute("INSERT INTO boot_attempts (mac, context, attempts, first_at,"
                             " last_at) VALUES ('m1', ?, ?, 't', 't')", (stored_context, stored))
            conn.commit()
            peeked = bootguard.peek(conn, "m1", answer)
            served = bootguard.guard(conn, "m1", answer)
            assert peeked is not None
            assert peeked[0] == served, (stored_context, stored)


# --- 2. התצוגה אינה אתחול ----------------------------------------------------


def test_previews_never_spend_the_boot_budget(server):
    """הבקרה השלילית של "הצגה לא משנה מונה": שלוש תצוגות לפני כל אתחול אמיתי, וההגנה
    עדיין יורה באתחול ה-(ATTEMPT_LIMIT+1) בדיוק. תצוגה שנספרת הייתה מקדימה אותה."""
    mac = open_session(server)["mac1"]
    for boot in range(1, ATTEMPT_LIMIT + 3):
        for _ in range(3):
            preview(server)
        guarded = decision_code(menu(server, mac)) == "boot-loop-guard"
        assert guarded == (boot > ATTEMPT_LIMIT), boot


def test_a_preview_changes_nothing_in_the_database(server):
    """לא מונה, לא יומן, לא net_seen, לא הצטרפות, ולא קידום סבב: כל טבלה, כל שורה.

    הסבב כאן **בשל להתחלה** (המצטרף היחיד מילא את המספר), ובחדר יש משכפל עם סבב
    חדר פתוח — בדיוק המקומות שבהם האתחול האמיתי כותב כשהוא עונה."""
    ids = open_session(server, expected=1)
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    register(server, CLONER, "grp_CLONERS", "shich-1")
    capture_task(server)
    assert server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"])).status_code == 200
    ctx = server["ctx"]
    row = ctx.conn.execute("SELECT * FROM sessions WHERE id = ?", (ids["session"],)).fetchone()
    assert row["state"] == "open" and ctx.store.would_start(row), "הסבב צריך להיות בשל"
    ctx.conn.execute("INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                     " VALUES (?, 'session:ses_gone', ?, 't', 't')", (CLONER, ATTEMPT_LIMIT))
    ctx.conn.commit()

    before = snapshot(ctx.conn)
    for _ in range(3):
        shown = preview(server)["machines"]
    assert snapshot(ctx.conn) == before
    # והבקרה על הבקרה: האתחול האמיתי כן משנה את אותו מצב, כלומר ההשוואה יכולה להיכשל.
    menu(server, ids["mac2"])
    assert snapshot(ctx.conn) != before
    # ה"בשל" לא הוכרע בשקט: מי שאינו חבר בסבב שהיה יוצא (mac2) — לא מקבל אותו.
    assert shown[ids["mac2"]]["kind"] == "auto"
    assert shown[ids["mac1"]]["kind"] == "session"


def test_a_preview_never_reaches_the_code_that_writes(server, monkeypatch):
    """חוט נוסף על ההשוואה למעלה: כל פונקציה שכותבת באתחול האמיתי — נופלת אם נקראת."""
    from server.agent import hello
    from server.rounds import room, sessions

    open_session(server, expected=1)
    register(server, CLONER, "grp_CLONERS", "shich-1")
    register(server, BUILD, "grp_BUILD", "BUILD-PC")

    def forbidden(name):
        def boom(*args, **kwargs):
            raise AssertionError(f"the preview called {name}")
        return boom

    for owner, name in ((bootguard, "guard"), (bootguard, "_record"), (room, "pulse"),
                        (room, "tick"), (room, "wake_check"), (hello, "net_seen"),
                        (hello, "cancel_gui_tasks_on_boot"), (hello, "journal"),
                        (sessions.SessionStore, "maybe_start"),
                        (sessions.SessionStore, "record_hello")):
        monkeypatch.setattr(owner, name, forbidden(name))
    shown = preview(server)["machines"]
    assert all(item["decided"] for item in shown.values()), shown


def test_the_response_carries_no_task_token_and_no_disk_serials(server):
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    capture_task(server)
    token = server["ctx"].conn.execute("SELECT token FROM tasks").fetchone()["token"]
    assert token
    assert token not in json.dumps(preview(server))
    assert set(preview(server)["machines"][BUILD]["task"]) == {"type", "name"}


# --- 3. ספק אינו ניחוש --------------------------------------------------------


def test_a_machine_whose_check_fails_is_unknown_and_the_room_survives(server, monkeypatch):
    open_session(server)
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    real = boot_preview.build_answer

    def flaky(conn, library, store, mac, **kwargs):
        if mac == BUILD:
            raise RuntimeError("boom")
        return real(conn, library, store, mac, **kwargs)

    monkeypatch.setattr(boot_preview, "build_answer", flaky)
    shown = preview(server)["machines"]
    assert shown[BUILD] == {"decided": False, "why": "answer-failed"}
    assert shown["b4:2e:99:07:1a:c4"]["decided"] is True


def test_an_unreadable_counter_is_unknown_not_not_guarded(server):
    """"לא הצלחנו לקרוא את המונה" אינו "ההגנה לא תירה" (עיקרון 5)."""
    ids = open_session(server)
    register(server, CLONER, "grp_CLONERS", "shich-1")
    conn = server["ctx"].conn
    conn.execute("ALTER TABLE boot_attempts RENAME TO boot_attempts_away")
    conn.commit()
    try:
        shown = preview(server)["machines"]
    finally:
        conn.execute("ALTER TABLE boot_attempts_away RENAME TO boot_attempts")
        conn.commit()
    assert shown[ids["mac1"]] == {"decided": False, "why": "guard-unreadable"}
    # מי שאין לו מונה לקרוא (משכפל בלי סבב) אינו תלוי בו.
    assert shown[CLONER]["decided"] is True


def test_a_decision_code_the_preview_does_not_know_is_unknown(server, monkeypatch):
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    monkeypatch.setattr(boot_preview, "decide", lambda answer: Decision(AGENT, "brand-new-code"))
    assert preview(server)["machines"][BUILD] == {"decided": False, "why": "unknown-code"}


@pytest.mark.parametrize("action, code, menu_shown, kind", [
    (AGENT, "task-assigned", False, "task"), (AGENT, "session-joinable", False, "session"),
    (AGENT, "cloner-wait", False, "wait"), (AGENT, "no-task", False, "auto"),
    (LOCAL, "no-task", True, "menu"), (LOCAL, "boot-loop-guard", True, "menu"),
    (LOCAL, "unregistered", False, "local"), (AGENT, "unregistered-screen", False, None),
    ("elsewhere", "no-task", False, None)])
def test_the_kind_table(action, code, menu_shown, kind):
    assert boot_preview._kind(action, code, menu_shown) == kind


# --- מי רואה ------------------------------------------------------------------


def test_who_may_read_it(server):
    """ההרשאה של הנתיב היא **כל משתמש מחובר** (כמו `GET /machines`), לא `admin_only`:
    אין כאן פעולה ואין סוד. משתמש הפצה עדיין מסורב בקונסולה כולה (#1073) — השער
    הכללי הזה לא השתנה, ולכן הוא נבדק בנפרד: ראשית הנתיב עצמו, בלי השער."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.console.api import create_console_router

    open_session(server)
    bare = FastAPI()
    bare.include_router(create_console_router(server["ctx"]))     # בלי `console_only`
    as_deploy = TestClient(bare, cookies=server["deploy"].cookies)
    assert as_deploy.get(URL).status_code == 200
    assert as_deploy.get(URL).json()["machines"] == preview(server)["machines"]
    assert TestClient(bare).get(URL).status_code == 401

    assert server["admin"].get(URL).status_code == 200
    assert server["anon"].get(URL).status_code == 401
    refused = server["deploy"].get(URL)           # השער של #1073, כמו כל נתיב קונסולה
    assert refused.status_code == 403
    assert server["deploy"].get("/api/console/machines").status_code == 403


def test_it_is_one_call_for_the_whole_room(server):
    ids = open_session(server)
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    body = preview(server)
    assert set(body["machines"]) == {ids["mac1"], ids["mac2"], BUILD}
    assert body["guard_limit"] == ATTEMPT_LIMIT and body["checked_at"]


def test_the_preview_is_not_a_hello(server):
    """לא נרשם ב-`net_devices`: מכונה שלא התחברה מעולם לא "נראתה" רק כי מישהו הסתכל."""
    ids = open_session(server)
    preview(server)
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) FROM net_devices WHERE mac = ?", (ids["mac1"],)).fetchone()[0] == 0
