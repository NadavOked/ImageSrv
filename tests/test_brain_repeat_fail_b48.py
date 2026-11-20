"""#3064 (B61): דיסק שנכשל באותה סיבה כמה גלים ברצף -- המלצה בצל, בלי לשנות דבר.

**בקרה שלילית:** עם `server/brain/cause.py` ו-`server/brain/decide.py` בבסיס ו-`repeatfail.py` מוסר, אין `fail_repeats` ואין שורת
B61; הטסטים המסומנים (neg) נופלים על התנהגות. הטסטים שמייבאים את `repeatfail` נופלים שם על ה-import, וזה אינו בקרה.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import cause as K, decide, memory, veto

from test_disk_failures import CABLE_LINES, DISK_LINES

MAC = "78:ac:c0:9b:11:c2"
SERIAL = "SICSD40302WL"
SKIP = {"skipped": 11390, "kernel": None}


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "b48.db")
    yield c
    veto.forget()


def _old(conn, wave: str, at: str, verdict: str = "UNCLASSIFIED", serial: str = SERIAL):
    """שורת K9 של גל קודם, בשעה נתונה (השורה של הגל הנוכחי נרשמת דרך `judge`)."""
    memory.record(conn, mode="shadow", rule="K9", point="D11", action="FAIL_CAUSE", verdict=verdict, reason="x",
                  round_id=wave, entity=("disk", serial), at=at)


def _took_part(conn, wave: str, at: str, failed: int | None = 0):
    conn.execute("INSERT INTO port_history (session_id, mac, port, serial, cable_fail, at, failed)"
                 " VALUES (?,?,?,?,?,?,?)", (wave, MAC, 3, SERIAL, 0, at, failed))
    conn.commit()


def _judge(conn, wave: str, **ev):
    return K.judge(conn, point="D11", round_id=wave, mac=MAC, serial=SERIAL, existing="נכשל", **(ev or SKIP))


def _b48(conn):
    return list(reversed(memory.decisions(conn, kind="disk", key=SERIAL, rule="B61", limit=50)))


def test_the_start_value_is_in_the_code_and_not_a_setting():
    assert decide.Config().fail_repeats == 3
    assert "fail_repeats" not in " ".join(veto.KEYS), "נדב 14/11 בערב: ספים אינם בהגדרות"


def test_two_failures_with_the_same_cause_say_nothing_and_the_third_recommends(conn):
    """(neg) שני גלים: שורת K9 בלבד. הגל השלישי: B61 עם המלצה."""
    _old(conn, "w1", "2026-11-15T10:00:00+00:00")
    _judge(conn, "w2")
    assert _b48(conn) == []
    _judge(conn, "w3")
    [row] = _b48(conn)
    assert row["verdict"] == "REPEAT_UNCLASSIFIED" and row["mode"] == "shadow", row
    assert row["round_id"] == "w3" and (row["entity_kind"], row["entity_key"]) == ("disk", SERIAL)
    assert "cause=UNCLASSIFIED" in row["evidence"] and "waves=3" in row["evidence"], row["evidence"]
    assert "בדוק" in row["reason"] and "צל" in row["outcome"], row


def test_the_advice_follows_the_cause(conn):
    for n in (1, 2):
        _old(conn, f"w{n}", f"2026-11-15T10:0{n}:00+00:00", "LINK")
    K.judge(conn, point="disk_failure", round_id="w3", mac=MAC, serial=SERIAL, existing="x", kernel=tuple(CABLE_LINES))
    [row] = _b48(conn)
    assert row["verdict"] == "REPEAT_LINK" and "כבל" in row["reason"], row


def test_a_different_cause_in_between_resets_the_run(conn):
    _old(conn, "w1", "2026-11-15T10:01:00+00:00")
    _old(conn, "w2", "2026-11-15T10:02:00+00:00", "DRIVE")
    _judge(conn, "w3")
    assert _b48(conn) == []


def test_a_wave_the_disk_took_part_in_and_did_not_fail_resets_the_run(conn):
    """(neg) הדיסק הצליח בגל האמצעי: שלושה כשלים שאינם ברצף."""
    _old(conn, "w1", "2026-11-15T10:01:00+00:00")
    _took_part(conn, "w2", "2026-11-15T10:02:00+00:00", failed=0)
    _old(conn, "w3", "2026-11-15T10:03:00+00:00")
    _judge(conn, "w4")
    assert _b48(conn) == []


def test_a_wave_whose_outcome_was_not_recorded_does_not_count_as_the_same_failure(conn):
    _old(conn, "w1", "2026-11-15T10:01:00+00:00")
    _took_part(conn, "w2", "2026-11-15T10:02:00+00:00", failed=None)
    _judge(conn, "w3")
    assert _b48(conn) == []


def test_the_wave_cause_is_the_most_specific_one_recorded_in_it(conn):
    """D11 אמר 'לא סווג' ו-disk_failure אמר DRIVE באותו גל: הגל הוא DRIVE, והרצף נשמר."""
    _old(conn, "w1", "2026-11-15T10:01:00+00:00", "DRIVE")
    _old(conn, "w2", "2026-11-15T10:02:00+00:00", "UNCLASSIFIED")
    _old(conn, "w2", "2026-11-15T10:02:30+00:00", "DRIVE")
    _judge(conn, "w3", kernel=tuple(DISK_LINES), skipped=9)
    [row] = _b48(conn)
    assert row["verdict"] == "REPEAT_DRIVE", row


def test_one_row_per_wave_and_cause_even_when_two_points_speak(conn):
    _old(conn, "w1", "2026-11-15T10:01:00+00:00")
    _old(conn, "w2", "2026-11-15T10:02:00+00:00")
    _judge(conn, "w3")
    _judge(conn, "w3")
    assert len(_b48(conn)) == 1


def test_a_later_wave_extends_the_run_and_records_again(conn):
    for n in (1, 2):
        _old(conn, f"w{n}", f"2026-11-15T10:0{n}:00+00:00")
    _judge(conn, "w3")
    _judge(conn, "w4")
    assert [r["round_id"] for r in _b48(conn)] == ["w3", "w4"]
    assert "waves=4" in _b48(conn)[-1]["evidence"]


def test_another_disk_does_not_share_the_run(conn):
    _old(conn, "w1", "2026-11-15T10:01:00+00:00", serial="OTHER")
    _old(conn, "w2", "2026-11-15T10:02:00+00:00", serial="OTHER")
    _judge(conn, "w3")
    assert _b48(conn) == []


def test_the_rule_never_changes_what_the_point_does_and_off_records_nothing(conn):
    for n in (1, 2):
        _old(conn, f"w{n}", f"2026-11-15T10:0{n}:00+00:00")
    v = _judge(conn, "w3")
    assert v.cause == "UNCLASSIFIED" and not v.against_disk
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, 'off')", (veto.SETTING,))
    conn.commit()
    before = len(_b48(conn))
    assert K.judge(conn, point="D11", round_id="w4", mac=MAC, serial=SERIAL, existing="x", skipped=1) is None
    assert len(_b48(conn)) == before


def test_a_rule_that_fails_never_throws_into_k9(conn, monkeypatch):
    from server.brain import repeatfail
    monkeypatch.setattr(repeatfail, "waves", lambda *a, **k: 1 / 0)
    assert _judge(conn, "w1").cause == "UNCLASSIFIED"
