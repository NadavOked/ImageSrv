"""#3723 -- K-STALL ("הזרם נעצר ולא הכונן") לא נאמר אחרי שהשידור כבר נגמר.

ברזל (משכפל 2, סבב `room_63d21adc`, APS `SICSD40302WL` + Samsung, LabRoot `bf79ff8b3`, 18/11): `send_done` ב-11:59:26Z; ה-APS
היה דיסק איטי שהשלים ברקע; ב-12:02:27Z שורה 7447 ב-`brain_decisions`: `K-STALL / STREAM_STALLED`, "כל 2 המגירות הכותבות עצרו
יחד (לפחות 180ש')". הסבב נגמר תקין ב-12:18:11Z. אחרי `send_done` אין זרם: המגירות רק מסיימות לכתוב, ושתיקתן אינה "הזרם נעצר".

מה נבדק: (1) אחרי `send_done` של הגל הפסק הוא `STREAM_ENDED` ולא `STREAM_STALLED`, ולא `NOT_STALLED`; (2) לפני `send_done` -- וגם
כשלגל אחר (קודם) יש `send_done` -- אותה עצירה עדיין `STREAM_STALLED`, כלומר עצירה אמיתית באמצע שידור לא מוסתרת; (3) עצירה
שנפתחה לפני `send_done` נסגרת בתוצאה "השידור הסתיים" ולא נרשמת מחדש; (4) מסך החדר מעביר את מזהה הגל.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, silence, veto
from server.rounds import room
from test_server_room import CLONER1, cloner_hello, room as room_json, room_body, room_server  # noqa: F401 -- room_server: fixture

R = "room_63d21adc"
WAVE = "ses_e5ffe27f"
OLD_WAVE = "ses_aaaa1111"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    silence.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()
    silence.forget()


def _machines(*stalled):
    return [{"drawer_list": [{"state": "writing", "stalled_s": s} for s in stalled]}]


def _rows(conn):
    return list(reversed(memory.decisions(conn, rule="K-STALL", limit=64)))


def _send_done(conn, wave=WAVE):
    db.journal(conn, "send_done", f"{wave} ok", round_id=wave)


# --- הפסק הטהור --------------------------------------------------------------------------------------------------------


def test_after_the_send_ended_the_verdict_is_not_relevant_not_stalled_and_not_not_stalled():
    machines = _machines(400, 400)
    assert silence.judge_stall(machines, R).verdict == "STREAM_STALLED"        # כמו היום, בלי ראיה שהשידור הסתיים
    ended = silence.judge_stall(machines, R, stream_ended=True)
    assert ended.verdict == "STREAM_ENDED"
    assert ended.verdict not in ("STREAM_STALLED", "NOT_STALLED", "NOT_MEASURED")
    assert "send_done" in ended.reason


# --- דרך מסך החדר (`room._stream_stalled`), עם היומן --------------------------------------------------------------------


def test_stalled_drawers_before_send_done_are_a_stalled_stream_and_after_it_they_are_not(conn):
    stalled = _machines(400, 400)
    assert room._stream_stalled(stalled, conn, R, WAVE) is True                # באמצע שידור: עצירה אמיתית
    _send_done(conn)
    assert room._stream_stalled(stalled, conn, R, WAVE) is False               # אחרי: השלמה, לא זרם


def test_a_send_done_of_another_wave_does_not_hide_a_stall_of_this_one(conn):
    _send_done(conn, OLD_WAVE)
    assert room._stream_stalled(_machines(400, 400), conn, R, WAVE) is True


def test_a_wave_that_is_not_known_keeps_the_label_as_it_was(conn):
    """אין גל, או אין חיבור ליומן = "לא ידוע שהסתיים": התווית נשפטת כמו קודם, ולא נבלעת."""
    _send_done(conn)
    assert room._stream_stalled(_machines(400, 400), conn, R, None) is True
    assert room._stream_stalled(_machines(400, 400), None, R, WAVE) is True


def test_a_journal_that_cannot_be_read_is_not_known_to_have_ended():
    """הכשל בקריאה אינו "השידור הסתיים": עצירה אמיתית לא מוסתרת בגללו."""
    class Broken:
        def execute(self, *a, **k):
            raise RuntimeError("journal unreadable")

    assert silence.stream_sent_done(Broken(), WAVE) is False


def test_the_check_for_send_done_runs_only_when_every_drawer_is_stalled(conn, monkeypatch):
    """המסך נקרא בלי הרף: קריאת היומן לא קורית בכל דופק, רק כשהמוח כבר היה אומר "הזרם נעצר"."""
    calls = []
    monkeypatch.setattr(silence, "stream_sent_done", lambda *a, **k: calls.append(a) or False)
    room._stream_stalled(_machines(5, 400), conn, R, WAVE)
    room._stream_stalled(_machines(400), conn, R, WAVE)
    assert calls == []
    room._stream_stalled(_machines(400, 400), conn, R, WAVE)
    assert len(calls) == 1


# --- ההחלטה הרשומה (`brain_decisions`) --------------------------------------------------------------------------------


def test_a_stall_opened_before_the_send_ended_is_closed_with_the_reason_and_is_not_recorded_again(conn):
    stalled = _machines(400, 400)
    for _ in range(3):
        assert room._stream_stalled(stalled, conn, R, WAVE) is True
    (row,) = _rows(conn)
    assert row["verdict"] == "STREAM_STALLED" and row["outcome_state"] == "open"
    _send_done(conn)
    for _ in range(3):                                                         # שלוש דקות של השלמה, המסך נקרא בלי הרף
        assert room._stream_stalled(stalled, conn, R, WAVE) is False
    (row,) = _rows(conn)                                                       # שורה אחת: אין STREAM_STALLED חדש אחרי send_done
    assert row["verdict"] == "STREAM_STALLED"
    assert row["outcome_state"] == "closed" and "השידור הסתיים" in row["outcome"]


def test_a_round_that_ends_its_send_while_slow_drawers_complete_records_no_stall_at_all(conn):
    """שורה 7447: השידור נגמר, ורק אז המגירות נעצרו 180 ש'. אין שורת `STREAM_STALLED`."""
    _send_done(conn)
    for _ in range(4):
        assert room._stream_stalled(_machines(400, 400), conn, R, WAVE) is False
    assert _rows(conn) == []


