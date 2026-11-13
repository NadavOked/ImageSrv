"""#1581 בהפצה ישירה — השרת אומר למקור שמחשב בגל ממתין לאדם.

`SenderEngine` של השרת אינו מפעיל את udp-sender של המחיצה הראשונה כל עוד
חבר בגל עומד על שאלה למפעיל (`_hold_for_asks`, `prompt_hold`, #2202), ועוצר
אותו אם השאלה עולה לפני הבייט הראשון. בגל שהמקור שלו הוא מחשב בנייה
(#715) המנוע הזה **אינו רץ** — udp-sender רץ במחשב הבנייה, עם
`--start-timeout 180` משלו, והשרת לא אמר לו דבר על השאלה. משכפל שעמד על
שאלת חלוקת המקום (#1898) או RAID (#1808) יותר משלוש דקות — המקור יצא
"אף מחשב משכפל לא הצטרף לזרם".

כאן: `task.direct.operator_wait` = כמה חברים בגל עומדים עכשיו על שאלה —
אותה שאילתה ואותן תקרות כמו השולח של השרת.

**בקרה שלילית:** על `server/rounds/direct.py` של `origin/main` השדה חסר
(`KeyError: 'operator_wait'`) בשלושת הטסטים הראשונים; הרביעי (גל שלא רץ)
נופל גם הוא על אותו שדה חסר.
"""

from __future__ import annotations

import pytest

from conftest import hello_body
from test_direct_live_receivers import CLONER3, _running_three
from test_direct_server import _build_hello
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


def test_a_member_on_a_question_is_told_to_the_source(room_server):
    _running_three(room_server)
    assert _direct(room_server)["operator_wait"] == 0
    _asks(room_server, CLONER2)
    direct = _direct(room_server)
    assert direct["operator_wait"] == 1
    assert direct["session_state"] == "running"      # הגל עצמו לא השתנה


def test_the_answer_clears_it(room_server):
    """ה-hello בלי prompt שהסוכן שולח כשהשאלה נגמרת (attended)."""
    _running_three(room_server)
    _asks(room_server, CLONER1)
    _asks(room_server, CLONER3)
    assert _direct(room_server)["operator_wait"] == 2
    _asks(room_server, CLONER1, asking=False)
    assert _direct(room_server)["operator_wait"] == 1
    _asks(room_server, CLONER3, asking=False)
    assert _direct(room_server)["operator_wait"] == 0


def test_a_member_past_the_ceiling_leaves_the_wave_and_stops_holding(room_server):
    """אותה תקרה כמו השולח של השרת (`ASK_HOLD_MAX_SECONDS` מתחילת הגל):
    שאלה שאיש לא עונה עליה אינה מחזיקה את המקור לנצח — רק החבר הזה משתחרר."""
    opened = _running_three(room_server)
    conn = room_server["ctx"].conn
    _asks(room_server, CLONER2)
    conn.execute("UPDATE sessions SET started_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                 (opened["wave_session_id"],))
    conn.commit()
    assert _direct(room_server)["operator_wait"] == 0
    state = conn.execute("SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
                         (opened["wave_session_id"], CLONER2)).fetchone()["state"]
    assert state == "failed"
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'prompt_hold_expired'").fetchone()


def test_a_wave_that_is_not_running_holds_nothing(room_server):
    """המקור ממתין ל-`running` בכל מקרה; לפני כן אין מה לעצור."""
    from test_direct_server import _open_direct, _register_build
    from test_server_room import cloner_hello

    _register_build(room_server)
    cloner_hello(room_server["anon"], CLONER1, ["S1"])
    opened = _open_direct(room_server["admin"], [{"mac": CLONER1, "ports": [1]}]).json()
    assert opened.get("task_id"), opened
    _asks(room_server, CLONER1)
    direct = _direct(room_server)
    assert direct["session_state"] != "running"
    assert direct["operator_wait"] == 0
