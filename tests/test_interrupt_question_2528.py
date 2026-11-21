"""#2528 (שלב 1) — מכונה שכבתה או אותחלה באמצע סבב: השרת שואל, ואינו כותב בלי תשובה.

הכרעות נדב (12/11), כל אחת עם הטסט שלה:

1. **שאלה אחרי כיבוי.** משכפל שחזר עם מזהה אתחול אחר באמצע הזרם — הסבב עומד על שאלה.
2. **בלי שאלה אחרי שליפה וחזרה**, ובלי שאלה על אותו אתחול או על סוכן בלי מזהה אתחול.
3. **פקיעה אחרי `lost_wait_seconds`**, והיומן של המכונה נמחק.
4. **"כתוב מחדש" כותב רק מה שלא הושלם.**
5. **בלי תשובה לא כותבים** — ושאלה אינה מחכה בלי הגבלה.
6. **זהות לפי serial, לא לפי מגירה.**

השעון: `room`/`interrupt` קוראים את השעון האמיתי, ולכן "עבר זמן" נעשה כאן בהזזת החותמות
שב-DB אחורה — אותה דרך שבה הטסטים של #450 מזיזים חבר ל"אבוד".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import hello_body
from test_server_room import (           # noqa: F401 — room_server הוא fixture
    CLONER1, CLONER2, room, room_body, room_server,
)

pytest.importorskip("fastapi")

from server.agent import timing as agent_timing   # noqa: E402
from server.rounds import prompt_hold, room as room_module   # noqa: E402

# אין כאן ייבוא של `server.rounds.interrupt` / `ledger`: בבקרה השלילית (כל קובצי הייצור
# חוזרים לבסיס, והמודולים החדשים אינם) הטסטים חייבים להגיע לשרת ולהיכשל על ההתנהגות שלו,
# לא על איסוף. התקרה של שאלה פתוחה היא זו של שאר השאלות למפעיל, והיא קיימת גם בבסיס.
ASK_MAX_SECONDS = prompt_hold.ASK_HOLD_MAX_SECONDS

BOOT_A = "11111111-1111-4111-8111-111111111111"
BOOT_B = "22222222-2222-4222-8222-222222222222"
SIZE = 256060514304


def hello(client, mac: str, serials: list[str], boot: str | None, *,
          joining: bool = True) -> dict:
    """hello של משכפל: מגירה לכל serial (serial i בחריץ i+1), ומזהה האתחול שלו."""
    body = hello_body(mac)
    body["joining"] = joining
    if boot is not None:
        body["boot_id"] = boot
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": SIZE, "model": "Drawer SSD",
         "serial": serial, "removable": False, "port": i + 1}
        for i, serial in enumerate(serials)
    ]
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def progress(client, wave: str, mac: str, per_dev: dict[str, str], top: str = "writing") -> None:
    response = client.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": top,
        "targets": [{"dev": dev, "bytes_written": 100 if state == "done" else 40,
                     "bytes_total": 100, "state": state,
                     **({"error": "I/O error"} if state == "failed" else {})}
                    for dev, state in per_dev.items()],
    })
    assert response.status_code == 200, response.text


def start(room_server, boot: str | None = BOOT_A, serials=("S1", "S2"), after_open=None) -> str:
    """סבב על המגירות של משכפל 1, והמשכפל קיבל את הגל הרץ באתחול `boot`."""
    serials = list(serials)
    opened = room_server["deploy"].post(
        "/api/console/room", json=room_body(room_server, len(serials), {CLONER1: serials}))
    assert opened.status_code == 200, opened.text
    if after_open is not None:
        after_open()
    for _ in range(3):
        session = hello(room_server["anon"], CLONER1, serials, boot)["session"]
        if session is not None and session["state"] == "running":
            return session["id"]
    raise AssertionError(f"הגל לא יצא: {session}")


def beat(room_server) -> None:
    """הדופק מהרקע — מה שמקדם את הסבב בלי hello של אף מכונה."""
    assert room_module.sweep(room_server["ctx"].conn, room_server["ctx"].store)


def ago(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def age_member(ctx, wave: str, seconds: int) -> None:
    """הדיווח האחרון של החבר — ותחילת הגל — היו לפני `seconds` שניות."""
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ?",
                     (ago(seconds), wave))
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (ago(seconds), wave))
    ctx.conn.commit()


def _rows(ctx, table: str, order: str) -> list[dict]:
    assert ctx.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                            (table,)).fetchone() is not None, f"אין בשרת טבלה {table}"
    return [dict(r) for r in ctx.conn.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def age_question(ctx, seconds: int) -> None:
    """השאלה נרשמה, והגל שלה נגמר, לפני `seconds` שניות — השעון נספר מהמאוחר מהשניים."""
    ctx.conn.execute("UPDATE round_interrupts SET raised_at = ?", (ago(seconds),))
    ctx.conn.execute("UPDATE sessions SET closed_at = ? WHERE id IN"
                     " (SELECT wave_id FROM round_interrupts) AND closed_at IS NOT NULL", (ago(seconds),))
    ctx.conn.commit()


def ledger_rows(ctx) -> list[dict]:
    return _rows(ctx, "write_ledger", "serial")


def asks(ctx) -> list[dict]:
    return _rows(ctx, "round_interrupts", "mac")


def question(client) -> dict | None:
    """`/room.round.interrupt`, או None כשאין שאלה (או כשהשרת אינו מכיר את השדה)."""
    view = room(client)["round"]
    return view.get("interrupt") if view is not None else None


def events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal ORDER BY id")]


def round_row(ctx) -> dict:
    return dict(ctx.conn.execute(
        "SELECT * FROM room_rounds ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone())


def post_answer(client, choice: str, macs: list[str] | None = None):
    """התשובה כמו שהמסך שולח אותה: עם המכונות שהשאלה שהוצגה לו נוגעת בהן."""
    if macs is None:
        shown = question(client)
        macs = [m["mac"] for m in shown["machines"]] if shown else [CLONER1]
    return client.post("/api/console/room/interrupt", json={"answer": choice, "machines": macs})


def interrupted(room_server, done: tuple[str, ...] = ()) -> str:
    """המשכפל באמצע הכתיבה (מה שב-`done` כבר הושלם), כבה, וחזר באתחול אחר."""
    anon = room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1,
             {dev: ("done" if dev in done else "writing") for dev in ("sda", "sdb")})
    answer = hello(anon, CLONER1, ["S1", "S2"], BOOT_B)       # poweroff → הדלקה → hello
    assert answer["session"] is None, answer["session"]
    return wave


# --- 1. שאלה אחרי כיבוי --------------------------------------------------------------


def test_the_ledger_records_the_disks_and_the_boot_that_got_the_wave(room_server):
    ctx = room_server["ctx"]
    wave = start(room_server)
    rows = ledger_rows(ctx)
    assert [(r["serial"], r["port"], r["size_bytes"], r["boot_id"], r["wave_id"]) for r in rows] == [
        ("S1", 1, SIZE, BOOT_A, wave), ("S2", 2, SIZE, BOOT_A, wave)], rows


def test_a_cloner_that_came_back_on_another_boot_raises_the_question(room_server):
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    interrupted(room_server)

    ask = question(deploy)
    assert ask is not None, "משכפל שאותחל באמצע הזרם, ואין שאלה למפעיל"
    assert ask["answer"] is None
    assert ask["disks"] == 2 and [m["name"] for m in ask["machines"]] == ["shich-1"]
    assert ask["text_he"] == "המשכפל shich-1 כבה או אותחל באמצע הסבב. 2 דיסקים לא הושלמו."
    assert ask["buttons_he"] == {"rewrite": "כתוב מחדש את הדיסקים שלא הושלמו",
                                 "close": "סגור את הסבב"}
    assert "round_interrupted" in events(ctx)


def test_the_wave_ends_and_no_wave_is_opened_while_the_question_is_open(room_server):
    """ההתנהגות של היום (#1728) הייתה גל 2 מעצמו. עכשיו: הסבב פעיל, בלי גל."""
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    wave = interrupted(room_server)
    beat(room_server)                                   # הגל נגמר: החבר היחיד `lost`
    beat(room_server)

    row = round_row(ctx)
    assert row["state"] == "active" and row["wave_session_id"] is None, row
    assert row["wave_number"] == 1, f"נפתח גל {row['wave_number']} בלי תשובה"
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?",
                            (wave,)).fetchone()["state"] == "closed"
    assert ctx.store.active_for_group(room_module.CLONERS_GROUP) is None, "חריץ השידור תפוס"
    assert room(deploy)["round"]["wave_state"] == "closed"
    assert question(deploy) is not None and question(deploy)["answer"] is None
    assert "room_wave" not in events(ctx) and "room_failed" not in events(ctx)


