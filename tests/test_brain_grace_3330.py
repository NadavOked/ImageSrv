"""#3330 (KT5, I15): חלון חסד אחרי עליית השרת -- `server/brain/grace.py`, `Snapshot.up_at`, ו-`decide._judge`.

שרת שעלה עכשיו לא ראה אף משכפל: "אין ראיה" אצלו הוא "לא הסתכלתי", לא "המשכפל שתק" (עיקרון 5). ב-300 הש' הראשונות
אחרי העלייה K2 מחזיר HOLD לכל פעולה הרסנית שנשענת על שתיקה. הטסטים כאן:

- ברמת הכלל: בתוך החלון HOLD, בגבול ואחריו ההחלטה של היום; כשל עם ראיה משלו עובר; הדיסק שאומת נשאר מוחזק;
  התקרה גוברת (כל החזקה נגמרת); הסף אינו בהגדרות.
- ברמת הווטו: ב-enforce הפעולה אינה מתבצעת בתוך החלון ומתבצעת אחריו; ב-shadow רק נרשם "היה חוסם".
- חיווט: `create_app` מחבר את רגע העלייה; חיבור שאינו נושא תכונות = "לא נמדד", בלי חלון ובלי זריקה.
- הקלטה: הצילום עם `up_at` נקרא בחזרה עם אותו hash, וצילום ישן (בלי `up_at`) עדיין נקרא.

**בקרה שלילית:** הסרת הבדיקה מ-`decide._judge` מפילה את טסטי הכלל והווטו; הסרת `grace.attach` מ-`app.py` או
`up_at=` מ-`veto.check` מפילה את טסטי החיווט ואת טסט ה-enforce.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import decide as brain, grace, recording, veto
from server.brain.observe import Proposal, build

NOW = 1_800_000_000.0
CFG = brain.Config()
W = "W-1"


def iso(age: float) -> str:
    return datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(timespec="seconds")


def snap(*, up_age: float | None, silent_for: float = 10_000, held_s: float = 0.0, own: str | None = None,
         drawers=(), state="writing"):
    return build(now=NOW, round_id="S", members=[{"mac": "a", "state": "writing", "updated_at": iso(silent_for),
                                                  "targets_json": json.dumps([{"dev": "sda", "serial": "a-1",
                                                                              "state": state}])}],
                 proposals=(Proposal("FAIL_WAVE", "D16", ("a",), "t", tuple(drawers), own, held_s),), part=4,
                 up_at=None if up_age is None else NOW - up_age)


def k2(s, cfg=CFG):
    return [d for d in brain.decide(s, cfg) if d.rule == "K2"][0]


# --- הכלל -------------------------------------------------------------------------------------------------------


def test_the_default_is_kt5_300_seconds_in_the_code_and_not_a_setting():
    assert grace.GRACE_S == 300.0 == brain.Config().grace_s
    assert not any("grace" in key for key in veto.KEYS)


@pytest.mark.parametrize("up_age", [0, 1, 150, 299.9])
def test_inside_the_window_silence_is_not_a_reason_to_fail(up_age):
    got = k2(snap(up_age=up_age))
    assert got.verdict == brain.HOLD and "KT5" in got.reason and got.evidence, got


@pytest.mark.parametrize("up_age", [300, 301, 5000])
def test_at_the_edge_and_after_it_the_decision_is_todays(up_age):
    got = k2(snap(up_age=up_age))
    assert got.verdict == brain.PROCEED and "KT5" not in got.reason, got


def test_a_server_that_did_not_measure_its_start_claims_no_window():
    """"לא נמדד" אינו "עלה עכשיו": בלי רגע עלייה ההחלטה היא של היום (טסטים וחיבור גולמי)."""
    assert k2(snap(up_age=None)).verdict == brain.PROCEED


def test_a_start_time_in_the_future_is_inside_the_window_and_the_ceiling_still_ends_it():
    """שעון שזז אחורה: גיל שלילי אינו מוכיח שהחלון נגמר, אבל כל החזקה נגמרת בתקרה."""
    assert k2(snap(up_age=-50)).verdict == brain.HOLD
    assert k2(snap(up_age=-50, held_s=CFG.ceiling_s)).verdict == brain.PROCEED


def test_a_failure_with_evidence_of_its_own_goes_through_inside_the_window():
    got = k2(snap(up_age=5, own="readback failed"))
    assert got.verdict == brain.PROCEED and "readback" in got.reason


def test_a_verified_disk_stays_held_inside_the_window_too():
    got = k2(snap(up_age=5, drawers=[("a", "a-1")], state="done"))
    assert got.verdict == brain.HOLD and "אומת" in got.reason


def test_the_ceiling_wins_over_the_window():
    """כל החזקה נגמרת: חלון שנקבע ארוך מהתקרה אינו מחזיק לנצח."""
    cfg = brain.Config(ceiling_s=60, grace_s=300)
    assert k2(snap(up_age=5, held_s=59), cfg).verdict == brain.HOLD
    assert k2(snap(up_age=5, held_s=60), cfg).verdict == brain.PROCEED


def test_the_window_depends_only_on_the_server_start_not_on_the_other_cloners():
    """I3: משכפל נוסף בצילום אינו משנה את ההחלטה על `a`."""
    one = snap(up_age=5)
    two = build(now=NOW, round_id="S", members=[
        {"mac": "a", "state": "writing", "updated_at": iso(10_000), "targets_json": None},
        {"mac": "b", "state": "writing", "updated_at": iso(1), "targets_json": None}],
        proposals=(Proposal("FAIL_WAVE", "D16", ("a",), "t"),), part=4, up_at=NOW - 5)
    assert (k2(one).verdict, k2(one).reason) == (k2(two).verdict, k2(two).reason)


# --- הצילום וההקלטה ---------------------------------------------------------------------------------------------


def test_the_start_time_is_part_of_the_snapshot_hash_only_when_it_was_measured():
    assert len({snap(up_age=5).digest(), snap(up_age=500).digest(), snap(up_age=None).digest()}) == 3
    plain = asdict(snap(up_age=None))
    assert "up_at" in plain and recording.snapshot(plain).digest() == snap(up_age=None).digest()
    old = {k: v for k, v in plain.items() if k != "up_at"}                     # הקלטה מלפני #3330
    assert recording.snapshot(old).digest() == snap(up_age=None).digest()


def test_a_recorded_snapshot_keeps_its_start_time_and_replays_to_the_same_decision():
    s = snap(up_age=5)
    back = recording.snapshot(asdict(s))
    assert back.up_at == NOW - 5 and back.digest() == s.digest()
    assert brain.decide(back, CFG) == brain.decide(s, CFG)


# --- הווטו --------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()


def silent_member(conn, mac="aa"):
    old = (datetime.now(timezone.utc) - timedelta(seconds=10_000)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, 'writing', ?, NULL)", (W, mac, old))
    conn.commit()


def ask(conn, now):
    return veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="test", now=now)


def test_enforce_holds_inside_the_window_and_lets_go_after_it(conn):
    silent_member(conn)
    db.set_setting(conn, veto.SETTING, "enforce")
    up = time.time() - 10
    grace.attach(conn, up)
    held = ask(conn, up + 10)
    assert held.proceed is False and held.would_block is True and "KT5" in held.reason
    veto.forget()
    assert ask(conn, up + grace.GRACE_S + 1).proceed is True


def test_shadow_says_it_would_block_inside_the_window_and_blocks_nothing(conn):
    silent_member(conn)
    up = time.time() - 10
    grace.attach(conn, up)
    got = ask(conn, up + 10)
    assert got.proceed is True and got.would_block is True and got.mode == "shadow"


def test_without_the_start_time_the_veto_decides_as_before(conn):
    silent_member(conn)
    db.set_setting(conn, veto.SETTING, "enforce")
    assert ask(conn, time.time()).proceed is True


# --- החיווט -------------------------------------------------------------------------------------------------------


def test_create_app_attaches_the_server_start_time(server):
    up = grace.up_at(server["ctx"].conn)
    assert up is not None and 0 <= time.time() - up < 600


def test_a_connection_that_cannot_carry_the_start_time_is_not_measured_and_does_not_raise(tmp_path):
    raw = sqlite3.connect(tmp_path / "raw.db")
    grace.attach(raw, 1.0)
    assert grace.up_at(raw) is None


@pytest.mark.parametrize("junk", [None, True, "5", [1]])
def test_a_start_time_that_is_not_a_number_is_not_measured(junk):
    class Conn:
        brain_up_at = junk

    assert grace.up_at(Conn()) is None
