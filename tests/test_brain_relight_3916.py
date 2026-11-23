"""#3916 (נדב 20/11, #3897 הכרעה 2): בפס ייצור משכפל שנכבה ועוד יש לו סבב מודלק מחדש (WoL). המוח מחליט, השרת מבצע.

"יש לו עוד סבב" = הוא ב-``keep`` של B24 (`brain.line.power`). הסיבה שזה כלל חדש ולא K-REWAKE: `room._unseen` מעיר רק משכפל שנשארה לו מגירה
טרייה, ומשכפל בפס ייצור שסיים את כל המגירות שלו (והדיסקים הבאים טרם הוכנסו, כי הוא כבוי) אינו כזה. ב-``shadow`` (ברירת המחדל) ההחלטה
נרשמת בלבד, וב-``enforce`` יוצא WoL. משכפל שאינו ב-``keep`` לעולם אינו מודלק.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import line, memory  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_brain_line_3064 import _c  # noqa: E402
from test_prodline_3181 import _nine, _open, _wave  # noqa: E402
from test_room_rewake_1721 import _packets_to  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, report, room, room_server  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _fresh():
    line.forget()
    yield
    line.forget()


def _mode(conn, mode: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('brain_veto_mode', ?)", (mode,))
    conn.commit()


def _wave_two_open_both_off(rs) -> None:
    """11 דיסקים על 9 מגירות. גל 1 נכתב כולו; נשארו 2, ולכן B24 מחליט ש-CLONER2 (מספר 03) נשאר, ו-CLONER1 (07) לא.
    גל 2 פתוח, ושני המשכפלים כבויים (ה-hello האחרון שלהם מלפני 20 דקות)."""
    conn = rs["ctx"].conn
    _nine(rs)
    conn.execute("UPDATE machines SET suffix = '07' WHERE mac = ?", (CLONER1,))
    conn.execute("UPDATE machines SET suffix = '03' WHERE mac = ?", (CLONER2,))
    conn.commit()
    assert _open(rs["deploy"], 11).status_code == 200
    wave = _wave(rs)
    assert rs["deploy"].post("/api/console/room/start").status_code == 200
    report(rs["anon"], wave, CLONER1, {f"sd{c}": "done" for c in "abcde"})
    report(rs["anon"], wave, CLONER2, {f"sd{c}": "done" for c in "abcd"})
    room_mod.sweep(conn, rs["ctx"].store)                                 # הדופק שמסיים את הגל, בלי hello שמצרף מישהו לגל 2
    assert room(rs["deploy"])["round"]["wave_number"] == 2
    assert room(rs["deploy"])["round"]["wave_state"] == "open"
    conn.execute("UPDATE net_devices SET last_seen = '2000-01-01T00:00:00+00:00'")
    conn.commit()


def _relights(conn, wave: str | None = None) -> list[dict]:
    """שורות LINE_RELIGHT. ``wave`` = רק גל אחד. גל 1 נפתח בשנייה שבה ה-hello של המשכפלים נכתב, ובדופק שנופל אחרי גבול
    השנייה `_not_back` (`seen < opened`, ברזולוציית שנייה) מחזיר True גם למשכפל שער — ולכן גל 1 מתעד שורת הדלקה
    אקראית (נמדד על tr2: כ-1 מ-4). מה שהמבחנים האלה בודקים הוא גל 2 בלבד."""
    return [d for d in memory.decisions(conn, rule=line.RULE_LINE, limit=64)
            if d["action"] == "LINE_RELIGHT" and (wave is None or d.get("point") == wave)]


def _wave_id(rs) -> str:
    return room(rs["deploy"])["round"]["wave_session_id"]


def _sweep(rs) -> None:
    room_mod.sweep(rs["ctx"].conn, rs["ctx"].store)


def test_in_enforce_the_kept_cloner_that_went_off_is_woken_and_the_one_that_is_not_kept_is_not(room_server):
    conn, woken = room_server["ctx"].conn, room_server["woken"]
    _mode(conn, "enforce")
    _wave_two_open_both_off(room_server)
    before = _packets_to(woken, CLONER2), _packets_to(woken, CLONER1)
    _sweep(room_server)
    assert _packets_to(woken, CLONER2) - before[0] == 1, "משכפל 03 ב-keep ונכבה: WoL"
    assert _packets_to(woken, CLONER1) == before[1], "משכפל 07 לא ב-keep: אין לו עוד סבב"
    rows = _relights(conn, _wave_id(room_server))
    assert [(r["verdict"], r["entity_key"], r["mode"]) for r in rows] == [("WOL", CLONER2, "enforce")], rows
    assert "remaining=2" in rows[0]["evidence"] and any(e.startswith("keep=") for e in rows[0]["evidence"]), rows[0]["evidence"]
    _sweep(room_server)
    assert _packets_to(woken, CLONER2) - before[0] == 1, "אותו גל, פחות מ-60 ש': לא שוב"


def test_in_shadow_it_is_recorded_once_and_no_wol_goes_out(room_server):
    conn, woken = room_server["ctx"].conn, room_server["woken"]
    _wave_two_open_both_off(room_server)
    before = _packets_to(woken, CLONER2), _packets_to(woken, CLONER1)
    for _ in range(3):
        _sweep(room_server)
    assert (_packets_to(woken, CLONER2), _packets_to(woken, CLONER1)) == before, "צל: שום WoL"
    rows = _relights(conn, _wave_id(room_server))
    assert [(r["entity_key"], r["mode"]) for r in rows] == [(CLONER2, "shadow")], "נרשם פעם אחת למשכפל בגל"
    assert "היה שולח" in rows[0]["reason"] and "לא נשלח" in rows[0]["outcome"]


def test_off_records_nothing_and_sends_nothing(room_server):
    conn, woken = room_server["ctx"].conn, room_server["woken"]
    _mode(conn, "off")
    _wave_two_open_both_off(room_server)
    before = _packets_to(woken, CLONER2)
    _sweep(room_server)
    assert _relights(conn) == [] and _packets_to(woken, CLONER2) == before


# --- הכלל הטהור ---------------------------------------------------------------------------------------------------


def _relight(conn, cloners, off, remaining=2, wave="w1", now=None):
    return line.relight(conn, round_id="r1", wave_id=wave, remaining=remaining, cloners=cloners, off=set(off), now=now)


def test_held_undecided_nothing_left_and_awake_are_not_woken(room_server):
    conn = room_server["ctx"].conn
    _mode(conn, "enforce")
    one, two = _c(1), _c(2)
    assert _relight(conn, [_c(1, held=True), two], [one.mac, two.mac]) == [two.mac], "מוחזק אינו מועמד"
    assert _relight(conn, [_c(1, drawers=None), two], [one.mac, two.mac], wave="w2") == [], "מגירות לא נקראו: לא הוכרע"
    assert _relight(conn, [one, two], [one.mac, two.mac], remaining=0, wave="w3") == [], "לא נשאר מה לכתוב"
    assert _relight(conn, [one, two], [two.mac], wave="w4") == [], "ב-keep אבל דולק"
    assert _relight(conn, [one, two], [one.mac, two.mac], wave="w5") == [one.mac], "הנמוך במספר ב-keep"


def test_enforce_stops_at_the_cap_and_spaces_the_notes(room_server):
    conn = room_server["ctx"].conn
    _mode(conn, "enforce")
    # שעון קבוע, לא time.monotonic(): `now + i*60` על שעון אמיתי מעוגל כשהוא חוצה חזקת-2, ואז `(now+60i) - (now+60(i-1))`
    # יוצא 59.99999999 ולא 60, וההפרש הזה נופל תחת סף ה-RELIGHT_S של `line.relight`. מה שהמבחן בודק הוא רווח בשניות, לא זמן המכונה.
    one, now = _c(1), 1000.0
    sent = []
    for i in range(line.RELIGHT_MAX + 3):
        sent += _relight(conn, [one], [one.mac], remaining=1, now=now + i * line.RELIGHT_S)
        assert _relight(conn, [one], [one.mac], remaining=1, now=now + i * line.RELIGHT_S + 1) == [], "פחות מ-60 ש' מההערה"
    assert len(sent) == line.RELIGHT_MAX