# --- 2. בלי שאלה כשזה לא אתחול ---------------------------------------------------------


def test_a_disk_pulled_and_returned_is_rewritten_without_a_question(room_server):
    """שליפה וחזרה: המגירה נכשלה, נשארת טרייה, והגל הבא כותב אותה — כמו ברשת, בלי שאלה."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "done", "sdb": "failed"}, top="partial")
    beat(room_server)

    view = room(deploy)["round"]
    assert question(deploy) is None and "round_interrupted" not in events(ctx)
    assert view["wave_number"] == 2 and view["wave_state"] == "open", view
    again = hello(anon, CLONER1, ["S1", "S2"], BOOT_A)["session"]     # אותו אתחול, הדיסק חזר
    assert again is not None and again["id"] != wave and again["target_ports"] == [2], again


def test_the_same_boot_coming_back_mid_stream_is_not_asked(room_server):
    """סוכן שעלה מחדש בלי אתחול (אותו מזהה): אין ראיה לכיבוי — גל 2 מעצמו, כמו ב-#1728."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert hello(anon, CLONER1, ["S1", "S2"], BOOT_A)["session"] is None
    beat(room_server)

    assert question(room_server["deploy"]) is None and "round_interrupted" not in events(ctx)
    assert round_row(ctx)["wave_number"] == 2 and "room_rejoin" in events(ctx)


