"""#3064: המוח של פס הייצור, בצל -- מי נשאר דלוק (B24, G13), המטרה (B38), התשובה האוטומטית (B42), ומגירה
שצריכה השלמה בכל גל (B33).

הכלל של B24 הוא המשפט של נדב (14/11): 20 דיסקים, 6 משכפלים עם 3 מגירות -- אחרי הגל הראשון נשארים 2, ולכן
משכפל אחד, לפי המספר, נשאר דלוק והשאר כבים. ההתנהגות נבדקת גם דרך ה-API (פתיחה, hello, progress), כדי
שהבקרה השלילית תיפול על ההחלטה שנרשמה ולא על שם של פונקציה.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.brain import line, slotfill  # noqa: E402
from test_prodline_3181 import _nine, _open, _wave  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, report, room, room_server  # noqa: E402,F401


def _c(n, drawers=3, held=False, number="same"):
    return line.Cloner(mac=f"aa:00:00:00:00:{n:02d}", name=f"משכפל-{n}",
                       number=n if number == "same" else number, drawers=drawers, held=held)


# --- B24: מי נשאר דלוק (טהור) ----------------------------------------------------------------------------------


def test_twenty_disks_six_cloners_of_three_keeps_one_by_number():
    """נדב 14/11, המספרים שלו: נשארו 2, מגירות של משכפל אחד מכסות -- הנמוך במספר, לא הראשון ברשימה."""
    cloners = [_c(n) for n in (4, 2, 6, 1, 5, 3)]
    got = line.power(2, cloners)
    assert got.verdict == line.KEEP
    assert got.keep == (_c(1).mac,)
    assert "משכפל-1 נשאר דלוק" in got.reason and "משכפל-2" in got.reason   # השאר היו נכבים, בשמם


def test_more_than_one_cloner_stays_on_when_the_remaining_needs_it():
    got = line.power(7, [_c(n) for n in (1, 2, 3, 4)])
    assert got.keep == (_c(1).mac, _c(2).mac, _c(3).mac)


def test_a_held_cloner_is_not_a_candidate_and_a_cloner_without_a_number_comes_last():
    cloners = [_c(1, held=True), _c(9, number=None), _c(2)]
    got = line.power(3, cloners)
    assert got.keep == (_c(2).mac,)
    assert "מוחזק" in got.reason


def test_drawers_that_were_not_read_are_undecided_not_zero():
    got = line.power(2, [_c(1, drawers=None), _c(2)])
    assert got.verdict == line.UNDECIDED and got.keep == ()
    assert "לא נקרא" in got.reason


def test_when_all_together_do_not_cover_all_stay_on():
    got = line.power(20, [_c(1), _c(2)])
    assert got.keep == (_c(1).mac, _c(2).mac) and "עוד גל" in got.reason


# --- B38: המטרה (טהור) --------------------------------------------------------------------------------------


def test_one_wave_is_the_fastest_disk_and_more_is_the_most_disks_an_hour():
    assert line.objective(9, 9, 2).verdict == line.FASTEST_DISK
    got = line.objective(20, 18, 6)
    assert got.verdict == line.MOST_PER_HOUR and "waves=2" in got.evidence
    assert line.objective(9, None, 2).verdict == line.UNDECIDED


# --- דרך ה-API: פס ייצור של 11 דיסקים ב-9 מגירות ----------------------------------------------------------------


def _decisions(conn, rule, action):
    rows = conn.execute("SELECT * FROM brain_decisions WHERE rule = ? AND action = ? ORDER BY id",
                        (rule, action)).fetchall()
    return [dict(r) for r in rows]


def test_after_a_wave_of_a_line_the_brain_keeps_the_lowest_number_on_and_learns_who_came(room_server):
    line.forget()
    conn = room_server["ctx"].conn
    _nine(room_server)                                 # CLONER1: 5 מגירות, CLONER2: 4
    conn.execute("UPDATE machines SET suffix = '07' WHERE mac = ?", (CLONER1,))
    conn.execute("UPDATE machines SET suffix = '03' WHERE mac = ?", (CLONER2,))
    conn.commit()
    assert _open(room_server["deploy"], 11).status_code == 200
    wave = _wave(room_server)

    objective = _decisions(conn, line.RULE_LINE, line.OBJECTIVE)
    assert [d["verdict"] for d in objective] == [line.MOST_PER_HOUR], "המטרה נרשמת פעם אחת, כשהגל הראשון פתוח"

    assert room_server["deploy"].post("/api/console/room/start").status_code == 200
    report(room_server["anon"], wave, CLONER1, {f"sd{c}": "done" for c in "abcde"})
    report(room_server["anon"], wave, CLONER2, {f"sd{c}": "done" for c in "abcd"})
    cloner_hello(room_server["anon"], CLONER2, ["N1", "N2", "B3", "B4"])   # הדופק שמסיים את הגל
    assert room(room_server["deploy"])["round"]["wave_number"] == 2

    power = _decisions(conn, line.RULE_POWER, line.POWER)
    assert len(power) == 1 and power[0]["verdict"] == line.KEEP and power[0]["mode"] == "shadow"
    evidence = json.loads(power[0]["evidence_json"])
    assert "remaining=2" in evidence
    keep = [e for e in evidence if e.startswith("keep=")]
    assert len(keep) == 1 and "3" in keep[0], "משכפל 3 (CLONER2) לפי המספר, לא CLONER1 שקודם לו ב-MAC"
    assert power[0]["outcome"] is None, "התוצאה נקבעת בגל הבא"

    cloner_hello(room_server["anon"], CLONER2, ["N1", "N2", "B3", "B4"])   # מצטרף לגל 2: 2 טריות מכסות
    wave2 = room(room_server["deploy"])["round"]["wave_session_id"]
    report(room_server["anon"], wave2, CLONER2, {"sda": "done", "sdb": "done"})
    cloner_hello(room_server["anon"], CLONER2, ["N1", "N2", "B3", "B4"])
    assert room(room_server["deploy"])["round"] is None
    power = _decisions(conn, line.RULE_POWER, line.POWER)
    assert len(power) == 1 and "הצטרפו" in power[0]["outcome"] and "תואם" in power[0]["outcome"]


def test_a_regular_round_records_no_power_decision(room_server):
    line.forget()
    conn = room_server["ctx"].conn
    _nine(room_server)
    assert _open(room_server["deploy"], 9).status_code == 200
    wave = _wave(room_server)
    assert [d["verdict"] for d in _decisions(conn, line.RULE_LINE, line.OBJECTIVE)] == [line.FASTEST_DISK]
    report(room_server["anon"], wave, CLONER1, {"sda": "failed", **{f"sd{c}": "done" for c in "bcde"}})
    report(room_server["anon"], wave, CLONER2, {f"sd{c}": "done" for c in "abcd"})
    cloner_hello(room_server["anon"], CLONER2, ["B1", "B2", "B3", "B4"])
    assert room(room_server["deploy"])["round"]["wave_number"] == 2, "גל שני לדיסק שנכשל -- וזה אינו פס ייצור"
    assert _decisions(conn, line.RULE_POWER, line.POWER) == []


def test_off_records_nothing(room_server):
    line.forget()
    conn = room_server["ctx"].conn
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('brain_veto_mode', 'off')")
    conn.commit()
    _nine(room_server)
    assert _open(room_server["deploy"], 11).status_code == 200
    _wave(room_server)
    assert _decisions(conn, line.RULE_LINE, line.OBJECTIVE) == []


# --- B42: בפס אין שאלות ------------------------------------------------------------------------------------


def test_in_a_line_the_brain_would_answer_rewrite_and_learns_what_the_operator_said(room_server):
    from server.rounds import interrupt                                  # noqa: PLC0415

    conn = room_server["ctx"].conn
    _nine(room_server)
    assert _open(room_server["deploy"], 11).status_code == 200
    wave = _wave(room_server)
    round_row = conn.execute("SELECT * FROM room_rounds WHERE state = 'active'").fetchone()
    interrupt._b42_ask(conn, round_row, CLONER1, 3)
    asked = _decisions(conn, line.RULE_LINE, line.ANSWER)
    assert len(asked) == 1 and asked[0]["verdict"] == line.REWRITE and asked[0]["entity_key"] == CLONER1
    assert asked[0]["outcome"] is None
    conn.execute("INSERT INTO round_interrupts (round_id, mac, wave_id, raised_at) VALUES (?, ?, ?, ?)",
                 (round_row["id"], CLONER1, wave, "2026-11-15T00:00:00+00:00"))
    conn.commit()
    assert interrupt.answer(conn, round_row, "close", "labtech", "console", [CLONER1]) == 1
    asked = _decisions(conn, line.RULE_LINE, line.ANSWER)
    assert "close" in asked[0]["outcome"] and "לא תואם" in asked[0]["outcome"]


def test_outside_a_line_the_question_stays_the_operators_alone(room_server):
    from server.rounds import interrupt                                  # noqa: PLC0415

    conn = room_server["ctx"].conn
    _nine(room_server)
    assert _open(room_server["deploy"], 9).status_code == 200
    _wave(room_server)
    round_row = conn.execute("SELECT * FROM room_rounds WHERE state = 'active'").fetchone()
    interrupt._b42_ask(conn, round_row, CLONER1, 3)
    assert _decisions(conn, line.RULE_LINE, line.ANSWER) == []


# --- B33: מגירה שצריכה השלמה בכל גל -------------------------------------------------------------------------


def _o(serial, needed=True, others=True, wave="w"):
    return slotfill.Obs(wave, serial, needed, others)


def test_a_drawer_short_three_waves_with_different_disks_while_a_neighbour_kept_up_is_suspect():
    assert slotfill.judge([_o("D3"), _o("D2"), _o("D1")])[0] is True
    assert slotfill.judge([_o("D1"), _o("D1"), _o("D1")])[0] is False, "אותו דיסק = הדיסק"
    assert slotfill.judge([_o("D3"), _o("D2", others=False), _o("D1")])[0] is False, "כולם קצרים = הזרם"
    assert slotfill.judge([_o("D3"), _o("D2", needed=False), _o("D1")])[0] is False
    assert slotfill.judge([_o("D2"), _o("D1")])[0] is False, "שני גלים אינם מספיקים"


def _chunk_wave(conn, session_id, mac, took: dict, offered=1000, port_of=None):
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream) VALUES (?, '{}', ?, ?)",
                 (session_id, f"2026-11-15T0{len(session_id) % 10}:00:00+00:00",
                  json.dumps({"chunk": {"stream_zbytes": offered, "drives": {mac: took}}})))
    for serial, port in (port_of or {}).items():
        conn.execute("INSERT INTO port_history (session_id, mac, port, serial, at) VALUES (?, ?, ?, ?, ?)",
                     (session_id, mac, port, serial, f"2026-11-15T{10 + int(session_id[-1]):02d}:00:00+00:00"))
    conn.commit()


def test_the_wave_end_records_the_suspect_drawer_from_what_the_drives_took(room_server):
    conn = room_server["ctx"].conn
    for i, serial in enumerate(("D1", "D2", "D3"), start=1):
        _chunk_wave(conn, f"ses_{i}", CLONER1, {serial: 100, f"K{i}": 1000}, port_of={serial: 2, f"K{i}": 1})
    assert slotfill.after_wave(conn, "ses_3", "room_x") == 1
    rows = _decisions(conn, slotfill.RULE, slotfill.ACTION)
    assert len(rows) == 1 and rows[0]["entity_kind"] == "slot" and rows[0]["entity_key"] == f"{CLONER1}/2"
    assert rows[0]["verdict"] == slotfill.SUSPECT


def test_a_wave_whose_first_pass_was_not_measured_is_not_an_observation(room_server):
    conn = room_server["ctx"].conn
    for i, serial in enumerate(("D1", "D2"), start=1):
        _chunk_wave(conn, f"ses_{i}", CLONER1, {serial: 100, f"K{i}": 1000}, port_of={serial: 2, f"K{i}": 1})
    _chunk_wave(conn, "ses_3", CLONER1, {"K3": 1000}, port_of={"D3": 2, "K3": 1})   # D3 אינו במעבר הראשון
    assert slotfill.after_wave(conn, "ses_3", "room_x") == 0
    assert _decisions(conn, slotfill.RULE, slotfill.ACTION) == []
