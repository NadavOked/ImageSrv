"""‏#989 ס׳ 2 — תשובת מפעיל לשאלת ה-SMART מהקונסולה (`server/disks/smart_ask.py`).

השאלה נוסעת ב-hello של הדופק (`ask`), תשובת הקונסולה חוזרת ב-`ask_answer`
של ה-hello הבא, ותשובה ליד המכונה נשלחת בהרשאה (`ask_answered`) — **הראשונה
גוברת**, והצד השני רואה שכבר נענה (409 "כבר נענה ע"י …").

עיקרון 5: "לא נענתה" אינה "המשך". אדום אין לו "כתוב בכל זאת"; שאלה שהמכונה
הפסיקה לשאול אינה פתוחה; nonce שחוזר אחרי אתחול אינו שאלה שכבר נענתה.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

CLONER = "aa:bb:cc:00:00:31"
OTHER = "aa:bb:cc:00:00:32"
BUILD = "b4:2e:99:07:1a:c5"


@pytest.fixture()
def server(tmp_path: Path, images_root):
    from server.console import users
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name, group in ((CLONER, "01", "grp_CLONERS"), (OTHER, "02", "grp_CLONERS"),
                             (BUILD, "INS", "grp_BUILD")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app)}
    ctx.sender.stop()


def ask(nonce="3-2", port=2, verdict="warn", reason="crc 4"):
    return {"nonce": nonce, "port": port, "verdict": verdict, "reason": reason}


def hello(server, mac=CLONER, **extra) -> dict:
    response = server["anon"].post("/api/v1/agent/hello", json={**hello_body(mac), "joining": False, **extra})
    assert response.status_code == 200
    return response.json()


def room_ask(server, mac=CLONER):
    machines = server["admin"].get("/api/console/room").json()["machines"]
    return next(m for m in machines if m["mac"] == mac)["ask"]


def answer(server, who="admin", mac=CLONER, nonce="3-2", value="skip"):
    return server[who].post("/api/console/room/answer", json={"mac": mac, "nonce": nonce, "answer": value})


def smart_journal(server) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'smart_answered' ORDER BY id")]


# --- השאלה נראית בקונסולה -----------------------------------------------------


def test_the_question_rides_the_hello_and_shows_in_the_room(server):
    assert room_ask(server) is None
    assert "ask_answer" not in hello(server, ask=ask())
    shown = room_ask(server)
    assert (shown["nonce"], shown["port"], shown["verdict"], shown["reason"]) == ("3-2", 2, "warn", "crc 4")
    assert shown["answer"] is None and shown["answered_by"] is None


@pytest.mark.parametrize("bad", [
    {"nonce": "", "port": 2, "verdict": "warn", "reason": ""},
    {"nonce": "a;b", "port": 2, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": 0, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": True, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": 2, "verdict": "ok", "reason": ""},
    "x", 7, [],
])
def test_a_malformed_question_is_dropped_not_stored(server, bad):
    hello(server, ask=bad)
    assert room_ask(server) is None


def test_a_hello_without_the_question_closes_an_open_one(server):
    hello(server, ask=ask())
    assert room_ask(server) is not None
    hello(server)      # המכונה כבר לא שואלת (המסך נפל / אתחול)
    assert room_ask(server) is None
    assert answer(server).status_code == 404


# --- תשובה מהקונסולה ----------------------------------------------------------


def test_a_console_answer_comes_back_in_the_next_hello_and_says_who(server):
    hello(server, ask=ask())
    response = answer(server, value="rescue")
    assert response.status_code == 200 and response.json() == {"ok": True, "answer": "rescue"}
    assert hello(server, ask=ask())["ask_answer"] == {"nonce": "3-2", "answer": "rescue"}
    shown = room_ask(server)
    assert shown["answer"] == "rescue" and shown["answered_by"] == "הקונסולה (noc)"
    assert smart_journal(server) == [f"{CLONER} 2 rescue console:noc"]


def test_the_answer_is_not_delivered_to_a_hello_that_does_not_ask(server):
    hello(server, ask=ask())
    answer(server, value="skip")
    assert "ask_answer" not in hello(server)           # לא שואלת עוד — אין מה לבצע
    assert "ask_answer" not in hello(server, ask=ask(nonce="4-2"))   # שאלה אחרת


def test_a_deploy_user_has_no_console_and_cannot_answer(server):
    # משתמש הפצה עובד ממחשב הבנייה (#1073): הקונסולה סגורה לו — והשאלה נשארת פתוחה.
    hello(server, ask=ask())
    assert answer(server, who="deploy", value="replace").status_code == 403
    assert room_ask(server)["answer"] is None


# --- הכרעה: הראשונה גוברת -----------------------------------------------------


def test_machine_first_then_the_console_gets_409_naming_the_machine(server):
    hello(server, ask=ask())
    reply = hello(server, ask_answered={"nonce": "3-2", "answer": "skip"})
    assert reply["ask_answer"] == {"nonce": "3-2", "answer": "skip"}
    late = answer(server, value="rescue")
    assert late.status_code == 409 and late.json()["detail"] == 'כבר נענה ע"י המכונה (ליד המסך)'
    assert room_ask(server)["answer"] == "skip"
    assert smart_journal(server) == [f"{CLONER} 2 skip machine"]


def test_console_first_then_the_machine_is_told_the_winning_answer(server):
    hello(server, ask=ask())
    assert answer(server, value="replace").status_code == 200
    # המכונה לחצה "דלג" באותה שנייה — השרת מכריע, והיא מבצעת את מה שהוא מחזיר.
    reply = hello(server, ask_answered={"nonce": "3-2", "answer": "skip"})
    assert reply["ask_answer"] == {"nonce": "3-2", "answer": "replace"}
    assert room_ask(server)["answered_by"] == "הקונסולה (noc)"
    assert smart_journal(server) == [f"{CLONER} 2 replace console:noc"]


def test_two_console_answers_second_gets_409(server):
    hello(server, ask=ask())
    assert answer(server, value="skip").status_code == 200
    second = answer(server, value="replace")
    assert second.status_code == 409 and second.json()["detail"] == 'כבר נענה ע"י הקונסולה (noc)'


# --- סירובים ------------------------------------------------------------------


def test_refusals_say_why_and_change_nothing(server):
    assert answer(server).status_code == 404                              # אין שאלה
    hello(server, ask=ask())
    stale = answer(server, nonce="2-2")
    assert stale.status_code == 409 and "התחלפה" in stale.json()["detail"]
    assert answer(server, value="maybe").status_code == 400
    assert answer(server, mac=BUILD).status_code == 404                   # לא משכפל
    assert answer(server, mac="de:ad:be:ef:00:01").status_code == 404     # לא רשום
    assert server["admin"].post("/api/console/room/answer", content=b"not json").status_code == 404
    assert room_ask(server)["answer"] is None, "no refused call may answer the question"
    assert smart_journal(server) == []
    assert server["anon"].post("/api/console/room/answer", json={"mac": CLONER}).status_code == 401


def test_retry_on_a_disk_that_failed_last_time_needs_no_confirmation(server):
    """‏#1580: נכשל בשיכפול הקודם = כתום — "נסה שוב" (`rescue`) מתקבל כמו כל כתום.
    אדום (SMART תקול) אינו נשאל כלל — ‏test_red_disk_never_target_1732.py (#1732)."""
    hello(server, ask=ask(verdict="failed_last", reason="cable"))
    assert answer(server, value="rescue").status_code == 200
    assert hello(server, ask=ask(verdict="failed_last", reason="cable"))["ask_answer"]["answer"] == "rescue"


# --- אחרי האתחול: nonce חוזר ---------------------------------------------------


def test_a_nonce_that_comes_back_after_a_reboot_is_a_new_question(server):
    hello(server, ask=ask())
    answer(server, value="replace")
    hello(server, ask=ask())                  # נמסרה
    hello(server)                             # ממשיכה הלאה / מתכבה והתעוררה: hello בלי שאלה
    # המכונה עלתה מחדש, המונה ב-tmpfs חזר ל-1, ושוב אותו nonce ואותו port:
    fresh = hello(server, ask=ask())
    assert "ask_answer" not in fresh, "the old answer must not answer the new question"
    assert room_ask(server)["answer"] is None
    assert answer(server, value="skip").status_code == 200


def test_questions_are_per_machine(server):
    hello(server, ask=ask())
    assert room_ask(server, OTHER) is None
    assert answer(server, mac=OTHER).status_code == 404


# --- ברזל 17/10: מי בגל ממתין לאדם (‏open_in_wave) ------------------------------


def _wave_member(ctx, mac, state="waiting", last_seen="2026-10-17T08:14:00+00:00",
                 started_at="2026-10-17T08:13:09+00:00"):
    ctx.conn.execute(
        "INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, started_at)"
        " VALUES ('ses_d6e79f65', 'grp_CLONERS', 'img_x', 'C', 1, 0, 'running', 't',"
        " '2026-10-17T08:13:00+00:00', 0, ?)", (started_at,))
    ctx.conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?, ?, ?)",
        ("ses_d6e79f65", mac, state, "2026-10-17T08:13:09+00:00"))
    ctx.conn.execute(
        "INSERT INTO net_devices (mac, last_seen) VALUES (?, ?) ON CONFLICT (mac)"
        " DO UPDATE SET last_seen = excluded.last_seen", (mac, last_seen))
    ctx.conn.commit()