def test_an_agent_that_sends_no_boot_id_is_not_asked(room_server):
    """סוכן ישן: "לא ידוע" אינו "אותחל". אין יומן, אין שאלה, והמסלול הישן נשאר."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server, boot=None)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert hello(anon, CLONER1, ["S1", "S2"], None)["session"] is None
    beat(room_server)
    assert question(room_server["deploy"]) is None and "round_interrupted" not in events(ctx)
    assert round_row(ctx)["wave_number"] == 2


def test_an_agent_that_sends_no_boot_id_gets_no_ledger(room_server):
    start(room_server, boot=None)
    assert ledger_rows(room_server["ctx"]) == []


def test_a_malformed_boot_id_is_unknown_and_not_another_boot(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    hello(anon, CLONER1, ["S1", "S2"], "not-a-boot-id")
    assert question(room_server["deploy"]) is None
    kept = dict(ctx.conn.execute("SELECT * FROM net_devices WHERE mac = ?", (CLONER1,)).fetchone())
    assert kept.get("boot_id") == BOOT_A, "ערך פגום דרס את מזהה האתחול שנרשם"


def test_a_round_from_the_build_machine_is_not_asked(room_server):
    """הפצה ישירה אינה בשלב הזה: המשכפל שאותחל באמצע אינו מקבל שאלה, כמו קודם. הסבב מסומן
    כאן כסבב מדיסק חי מיד אחרי הפתיחה (עם מניפסט, כדי שהגל ייצא) — בלי מחשב בנייה אמיתי."""
    ctx, anon = room_server["ctx"], room_server["anon"]

    def as_direct() -> None:
        ctx.conn.execute("UPDATE room_rounds SET source_kind = 'build_disk',"
                         " live_manifest_json = ?", (json.dumps({"partitions": []}),))
        ctx.conn.commit()

    wave = start(room_server, after_open=as_direct)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None
    assert question(room_server["deploy"]) is None and "round_interrupted" not in events(ctx)


def test_a_round_from_the_build_machine_keeps_no_ledger(room_server):
    ctx = room_server["ctx"]

    def as_direct() -> None:
        ctx.conn.execute("UPDATE room_rounds SET source_kind = 'build_disk',"
                         " live_manifest_json = ?", (json.dumps({"partitions": []}),))
        ctx.conn.commit()

    start(room_server, after_open=as_direct)
    assert ledger_rows(ctx) == []


# --- 3. פקיעה אחרי lost_wait, ומחיקת המידע ----------------------------------------------


def test_a_cloner_that_came_back_after_lost_wait_is_not_asked(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    age_member(ctx, wave, agent_timing.lost_seconds(ctx.conn) + 30)

    hello(anon, CLONER1, ["S1", "S2"], BOOT_B)          # חזרה מאוחר מדי

    assert question(room_server["deploy"]) is None, "מכונה שחזרה אחרי lost_wait קיבלה שאלה"
    assert "round_interrupted" not in events(ctx)


def test_the_ledger_of_a_cloner_that_came_back_after_lost_wait_is_deleted(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    assert len(ledger_rows(ctx)) == 2
    age_member(ctx, wave, agent_timing.lost_seconds(ctx.conn) + 30)

    hello(anon, CLONER1, ["S1", "S2"], BOOT_B)

    assert ledger_rows(ctx) == [], "המידע לא נמחק אחרי lost_wait"
    assert asks(ctx) == [] and "write_ledger_expired" in events(ctx)


def test_a_cloner_that_never_came_back_loses_its_ledger_at_lost_wait(room_server):
    """בלי שום hello: הדופק מהרקע מכריז `lost`, והיומן נמחק באותו רגע."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    age_member(ctx, wave, agent_timing.lost_seconds(ctx.conn) + 30)
    beat(room_server)
    assert ledger_rows(ctx) == [] and asks(ctx) == []
    assert events(ctx).count("write_ledger_expired") == 1


