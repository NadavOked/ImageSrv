"""#2202 — שאלה למפעיל (חלוקת המקום, RAID) מחזיקה את המחיצה הראשונה של השולח.

ברזל 25/10 (משכפל 2, `ses_ca1fb8e9`): `send_start` ב-21:32:19, שאלת חלוקת
המקום (#1898) עלתה ב-21:32:21, וב-21:35:19 `send_failed: אף מחשב לא הצטרף
לשידור תוך 180 שניות`. #1581 עצר את השעון רק לשאלת SMART (`smart_asks`);
שאלת share/raid נשאלת על המסך בלבד, והשרת יודע עליה רק מה-`prompt` של ה-hello.

הכרעה (03/11): בלי שדה חדש — השולח ממתין כל עוד לחבר חי בגל, לא במצב סופי,
יש `net_devices.prompt`; והסוכן שולח hello בלי prompt כשהשאלה נגמרת.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from conftest import HANG_S, hello_body, setup_classroom

pytest.importorskip("fastapi")

from server.rounds import sender as sender_module                      # noqa: E402
from server.rounds.sender import DEFAULT_START_TIMEOUT, SenderEngine    # noqa: E402
from test_sender import (NOBODY_JOINED_LOG, Recorder, _journal_events,  # noqa: E402
                         free_ports, library, wait_for)                 # noqa: F401 (fixtures)

#: השאלה כפי שהסוכן שולח אותה בזמן שאלת חלוקת המקום (attended.sh, drawergate.sh).
SHARE_PROMPT = {"waiting_for": "operator", "prompt": "Disk 2: share question"}


def test_a_share_question_holds_the_first_partition_until_it_is_answered(
    server_with_sender, free_ports, monkeypatch,
):
    """התרחיש של 25/10. כל עוד ה-prompt פתוח — **אף udp-sender לא מופעל**;
    ה-hello שמנקה אותו משחרר, עם `--start-timeout` מלא, והיומן אומר את שניהם
    בשם משלו (`send_held_prompt`), בלי לגעת ב-`send_held_smart` של #1889.

    **בקרה שלילית:** על הקוד הישן השולח מופעל מיד עם תחילת הגל."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    server, recorder = server_with_sender
    ctx = server["ctx"]
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    for _ in range(2):    # מצטרף ושואל; ה-hello השני מוצא סבב בשל ומתחיל את הגל
        server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), **SHARE_PROMPT})
    assert wait_for(lambda: "send_start" in _journal_events(ctx))
    threading.Event().wait(0.3)
    assert recorder.commands == [], "udp-sender הופעל בזמן שהמכונה ממתינה לאדם"
    assert "send_held_prompt" in _journal_events(ctx)

    # התשובה: ה-hello שהסוכן שולח כשהשאלה נגמרת — בלי prompt.
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "joining": False})
    assert recorder.spawned.wait(timeout=HANG_S)
    events = _journal_events(ctx)
    assert events.index("send_held_prompt") < events.index("send_prompt_released")
    assert "send_held_smart" not in events and "send_hold_released" not in events   # #1889
    first = recorder.commands[0]
    assert first[first.index("--start-timeout") + 1] == str(int(DEFAULT_START_TIMEOUT))
    recorder.processes[0].release()


def test_a_start_timeout_under_an_open_question_says_so_not_check_the_cable(library, free_ports):
    """"אף מחשב לא הצטרף ... יש לבדוק כבל רשת" שלח את המפעיל לחפש תקלה במכונה
    שחיכתה לו. udp-sender שיצא בלי לשדר בזמן שחבר בגל עומד על שאלה — נאמר כך.

    **בקרה שלילית:** על הקוד הישן ההודעה היא "אף מחשב לא הצטרף"."""
    failed: list[str] = []
    recorder = Recorder(log_text=NOBODY_JOINED_LOG)        # יצא 0 בלי Starting transfer
    engine = SenderEngine(library, runner=recorder,
                          on_event=lambda e, d: failed.append(d) if e == "send_failed" else None)
    # לפני ההפעלה אין שאלה (אין עצירה); אחרי שהשולח יצא — יש.
    engine.pending_prompts = lambda _sid: ["78:ac:c0:9b:11:c2"] if recorder.commands else []
    engine.start({"id": "ses_q", "image_id": "img_7f3a91", "joined": 1})
    assert wait_for(lambda: failed)
    assert "ממתין לתשובת מפעיל" in failed[0] and "78:ac:c0:9b:11:c2" in failed[0], failed
    assert "אף מחשב לא הצטרף" not in failed[0], failed


def test_without_a_question_the_sender_starts_at_once_and_says_nothing_new(
    server_with_sender, free_ports,
):
    """השומר: hello בלי prompt — שום עצירה ושום אירוע חדש (עובר בשני המצבים)."""
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    for _ in range(2):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=HANG_S)
    assert "send_held_prompt" not in _journal_events(server["ctx"])
    recorder.processes[0].release()


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