# --- מקצה לקצה, דרך `GET /api/console/room`: ההתנהגות שהמפעיל רואה, בלי להכיר את החתימות החדשות --------------------------


def _two_writing_drawers_standing(room_server, standing_s: int = 400) -> str:
    """סבב פתוח עם שתי מגירות כותבות שלא זזו `standing_s` ש' (חותמת `moved_at` ישנה). מחזיר את מזהה הגל."""
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    assert deploy.post("/api/console/room", json=room_body(room_server, 4, {CLONER1: ["S1", "S2"]})).status_code == 200
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 50, "bytes_total": 100, "state": "writing"},
                    {"dev": "sdb", "bytes_written": 30, "bytes_total": 100, "state": "writing"}]}).status_code == 200
    old = (datetime.now(timezone.utc) - timedelta(seconds=standing_s)).isoformat(timespec="seconds")
    got = ctx.conn.execute("SELECT targets_json FROM session_members WHERE session_id = ? AND mac = ?",
                           (wave, CLONER1)).fetchone()
    targets = json.loads(got["targets_json"])
    for t in targets:
        t["moved_at"] = old
    ctx.conn.execute("UPDATE session_members SET targets_json = ? WHERE session_id = ? AND mac = ?",
                     (json.dumps(targets), wave, CLONER1))
    ctx.conn.commit()
    # `target_drives` = 4 מול שני דיסקים: הסבב לא יוצא לדרך, אין שולח ואין `send_done` אלא מה שהטסט עצמו כותב. עם 2 מול 2 הסבב
    # יצא לדרך לבד, והשולח המזויף של הפיקסצ'ר רושם `send_done` מתהליכון רקע בזמן לא קבוע (3 מ-4 ריצות נפלו על זה).
    assert ctx.chunk.status() is None
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'send_done'").fetchone()[0] == 0
    return wave


def test_the_room_screen_says_the_stream_stopped_before_send_done_and_not_after_it(room_server):
    """שורה 7447 כפי שהמפעיל רואה אותה: שתי מגירות כותבות עצורות 400 ש'. באמצע שידור -- "הזרם נעצר". אחרי `send_done` -- לא."""
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    wave = _two_writing_drawers_standing(room_server)
    assert room_json(deploy)["stream_stalled"] is True                         # עצירה אמיתית באמצע שידור: נשארת
    _send_done(ctx.conn, wave)
    assert room_json(deploy)["stream_stalled"] is False, "אחרי send_done: המגירות רק מסיימות לכתוב, אין זרם שנעצר"


def test_the_room_screen_keeps_the_stall_when_only_an_older_wave_sent_done(room_server):
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    _two_writing_drawers_standing(room_server)
    _send_done(ctx.conn, OLD_WAVE)
    assert room_json(deploy)["stream_stalled"] is True


def test_the_stall_that_was_recorded_before_send_done_is_closed_by_it_through_the_room_screen(room_server):
    deploy, ctx = room_server["deploy"], room_server["ctx"]
    wave = _two_writing_drawers_standing(room_server)
    for _ in range(2):
        assert room_json(deploy)["stream_stalled"] is True
    (row,) = _rows(ctx.conn)
    assert row["verdict"] == "STREAM_STALLED" and row["outcome_state"] == "open"
    _send_done(ctx.conn, wave)
    for _ in range(3):
        room_json(deploy)
    (row,) = _rows(ctx.conn)                                                   # שורה אחת, לא שנייה
    assert row["outcome_state"] == "closed" and "השידור הסתיים" in row["outcome"]


# --- החיווט: מסך החדר מעביר את מזהה הגל -----------------------------------------------------------------------------------


def test_the_room_screen_hands_the_wave_of_the_round_to_the_brain(room_server, monkeypatch):
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]})).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    seen = []
    monkeypatch.setattr(silence, "stream_stalled", lambda *a, **k: seen.append((a, k)) or False)
    view = deploy.get("/api/console/room").json()
    assert view["round"]["wave_session_id"]
    assert seen, "status_view did not ask the brain"
    for args, kwargs in seen:
        assert (args[2], args[3] if len(args) > 3 else kwargs.get("wave_id")) ==             (view["round"]["id"], view["round"]["wave_session_id"])
