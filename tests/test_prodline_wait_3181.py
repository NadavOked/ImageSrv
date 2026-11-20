"""#3181 (כותב E, צד השרת): בפס ייצור, משכפל שכל הדיסקים שלו סיימו ממתין לדיסק הבא -- והסבב אינו נסגר בינתיים.

הסוכן (`agent/lib/restore/linewait.sh`) נשאר `writing`, ושואל בדיווח: `line_wait: {"s": N}`. כאן, דרך ה-API:

1. התשובה: `production_line.queue_left` (שמות שאף דיסק אינו מחזיק) ליד `insert` -- לפיהם הסוכן מכריע אם לחכות.
2. הקונסולה: `round.production_line.waiting` -- המשכפל שאמר שהוא ממתין **ו**יש לו למה, עם הזמן שעבר.
3. הגל והסבב נשארים פתוחים כל עוד משכפל ממתין, והמפעיל סוגר אותם מהקונסולה. השרת אינו סוגר לבד.

**בקרה שלילית:** החזרת `server/rounds/prodline.py` ו-`server/agent/api.py` לבסיס מפילה את 1 ו-2 (אין `queue_left`,
אין `waiting`). הטסט של 3 הוא **שומר**: גם בבסיס חבר `writing` שמדווח אינו סוגר את הגל -- זו ההתנהגות שההמתנה נשענת
עליה, והוא עובר בשני המצבים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_prodline_3181 import A, B, FIRST, _nine, _open, _progress, _queue, _t, _wave  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, heartbeat, report, room, room_server  # noqa: E402,F401


def _ask(rs, wave, mac, targets, wait):
    """דיווח של משכפל שנשאר `writing`, עם השאלה שלו (`line_wait`) ברמת הגוף."""
    answer = rs["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": "writing", "targets": targets, "line_wait": wait})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _done(serials, **extra):
    """כל המגירות של המשכפל נכתבו ונותקו בבטחה."""
    return [_t(f"sd{chr(ord('a') + i)}", serial, i + 1, "done", detached=True, **extra) for i, serial in enumerate(serials)]


def _waiting(rs) -> list[dict]:
    return room(rs["deploy"])["round"]["production_line"].get("waiting")


def test_the_answer_says_how_many_names_no_disk_holds(room_server):
    """`queue_left`: לפיו ולפי `insert` משכפל שסיים יודע אם לחכות. שם שמור למגירה ריקה כבר אינו בתור."""
    _nine(room_server)
    assert _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    session = cloner_hello(room_server["anon"], CLONER1, A)["session"]
    assert session["production_line"].get("queue_left") == 2, "ה-hello אומר את אותו דבר"
    wave = _wave(room_server)
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True)])
    assert (got["production_line"].get("queue_left"), got["production_line"]["insert"]) == (2, {})
    got = _progress(room_server, wave, CLONER1, [_t("sda", "A1", 1, "done", detached=True, removed=True)])
    assert (got["production_line"].get("queue_left"), got["production_line"]["insert"]) == (1, {"1": "LAB-10"})
    # דיסק שנכשל שומר את השם שלו (B22, 15/11, prodline.FREE = queued בלבד): השם אינו חוזר לתור, ולכן queue_left לא עולה
    got = _progress(room_server, wave, CLONER2, [_t("sdb", "B2", 2, "failed", error="I/O error")])
    assert got["production_line"].get("queue_left") == 1


def test_a_cloner_that_says_it_waits_and_has_names_left_is_shown_waiting_with_the_time(room_server):
    """הליבה: כל חמש המגירות של משכפל 1 נכתבו ונותקו, בתור נשארו שני שמות, והוא שואל -- כבר 754 שניות. הקונסולה
    רואה אותו ממתין, עם הזמן. המעבר נרשם ביומן פעם אחת. דיווח בלי השאלה (דיסק נכתב שוב) מוריד אותו."""
    _nine(room_server)
    assert _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(room_server)
    assert _waiting(room_server) == []
    got = _ask(room_server, wave, CLONER1, _done(A), {"s": 754})
    assert got["ok"] is True and got["production_line"].get("queue_left") == 2
    waiting = _waiting(room_server)
    assert waiting and [w["mac"] for w in waiting] == [CLONER1], waiting
    assert 754 <= waiting[0]["since_s"] <= 760, waiting
    _ask(room_server, wave, CLONER1, _done(A), {"s": 756})
    events = [row["detail"] for row in room_server["ctx"].conn.execute("SELECT detail FROM journal WHERE event = 'prodline'")]
    assert sum(f"wait {CLONER1} queue_left=2 insert=-" in text for text in events) == 1, events
    # משכפל 2 לא שאל: הוא אינו ממתין
    _progress(room_server, wave, CLONER2, _done(B))
    assert [w["mac"] for w in _waiting(room_server) or []] == [CLONER1]
    # דיסק חדש נכתב במשכפל 1: הדיווח כבר אינו נושא את השאלה
    _progress(room_server, wave, CLONER1, _done(A, removed=True) + [_t("sdf", "N1", 1, "writing")])
    assert _waiting(room_server) == []


def test_a_cloner_with_nothing_left_for_it_is_told_so_and_is_not_shown_waiting(room_server):
    """עשרה דיסקים על תשע מגירות: שם אחד בתור. המגירה שהתרוקנה במשכפל 2 שומרת אותו לעצמה, ולכן למשכפל 1 לא נשאר
    דבר: התור ריק ואין שם ששמור למגירה שלו. הוא שואל, נענה, ואינו "ממתין". משכפל 2 כן."""
    _nine(room_server)
    assert _open(room_server["deploy"], 10, {"names": FIRST, "queue": _queue(1)}).status_code == 200
    wave = _wave(room_server)
    _progress(room_server, wave, CLONER2, _done(B))
    got = _ask(room_server, wave, CLONER2, _done(B, removed=True), {"s": 3})
    assert got["production_line"].get("queue_left") == 0 and got["production_line"]["insert"] == {"1": "LAB-10"}
    got = _ask(room_server, wave, CLONER1, _done(A), {"s": 3})
    assert (got["production_line"].get("queue_left"), got["production_line"]["insert"]) == (0, {}), got
    assert [w["mac"] for w in _waiting(room_server) or []] == [CLONER2]


@pytest.mark.parametrize("wait", [{"s": -1}, {"s": "754"}, {"s": True}, {"s": 1.5}, {}, True, "754", None])
def test_a_question_that_is_not_whole_seconds_is_not_said(room_server, wait):
    """עיקרון 5: שדה שאינו `{"s": שלם אי-שלילי}` הוא "לא נאמר". הדיווח עצמו עובר, והמשכפל אינו מוצג כממתין."""
    _nine(room_server)
    assert _open(room_server["deploy"], 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(room_server)
    got = _ask(room_server, wave, CLONER1, _done(A), wait)
    assert got["ok"] is True and got["production_line"].get("queue_left") == 2
    assert _waiting(room_server) == []


def test_the_wave_and_the_round_stay_open_while_a_cloner_waits_and_the_operator_closes_them(room_server):
    """שומר (עובר גם בלי השינוי): משכפל 2 סיים ודיווח `done`; משכפל 1 ממתין לדיסק, `writing`. הגל רץ והסבב פעיל --
    השרת אינו סוגר לבד. המפעיל סוגר מהקונסולה, ומאותו רגע הדיווח של המשכפל נענה `not_open`: כך ההמתנה נגמרת."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    _nine(room_server)
    assert _open(deploy, 11, {"names": FIRST, "queue": _queue(2)}).status_code == 200
    wave = _wave(room_server)
    assert deploy.post("/api/console/room/start").status_code == 200
    report(anon, wave, CLONER2, {f"sd{chr(ord('a') + i)}": "done" for i in range(len(B))})
    for _ in range(3):
        assert _ask(room_server, wave, CLONER1, _done(A), {"s": 900})["ok"] is True
        heartbeat(room_server)
    view = room(deploy)["round"]
    assert view is not None and view["wave_state"] == "running" and view["wave_session_id"] == wave, view
    assert deploy.post("/api/console/room/close", json={"confirm_name": "Office 2024 Standard"}).json()["ok"] is True
    heartbeat(room_server)
    assert room(deploy)["round"] is None
    late = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing", "targets": _done(A), "line_wait": {"s": 902}})
    assert late.json().get("code") == "not_open", late.text