def _open_ask(ctx, mac, answer=None, asked_at="2026-10-17T08:13:20+00:00"):
    ctx.conn.execute(
        "INSERT INTO smart_asks (mac, nonce, port, verdict, reason, asked_at, answer)"
        " VALUES (?, '3-2', 2, 'failed_last', '', ?, ?)", (mac, asked_at, answer))
    ctx.conn.commit()


def test_open_in_wave_names_a_live_member_with_an_unanswered_question(server):
    from datetime import datetime
    from server.disks import smart_ask
    ctx = server["ctx"]
    now = datetime.fromisoformat("2026-10-17T08:14:30+00:00")
    _wave_member(ctx, CLONER)
    _open_ask(ctx, CLONER)
    assert smart_ask.open_in_wave(ctx.conn, "ses_d6e79f65", now) == [CLONER]
    assert smart_ask.open_in_wave(ctx.conn, "ses_other", now) == []    # רק חברי הגל


@pytest.mark.parametrize("member_state, last_seen, answer, asked_at", [
    ("waiting", "2026-10-17T08:14:00+00:00", "skip", None),     # נענתה
    ("failed", "2026-10-17T08:14:00+00:00", None, None),        # החבר כבר סופי
    ("lost", "2026-10-17T08:14:00+00:00", None, None),
    ("waiting", "2026-10-17T08:12:00+00:00", None, None),       # נשתקה — 150ש' בלי hello
    ("waiting", "not-a-time", None, None),                      # אין ראיה שהיא חיה
    ("waiting", "2026-10-17T08:14:00+00:00", None, "2026-10-17T07:39:00+00:00"),  # 35:30 > התקרה
])
def test_open_in_wave_does_not_hold_for_a_question_nobody_can_answer(
        server, member_state, last_seen, answer, asked_at):
    from datetime import datetime
    from server.disks import smart_ask
    ctx = server["ctx"]
    _wave_member(ctx, CLONER, member_state, last_seen)
    _open_ask(ctx, CLONER, answer, asked_at or "2026-10-17T08:13:20+00:00")
    now = datetime.fromisoformat("2026-10-17T08:14:30+00:00")
    assert smart_ask.open_in_wave(ctx.conn, "ses_d6e79f65", now) == []