def test_coming_back_just_inside_lost_wait_is_still_asked(room_server):
    """שומר: הפקיעה אינה דוחה מכונה שחזרה בזמן."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    age_member(ctx, wave, agent_timing.lost_seconds(ctx.conn) - 30)
    hello(anon, CLONER1, ["S1", "S2"], BOOT_B)
    assert question(room_server["deploy"]) is not None, "מכונה שחזרה בתוך lost_wait לא קיבלה שאלה"
    assert len(asks(ctx)) == 1 and len(ledger_rows(ctx)) == 2


# --- 4. "כתוב מחדש" כותב רק מה שלא הושלם ------------------------------------------------


def test_rewrite_opens_the_next_wave_with_only_the_disk_that_was_not_completed(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    wave = interrupted(room_server, done=("sda",))
    ask = question(deploy)
    assert ask is not None, "אין שאלה — אין על מה לענות"
    assert ask["disks"] == 1 and ask["text_he"].endswith("דיסק אחד לא הושלם.")
    assert [d["serial"] for d in ask["machines"][0]["disks"]] == ["S2"]
    beat(room_server)

    answered = post_answer(room_server["admin"], "rewrite")
    assert answered.status_code == 200, answered.text

    view = room(deploy)["round"]
    assert question(deploy) is None and view["wave_number"] == 2 and view["wave_state"] == "open"
    assert view["written_drives"] == 1, "הדיסק שהושלם לפני הכיבוי לא נספר"
    session = hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"]
    assert session is not None and session["id"] != wave
    assert session["target_ports"] == [2], f"נכתב גם דיסק שהושלם: {session['target_ports']}"
    assert asks(ctx) == []
    assert "round_interrupt_answer" in events(ctx)


def test_rewrite_given_while_the_wave_still_runs_is_applied_when_it_ends(room_server):
    """שני משכפלים: אחד כבה, השני עוד כותב. התשובה נשמרת, והגל הבא נפתח רק כשהגל נגמר."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1"], CLONER2: ["T1"]}))
    assert opened.status_code == 200, opened.text
    hello(anon, CLONER1, ["S1"], BOOT_A)
    wave = hello(anon, CLONER2, ["T1"], BOOT_A)["session"]["id"]
    assert hello(anon, CLONER1, ["S1"], BOOT_A)["session"]["state"] == "running"
    progress(anon, wave, CLONER1, {"sda": "writing"})
    progress(anon, wave, CLONER2, {"sda": "writing"})
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"] is None      # משכפל 1 כבה וחזר

    assert post_answer(room_server["admin"], "rewrite").status_code == 200
    row = round_row(ctx)
    assert row["wave_session_id"] == wave and row["wave_number"] == 1, "הגל הרץ הוחלף באמצע"

    progress(anon, wave, CLONER2, {"sda": "done"}, top="done")           # משכפל 2 סיים
    beat(room_server)
    beat(room_server)
    view = room(deploy)["round"]
    assert view["wave_number"] == 2 and view["written_drives"] == 1 and question(deploy) is None
    assert hello(anon, CLONER1, ["S1"], BOOT_B)["session"]["target_ports"] == [1]


