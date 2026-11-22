"""#2202 — שאלה למפעיל (חלוקת המקום, RAID) מחזיקה את המחיצה הראשונה של השולח.

ברזל 25/10 (משכפל 2, `ses_ca1fb8e9`): `send_start` ב-21:32:19, שאלת חלוקת
המקום (#1898) עלתה ב-21:32:21, וב-21:35:19 `send_failed: אף מחשב לא הצטרף
לשידור תוך 180 שניות`. #1581 עצר את השעון רק לשאלת SMART (`smart_asks`);
שאלת share/raid נשאלת על המסך בלבד, והשרת יודע עליה רק מה-`prompt` של ה-hello.

הכרעה (03/11): בלי שדה חדש — השולח ממתין כל עוד לחבר חי בגל, לא במצב סופי,
יש `net_devices.prompt`; והסוכן שולח hello בלי prompt כשהשאלה נגמרת.

#3173: השולח היחיד הוא מנוע החתיכות (`ChunkEngine.pending_prompts`, `_wait_marks`), והגל הוא גל
של חדר השיכפול (סבב כיתה הוא v2). ההחזקה ברמת המנוע בלבד: test_chunk_hold_2388_server.py
ו-test_waits_prompt_events_3470.py; כאן — מה-hello ועד המנוע, דרך האפליקציה.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from conftest import HANG_S, MANIFEST_256, hello_body

pytest.importorskip("fastapi")

from fakesend import Cloners, wait_for                                   # noqa: E402
from test_chunksend_2533 import rig                                      # noqa: E402,F401 (fixture)
from test_server_room import CLONER1, room, room_body, room_server       # noqa: E402,F401 (fixture)

#: השאלה כפי שהסוכן שולח אותה בזמן שאלת חלוקת המקום (attended.sh, drawergate.sh).
SHARE_PROMPT = {"waiting_for": "operator", "prompt": "Disk 2: share question"}
FIRST_PART = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))


def _journal_events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal ORDER BY id")]


def _hello(room_server, **extra) -> dict:
    """hello של המשכפל עם מגירה אחת (S1, חריץ 1), ועם מה ש-`extra` מוסיף (השאלה, `joining`)."""
    body = hello_body(CLONER1)
    body["disks"] = [{"dev": "sda", "size_bytes": 256060514304, "model": "Drawer SSD", "serial": "S1",
                      "removable": False, "scheme": "gpt", "has_data": False, "port": 1}]
    body.update(extra)
    answer = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _running(room_server, **extra) -> str:
    """סבב חדר של מגירה אחת, עד גל רץ; ה-hello שמצטרף נושא את `extra`. מחזיר את מזהה הגל."""
    deploy = room_server["deploy"]
    assert deploy.post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]})).status_code == 200
    wave = _hello(room_server, **extra)["session"]["id"]       # the one drawer covers the target: this hello ripens the wave
    assert room(deploy)["round"]["wave_state"] == "running"
    return wave


def test_a_share_question_holds_the_first_partition_until_it_is_answered(room_server):
    """התרחיש של 25/10. כל עוד ה-prompt פתוח — **שום `chunkcast send` לא מופעל**;
    ה-hello שמנקה אותו משחרר, והיומן אומר את שניהם בשם משלו (`send_held_prompt`),
    בלי לגעת ב-`send_held_smart` של #1889.

    **בקרה שלילית:** על הקוד הישן השולח מופעל מיד עם תחילת הגל."""
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    wave = _running(room_server, **SHARE_PROMPT)
    assert wait_for(lambda: "send_start" in _journal_events(ctx))
    assert wait_for(lambda: "send_held_prompt" in _journal_events(ctx)), _journal_events(ctx)
    threading.Event().wait(0.3)
    assert recorder.commands == [], "chunkcast send הופעל בזמן שהמכונה ממתינה לאדם"

    # התשובה: ה-hello שהסוכן שולח כשהשאלה נגמרת — בלי prompt. אחריה המכונה מתייצבת.
    _hello(room_server, joining=False)
    assert wait_for(lambda: "send_prompt_released" in _journal_events(ctx)), _journal_events(ctx)
    Cloners(room_server["anon"], wave, {CLONER1: ["S1"]}).listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    events = _journal_events(ctx)
    assert events.index("send_held_prompt") < events.index("send_prompt_released")
    assert "send_held_smart" not in events and "send_hold_released" not in events   # #1889


def test_a_start_timeout_under_an_open_question_never_says_check_the_cable(rig):
    """"אף מחשב לא הצטרף ... יש לבדוק כבל רשת" שלח את המפעיל לחפש תקלה במכונה
    שחיכתה לו. במנוע החתיכות שאלה פתוחה עוצרת את שעון ההתייצבות (#2202): גם אחרי
    כמה תקרות התחלה הגל אינו נכשל, ולא "בכבל רשת".

    **בקרה שלילית:** בלי `pending_prompts` הגל נכשל בתקרה ("אף מחשב לא התייצב")."""
    rig.make(pending_prompts=lambda _sid: ["78:ac:c0:9b:11:c2"], start_timeout=0.3)
    assert wait_for(lambda: rig.named("send_held_prompt")), rig.events
    assert rig.named("send_held_prompt")[0].endswith("78:ac:c0:9b:11:c2")
    threading.Event().wait(1.2)                         # four start ceilings under the question
    assert rig.named("send_failed") == [], rig.events
    assert rig.engine.status()["state"] == "sending" and rig.runner.commands == []


def test_the_answer_gives_the_room_its_whole_start_ceiling_again(rig):
    """השאלה אינה "אוכלת" את שעון ההתייצבות: אחרי התשובה התקרה מתחילה מחדש, במלואה. (בשולח הישן זו הייתה
    הטענה שה-udp-sender הראשון קיבל `--start-timeout` מלא; במנוע החתיכות השעון הוא של המנוע עצמו.)

    איש אינו מתייצב כאן, ולכן הגל נכשל בשמו — תקרה שלמה **אחרי השחרור**, ולא מיד איתו. הגבול התחתון בלבד
    נבדק: עומס על המכונה רק מאריך אותו.

    **בקרה שלילית:** בלי האיפוס של `started` בזמן שאלה (`ChunkEngine._wait_marks_inner`) הכישלון בא ברגע השחרור."""
    asking = {"open": True}
    rig.make(pending_prompts=lambda _sid: ["78:ac:c0:9b:11:c2"] if asking["open"] else [], start_timeout=2.0)
    assert wait_for(lambda: rig.named("send_held_prompt")), rig.events
    threading.Event().wait(2.5)                         # more than a whole ceiling, under the question
    assert rig.named("send_failed") == [], rig.events
    asking["open"] = False                              # answered
    assert wait_for(lambda: rig.named("send_prompt_released")), rig.events
    released = time.monotonic()
    assert wait_for(lambda: rig.named("send_failed")), rig.events
    assert time.monotonic() - released >= 1.5, "the question ate the start ceiling"


def test_without_a_question_the_sender_starts_at_once_and_says_nothing_new(room_server):
    """השומר: hello בלי prompt — שום עצירה ושום אירוע חדש (עובר בשני המצבים)."""
    recorder = room_server["recorder"]
    wave = _running(room_server)
    Cloners(room_server["anon"], wave, {CLONER1: ["S1"]}).listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    assert "send_held_prompt" not in _journal_events(room_server["ctx"])


# --- prompt_hold: מי מחזיק, ועד מתי -------------------------------------------

CLONER = "aa:bb:cc:00:00:31"
NOW = datetime.fromisoformat("2026-10-25T21:34:00+00:00")


def _member(conn, mac=CLONER, state="waiting", last_seen="2026-10-25T21:33:50+00:00",
            prompt="Disk 2: share question", started_at="2026-10-25T21:32:19+00:00"):
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, started_at)"
        " VALUES ('ses_ca1fb8e9', 'grp_CLONERS', 'img_x', 'C', 1, 0, 'running', 't',"
        " '2026-10-25T21:32:00+00:00', 0, ?)", (started_at,))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES ('ses_ca1fb8e9', ?, ?, ?)", (mac, state, "2026-10-25T21:32:19+00:00"))
    conn.execute("INSERT INTO net_devices (mac, last_seen, prompt) VALUES (?, ?, ?)",
                 (mac, last_seen, prompt))
    conn.commit()


@pytest.fixture()
def conn(tmp_path: Path):
    from server import db
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    c.close()


def test_a_live_member_on_a_question_holds(conn):
    from server.rounds import prompt_hold
    _member(conn)
    assert prompt_hold.waiting_in_wave(conn, "ses_ca1fb8e9", NOW) == [CLONER]
    assert prompt_hold.open_in_wave(conn, "ses_other", NOW) == []


@pytest.mark.parametrize("kw", [
    {"prompt": None},                                    # לא ממתינה
    {"state": "failed"}, {"state": "lost"},              # כבר סופית
    {"last_seen": "2026-10-25T21:31:00+00:00"},          # נשתקה — 180ש' בלי hello
    {"last_seen": "not-a-time"},                         # אין ראיה שהיא חיה
], ids=["no-prompt", "failed", "lost", "silent", "unreadable"])
def test_no_hold_for_a_question_nobody_is_at(conn, kw):
    from server.rounds import prompt_hold
    _member(conn, **kw)
    assert prompt_hold.open_in_wave(conn, "ses_ca1fb8e9", NOW) == []


def test_past_the_ceiling_only_that_machine_leaves_the_wave(conn):
    from server.rounds import prompt_hold
    from server.rounds.prompt_hold import ASK_HOLD_MAX_SECONDS
    _member(conn, started_at="2026-10-25T20:58:00+00:00")       # 36 דקות
    assert ASK_HOLD_MAX_SECONDS < 36 * 60
    assert prompt_hold.waiting_in_wave(conn, "ses_ca1fb8e9", NOW) == []
    row = conn.execute("SELECT state, error FROM session_members WHERE mac = ?", (CLONER,)).fetchone()
    assert row["state"] == "failed" and "שאלה למפעיל" in row["error"], dict(row)
    assert [r["detail"] for r in conn.execute(
        "SELECT detail FROM journal WHERE event = 'prompt_hold_expired'")] == [f"{CLONER} ses_ca1fb8e9"]


# --- הסוכן: ה-prompt מתנקה כשהשאלה נגמרת --------------------------------------

def test_the_question_over_one_hello_without_the_prompt_clears_it(tmp_path):
    """הממצא הצדדי של #2202: אחרי `attended` ה-hello האחרון שיצא נשא עדיין
    את השאלה, ושום hello לא בא אחריו לפני הזרם — `net_devices.prompt` נשאר
    על השאלה שנענתה, והקונסולה הציגה "ממתין למפעיל" לאורך השחזור. עכשיו:
    hello אחד בלי `waiting_for`/`prompt` אחרי התשובה, ו-stdout של הקורא נקי.

    **בקרה שלילית:** על `attended.sh` של הבסיס ה-hello האחרון נושא את ה-prompt."""
    from test_smart import FAKE_HELLO, PRELUDE, env, sh
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + 'export ATTENDED_BEAT_S=1; '
             + 'http_post_json() { printf "%s\\n" "$(cat "$2")" >> "$RUN_DIR/hellos"; }; '
             + 'ask() { sleep 1.5; echo split; }; '
             + "_a=$(attended 'Disk 2: share question' ask); echo \"answer=$_a\"")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "answer=split", out.stdout
    hellos = [json.loads(line) for line in (run / "hellos").read_text().splitlines() if line]
    assert hellos[0]["prompt"] == "Disk 2: share question", hellos
    assert "prompt" not in hellos[-1] and "waiting_for" not in hellos[-1], hellos
    assert hellos[-1]["joining"] is False