def test_release_overdue_frees_only_the_machine_past_the_ceiling(server):
    """התקרה לכל מכונה: מעל `ASK_HOLD_MAX_SECONDS` — ‏`failed` עם הסיבה ושורת
    יומן; שאלה צעירה מהתקרה לא נוגעים בה. פעם שנייה — אין שורה נוספת."""
    from datetime import datetime
    from server.disks import smart_ask
    ctx = server["ctx"]
    now = datetime.fromisoformat("2026-10-17T08:14:30+00:00")
    _wave_member(ctx, CLONER)
    _open_ask(ctx, CLONER, asked_at="2026-10-17T07:39:00+00:00")      # 35:30
    _wave_member(ctx, OTHER)
    _open_ask(ctx, OTHER, asked_at="2026-10-17T08:13:20+00:00")       # 1:10
    assert smart_ask.waiting_in_wave(ctx.conn, "ses_d6e79f65", now) == [OTHER]
    rows = {r["mac"]: (r["state"], r["error"]) for r in ctx.conn.execute(
        "SELECT mac, state, error FROM session_members WHERE session_id = 'ses_d6e79f65'")}
    assert rows[CLONER][0] == "failed" and "35 דקות" in rows[CLONER][1], rows
    assert rows[OTHER] == ("waiting", None), rows
    assert [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = 'smart_ask_expired'")] == \
        [f"{CLONER} 2 ses_d6e79f65"]
    smart_ask.release_overdue(ctx.conn, "ses_d6e79f65", now)
    assert ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'smart_ask_expired'").fetchone()["n"] == 1


def test_the_server_ceiling_outlasts_the_agents():
    """הסוכן מוותר ראשון ולוקח את ברירת המחדל של הצבע (והשאלה נסגרת); השרת
    הוא רשת הביטחון. תקרה בשרת שקצרה מזו של הסוכן הייתה משחררת מהגל
    מכונה שעוד עומדת לקבל את הזרם."""
    import re
    from server.disks import smart_ask
    waits = (Path(__file__).resolve().parent.parent / "agent/lib/core/waits.sh").read_text(encoding="utf-8")
    agent = int(re.search(r'WAIT_SMART_GUI_S="\$\{WAIT_SMART_GUI_S:-(\d+)\}"', waits).group(1))
    assert smart_ask.ASK_HOLD_MAX_SECONDS > agent


def test_the_ceiling_counts_from_the_wave_start_not_from_each_question(server):
    """סקירה שנייה 17/10: כל nonce חדש מאפס את `asked_at`, ולכן תקרה לפי
    השאלה הנוכחית הייתה N×35 דקות למכונה עם N מגירות — ובלי גבול למכונה
    ששואלת שאלה חדשה כל חצי שעה. הגל התחיל לפני 35:30, השאלה הנוכחית בת
    דקה: המכונה משתחררת."""
    from datetime import datetime
    from server.disks import smart_ask
    ctx = server["ctx"]
    now = datetime.fromisoformat("2026-10-17T08:14:30+00:00")
    _wave_member(ctx, CLONER, started_at="2026-10-17T07:39:00+00:00")
    _open_ask(ctx, CLONER, asked_at="2026-10-17T08:13:30+00:00")
    assert smart_ask.waiting_in_wave(ctx.conn, "ses_d6e79f65", now) == []
    row = ctx.conn.execute("SELECT state, error FROM session_members WHERE mac = ?",
                           (CLONER,)).fetchone()
    assert row["state"] == "failed" and "35 דקות מתחילת הגל" in row["error"], dict(row)


@pytest.mark.parametrize("started_at, asked_at, held", [
    ("2026-10-17T08:13:09+00:00", "garbage", True),     # תחילת הגל נקראת — היא הראיה
    ("garbage", "2026-10-17T08:13:20+00:00", True),     # והשאלה, כשהגל לא נקרא
    ("garbage", "garbage", False),                       # אין שום ראיה לגיל — אין עצירה
])
def test_an_unreadable_stamp_falls_back_to_the_other_or_does_not_hold(
        server, started_at, asked_at, held):
    from datetime import datetime
    from server.disks import smart_ask
    ctx = server["ctx"]
    _wave_member(ctx, CLONER, started_at=started_at)
    _open_ask(ctx, CLONER, asked_at=asked_at)
    now = datetime.fromisoformat("2026-10-17T08:14:30+00:00")
    assert smart_ask.waiting_in_wave(ctx.conn, "ses_d6e79f65", now) == ([CLONER] if held else [])
    assert ctx.conn.execute("SELECT state FROM session_members WHERE mac = ?",
                            (CLONER,)).fetchone()["state"] == "waiting"


# --- #1580: עצירה באמצע אינה צובעת את הדיסק ------------------------------------


def _failure(ctx, mac, serial, cause, error, session="ses_x"):
    ctx.conn.execute(
        "INSERT INTO disk_failures (session_id, mac, dev, serial, port, ata_port, at, cause, error,"
        " ata_log) VALUES (?, ?, 'sda', ?, 1, 1, '2026-10-17T08:16:10+00:00', ?, ?, '[]')",
        (session, mac, serial, cause, error))
    ctx.conn.commit()


@pytest.mark.parametrize("error", [
    "מחיצה 1: פג הזמן -- הזרם לא הגיע",                 # שליפת המגירה 17/10: sda התקין
    "מחיצה 2: הזרם נקטע לפני סופו (מקור rc=1)",
    "מחיצה 1: השרת סגר את הסבב -- הכתיבה הופסקה",
    "המחשב כובה מהקונסולה באמצע שיכפול מקומי — הדיסק שבור ואינו שמיש",
    "השיכפול נעצר לפני מחיצה 3 — היעד אינו שמיש",
])
def test_an_interruption_does_not_colour_the_disk(server, error):
    """‏#1580 (נדב 17/10): כשל שהוא עצירה באמצע (הסבב נעצר/נסגר, המכונה כובתה,
    הזרם לא הגיע) אינו שגיאת דיסק — hello אינו מחזיר אותו (הסוכן לא צובע),
    והקונסולה אינה מציגה אותו. ‏**בקרה שלילית:** על הקוד הישן הוא חוזר בשניהם."""
    from server.disks import disk_failures
    ctx = server["ctx"]
    _failure(ctx, CLONER, "S-OK", "unclassified", error)
    assert disk_failures.open_for(ctx.conn, CLONER, [{"serial": "S-OK"}]) == []
    assert disk_failures.list_open(ctx.conn) == []


@pytest.mark.parametrize("cause, error", [
    ("cable", "מחיצה 1: פג הזמן -- הזרם לא הגיע"),   # הקרנל ראה שגיאת קו — היא ההסבר
    ("disk", "מחיצה 1: השרת סגר את הסבב -- הכתיבה הופסקה"),
    ("unclassified", "מחיצה 3: הכתיבה נכשלה (partclone rc=1)"),   # לא עצירה
    ("unclassified", None),                                       # אין error — אין ראיה
])
def test_a_real_failure_still_colours_the_disk(server, cause, error):
    """הצד השני: שגיאת קו/דיסק מה-ATA, או כשל כתיבה שאינו עצירה — נשארים."""
    from server.disks import disk_failures
    ctx = server["ctx"]
    _failure(ctx, CLONER, "S-BAD", cause, error)
    assert [f["serial"] for f in disk_failures.open_for(ctx.conn, CLONER, [{"serial": "S-BAD"}])] == ["S-BAD"]
    assert [f["serial"] for f in disk_failures.list_open(ctx.conn)] == ["S-BAD"]


def test_the_interruption_phrases_are_what_the_agent_writes():
    """הביטויים בשרת חייבים להיות המילים שהסוכן כותב — אחרת הסיווג "עובד"
    בטסט ולא על ברזל."""
    from server.disks import disk_failures
    agent = Path(__file__).resolve().parent.parent / "agent"
    source = "\n".join(p.read_text(encoding="utf-8") for p in agent.rglob("*.sh")) + \
        (agent / "imagesrv-agent").read_text(encoding="utf-8")
    for phrase in disk_failures.INTERRUPTION_PHRASES:
        assert phrase in source, phrase