# --- 5. בלי תשובה לא כותבים -------------------------------------------------------------


def test_without_an_answer_nothing_is_handed_to_the_cloner(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    interrupted(room_server)
    for _ in range(4):
        beat(room_server)
        assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None, "המשכפל קיבל גל בלי תשובה"
    row = round_row(ctx)
    assert row["state"] == "active" and row["wave_session_id"] is None and row["wave_number"] == 1
    started = room_server["deploy"].post("/api/console/room/start")
    assert started.status_code == 409, f'"התחל גל" עקף את השאלה: {started.text}'


def test_an_unanswered_question_expires_and_the_round_fails_by_name(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    interrupted(room_server)
    beat(room_server)
    assert question(deploy) is not None, "אין שאלה שיכולה לפקוע"
    age_question(ctx, ASK_MAX_SECONDS + 5)
    beat(room_server)

    row = round_row(ctx)
    assert row["state"] == "failed", "שאלה שלא נענתה נשארה פתוחה בלי הגבלה"
    assert row["failed_reason"] == (
        "המשכפל shich-1 כבה או אותחל באמצע הסבב, ולא התקבלה הכרעה תוך 35 דקות"
        " — הסבב נסגר בלי לכתוב. 2 דיסקים לא הושלמו.")
    assert row["wave_number"] == 1 and asks(ctx) == [] and ledger_rows(ctx) == []
    assert "round_interrupt_unanswered" in events(ctx)
    assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None
    assert question(deploy) is None


def test_a_question_inside_its_ceiling_keeps_waiting(room_server):
    """שומר: התקרה אינה סוגרת שאלה שעוד בתוקף."""
    ctx = room_server["ctx"]
    interrupted(room_server)
    beat(room_server)
    assert question(room_server["deploy"]) is not None, "אין שאלה שיכולה לחכות"
    age_question(ctx, ASK_MAX_SECONDS - 60)
    beat(room_server)
    assert round_row(ctx)["state"] == "active" and len(asks(ctx)) == 1


def test_close_ends_the_round_and_writes_nothing_more(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    interrupted(room_server, done=("sda",))
    beat(room_server)

    assert post_answer(room_server["admin"], "close").status_code == 200

    row = round_row(ctx)
    assert row["state"] == "closed" and row["written_drives"] == 1 and row["wave_number"] == 1
    assert room(deploy)["round"] is None
    assert hello(anon, CLONER1, ["S1", "S2"], BOOT_B)["session"] is None
    assert asks(ctx) == [] and ledger_rows(ctx) == []


# --- 6. זהות לפי serial, לא לפי מגירה ---------------------------------------------------


def test_the_incomplete_disk_is_found_by_its_identity_in_another_drawer(room_server):
    """S1 הושלם בחריץ 1, S2 לא הושלם בחריץ 2. בזמן הכיבוי הוחלפו המגירות ביניהם."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "done", "sdb": "writing"})
    assert hello(anon, CLONER1, ["S2", "S1"], BOOT_B)["session"] is None     # S2 עכשיו בחריץ 1

    ask = question(deploy)
    assert ask is not None, "אין שאלה — אין מי שיגיד איפה הדיסק יושב עכשיו"
    disks = ask["machines"][0]["disks"]
    assert [(d["serial"], d["was_port"], d["port"]) for d in disks] == [("S2", 2, 1)], disks

    beat(room_server)
    assert post_answer(room_server["admin"], "rewrite").status_code == 200
    session = hello(anon, CLONER1, ["S2", "S1"], BOOT_B)["session"]
    assert session["target_ports"] == [1], (
        f"נכתב לפי המגירה ולא לפי הדיסק: {session['target_ports']}")
    assert session["target_disks"] == [{"port": 1, "serial": "S2", "size_bytes": SIZE}]


def test_a_disk_of_another_size_with_the_same_serial_is_not_the_same_disk(room_server):
    """הזהות היא serial **וגם** גודל: דיסק אחר שעונה לאותו serial אינו "נמצא"."""
    anon = room_server["anon"]
    wave = start(room_server)
    progress(anon, wave, CLONER1, {"sda": "writing", "sdb": "writing"})
    body = hello_body(CLONER1)
    body.update(joining=True, boot_id=BOOT_B, disks=[
        {"dev": "sda", "size_bytes": SIZE, "model": "Drawer SSD", "serial": "S1",
         "removable": False, "port": 1},
        {"dev": "sdb", "size_bytes": SIZE // 2, "model": "Drawer SSD", "serial": "S2",
         "removable": False, "port": 2}])
    assert anon.post("/api/v1/agent/hello", json=body).status_code == 200
    ask = question(room_server["deploy"])
    assert ask is not None, "אין שאלה"
    found = {d["serial"]: d["port"] for d in ask["machines"][0]["disks"]}
    assert found == {"S1": 1, "S2": None}, found


# --- מי עונה ---------------------------------------------------------------------------


def test_one_answer_closes_the_question_for_everyone(room_server):
    admin = room_server["admin"]
    interrupted(room_server)
    beat(room_server)
    assert post_answer(admin, "rewrite").status_code == 200
    second = post_answer(admin, "close")
    assert second.status_code == 409, second.text
    assert round_row(room_server["ctx"])["state"] == "active", "תשובה שנייה סגרה את הסבב"


def test_the_answer_needs_a_round_operator_and_a_known_word(room_server):
    """#152: "כתוב מחדש" כותב על דיסקים — תפקיד שאינו ברשימת מפעילי הסבבים אינו עונה."""
    from fastapi.testclient import TestClient                  # noqa: PLC0415
    from test_station import add_user_with_role                # noqa: PLC0415

    ctx, anon, admin = room_server["ctx"], room_server["anon"], room_server["admin"]
    interrupted(room_server)
    assert post_answer(anon, "rewrite", [CLONER1]).status_code in (401, 403)
    add_user_with_role(room_server, "auditor", "audit-pass-12", "auditor")
    auditor = TestClient(room_server["app"])
    assert auditor.post("/api/console/login", json={
        "username": "auditor", "password": "audit-pass-12"}).status_code == 200
    assert question(auditor) is not None
    assert post_answer(auditor, "rewrite", [CLONER1]).status_code == 403
    assert admin.post("/api/console/room/interrupt",
                      json={"answer": "resume", "machines": [CLONER1]}).status_code == 400
    assert admin.post("/api/console/room/interrupt", json={}).status_code == 400
    # סקירה 1-ב: בלי רשימת המכונות שהמסך הציג אין תשובה
    assert admin.post("/api/console/room/interrupt", json={"answer": "rewrite"}).status_code == 400
    assert admin.post("/api/console/room/interrupt",
                      json={"answer": "rewrite", "machines": []}).status_code == 400
    assert asks(ctx)[0]["answer"] is None, "תשובה שנדחתה נרשמה"


def test_answering_without_a_question_is_refused(room_server):
    start(room_server)
    refused = post_answer(room_server["admin"], "rewrite")
    assert refused.status_code == 409, refused.text
