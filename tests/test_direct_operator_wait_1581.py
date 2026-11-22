"""#1581 בהפצה ישירה — מחשב בגל שממתין לאדם מחזיק את השידור של הגל.

עד #3173 השולח של הפצה ישירה היה udp-sender במחשב הבנייה, עם `--start-timeout 180` משלו, והשרת
אמר לו ב-`task.direct.operator_wait` כמה חברים בגל עומדים על שאלה למפעיל (חלוקת המקום #1898, RAID
#1808). משכפל שעמד על שאלה יותר משלוש דקות — המקור יצא "אף מחשב משכפל לא הצטרף לזרם".

היום הפצה ישירה היא חתיכות בלבד (#3161): `ChunkEngine` של השרת מתזמר את הגל, ובזמן שהוא ממתין
שהמשכפלים יתייצבו הוא שואל בעצמו את `prompt_hold` (`pending_prompts` -- אותה שאילתה ואותן תקרות
#2202). מחשב הבנייה משדר רק הזמנה שהמנוע נתן לו, ולכן ההחזקה היא של המנוע: שאלה פתוחה מחזיקה את
הגל (`send_held_prompt`, ואין הזמנה), התשובה משחררת אותו (`send_prompt_released`), חבר שעבר את
התקרה משתחרר מהגל לבדו, וגל שלא רץ אינו מחזיק דבר.
"""

from __future__ import annotations

import threading

import pytest

from conftest import hello_body
from fakesend import wait_for
from test_direct_live_receivers import CLONER3, _running_three
from test_direct_server import _build_hello, _cloners, _order
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401

pytest.importorskip("fastapi")

#: השאלה כפי שהסוכן שולח אותה בזמן שאלת מגירה (attended.sh, drawergate.sh).
SHARE_PROMPT = {"waiting_for": "operator", "prompt": "Disk 2: share question", "joining": False}


def _asks(room_server, mac: str, asking: bool = True) -> None:
    body = {**hello_body(mac), "joining": False}
    if asking:
        body.update(SHARE_PROMPT)
    assert room_server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200


def _direct(room_server) -> dict:
    return _build_hello(room_server["anon"])["task"]["direct"]


def _named(room_server, event: str) -> list[str]:
    return [r["detail"] for r in room_server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def test_a_member_on_a_question_holds_the_wave(room_server):
    opened = _running_three(room_server)
    assert _named(room_server, "send_held_prompt") == []
    _asks(room_server, CLONER2)
    assert wait_for(lambda: _named(room_server, "send_held_prompt")), "the engine did not hold for the question"
    assert _named(room_server, "send_held_prompt") == [f'{opened["wave_session_id"]} {CLONER2}']
    direct = _direct(room_server)
    assert direct["session_state"] == "running"      # הגל עצמו לא השתנה
    assert direct["chunk"]["order"] is None           # ומחשב הבנייה לא התבקש לשדר
    assert room_server["recorder"].commands == []


def test_the_answer_clears_it(room_server):
    """ה-hello בלי prompt שהסוכן שולח כשהשאלה נגמרת (attended)."""
    _running_three(room_server)
    _asks(room_server, CLONER1)
    _asks(room_server, CLONER3)
    assert wait_for(lambda: _named(room_server, "send_held_prompt"))
    _asks(room_server, CLONER1, asking=False)
    threading.Event().wait(1.0)                        # five polls of the engine (`chunksend.POLL_S`)
    assert _named(room_server, "send_prompt_released") == [], "CLONER3 still waits for a person"
    _asks(room_server, CLONER3, asking=False)
    assert wait_for(lambda: _named(room_server, "send_prompt_released")), "the answer did not release the wave"
    assert len(_named(room_server, "send_held_prompt")) == 1   # one hold, and one release


def test_a_member_past_the_ceiling_leaves_the_wave_and_stops_holding(room_server):
    """אותה תקרה כמו השולח של השרת (`ASK_HOLD_MAX_SECONDS` מתחילת הגל):
    שאלה שאיש לא עונה עליה אינה מחזיקה את הגל לנצח — רק החבר הזה משתחרר."""
    opened = _running_three(room_server)
    conn = room_server["ctx"].conn
    _asks(room_server, CLONER2)
    conn.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                 (opened["wave_session_id"],))
    conn.commit()

    def member():
        return conn.execute("SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
                            (opened["wave_session_id"], CLONER2)).fetchone()["state"]

    assert wait_for(lambda: member() == "failed"), member()       # the engine's own poll releases it
    # the journal line is written after the member is failed (`prompt_hold.waiting_in_wave`), by the engine's thread
    assert wait_for(lambda: conn.execute("SELECT 1 FROM journal WHERE event = 'prompt_hold_expired'").fetchone())
    # a hold that began before the ceiling ended with it
    assert wait_for(lambda: len(_named(room_server, "send_prompt_released"))
                    == len(_named(room_server, "send_held_prompt")))
    # and the wave goes on without it: the two that are left line up, and the source is ordered to send
    _cloners(room_server, opened, {CLONER1: ["S1"], CLONER3: ["S3"]}).listen(1)
    assert _order(room_server)["part"] == 1


def test_a_wave_that_is_not_running_holds_nothing(room_server):
    """לפני `running` אין גל של המנוע, ואין מה לעצור."""
    from test_direct_server import _open_direct, _register_build
    from test_server_room import cloner_hello

    _register_build(room_server)
    cloner_hello(room_server["anon"], CLONER1, ["S1"])
    opened = _open_direct(room_server["admin"], [{"mac": CLONER1, "ports": [1]}]).json()
    assert opened.get("task_id"), opened
    _asks(room_server, CLONER1)
    direct = _direct(room_server)
    assert direct["session_state"] != "running"
    assert room_server["ctx"].chunk.status() is None
    assert _named(room_server, "send_held_prompt") == []
