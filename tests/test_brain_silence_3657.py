"""#3657 (אפיק #3653): חיות הגל בשרת -- המוח מחליט (`server/brain/silence.py`), והשרת מבצע.

מה נבדק, לכל נקודה (S22 `_mark_lost` = K-LOST, S23 `_stream_stalled` = K-STALL):

- **ההתנהגות זהה לזו של לפני #3657.** `OLD_LOST` למטה הוא העותק המילולי של ההחלטה כפי שישבה ב-`room._mark_lost` בבסיס
  (`lab/vm-1114f` r33, `02d2092`), בתור אורקל בטסט בלבד: רשת של חותמות וספים, והתוצאה חייבת להיות זהה.
- **ההחלטה נרשמת** ב-`brain_decisions` עם הראיות, ובמצב `shadow` (ברירת המחדל), `enforce`; ב-`off` היא מתקבלת ואינה נרשמת.
- **התוצאה נרשמת אחרי הביצוע**: סומן, או דיווח הגיע בינתיים.
- **אין החלטה = לא עושים כלום** (עיקרון 5): כלל שנפל אינו מסמן אבוד, ואינו "תקין": שורת UNDECIDED אחת לגל.
- **מחיקת היומן לא עברה**: היא עדיין מאחורי הווטו. `test_brain_veto_3064.py` מכסה את זה, והוא לא השתנה ורץ בשני המצבים.

**בקרה שלילית:** הבסיס חסר את `server/brain/silence.py`. `silence = None` שם (לא ImportError של הקובץ): הטסטים שעוברים דרך
`room._mark_lost` ו-`room._stream_stalled` נופלים ב-assert על `brain_decisions` ריק, והטסטים של המודול עצמו נופלים ב-assert
שהמודול חסר.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.agent import reports
from server.brain import memory, veto
from server.rounds import room

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import silence
except ImportError:                     # pragma: no cover
    silence = None

W = "W-1"
R = "R-1"
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    if silence is not None:
        silence.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()
    if silence is not None:
        silence.forget()


def ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def member(conn, mac: str, age: float, state: str = "writing") -> None:
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, ?, ?, ?)", (W, mac, state, ago(age),
                                             json.dumps([{"dev": "sda", "serial": f"{mac}-1", "state": state}])))
    conn.commit()


class Store:
    def __init__(self, conn):
        self.conn = conn

    def members(self, wave_id):
        return self.conn.execute("SELECT * FROM session_members WHERE session_id = ?", (wave_id,)).fetchall()


def mark_lost(conn, started_age: float = 3600):
    wave = {"id": W, "started_at": ago(started_age)}
    return room._mark_lost(conn, Store(conn), wave, Store(conn).members(W))


def state_of(conn, mac: str) -> str:
    return conn.execute("SELECT state FROM session_members WHERE session_id = ? AND mac = ?", (W, mac)).fetchone()[0]


def rows(conn, rule: str) -> list[dict]:
    return list(reversed(memory.decisions(conn, rule=rule, limit=64)))


def one(found):
    """החלטה אחת בדיוק, ואם לא: כשל התנהגותי שאומר מה נמצא (לא ValueError של פירוק רשימה)."""
    assert len(found) == 1, f"expected exactly one brain decision, got {len(found)}: {[(r['rule'], r['verdict']) for r in found]}"
    return found[0]


# --- ההתנהגות זהה: העותק המילולי של ההחלטה שהוסרה מ-room._mark_lost ----------------------------------------------------


def _iso_or_none(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def OLD_LOST(members, started_at, now, lost_limit, jump, final):
    """אורקל: ההחלטה של `room._mark_lost` בבסיס (לפני #3657), מילה במילה, מחזירה (lost, future) כרשימות mac."""
    ahead = now + timedelta(seconds=jump)
    floor = _iso_or_none(started_at)
    lost, future = [], []
    for m in members:
        if m["state"] in final:
            continue
        refs = [t for t in (_iso_or_none(m["updated_at"]), floor) if t is not None]
        if not refs:
            continue
        if max(refs) > ahead:
            future.append(m["mac"])
        elif (now - max(refs)).total_seconds() > lost_limit:
            lost.append(m["mac"])
    return lost, future


def _at(seconds_ago):
    return None if seconds_ago is None else (NOW - timedelta(seconds=seconds_ago)).isoformat()


def test_the_brain_decides_exactly_what_the_server_code_decided_before():
    assert silence is not None, "server/brain/silence.py חסר"
    states = ("writing", "waiting", "verifying", "done", "failed", "lost")
    ages = (None, 0, 100, 179, 180, 181, 400, -30, -60, -61, -4000)
    starts = (None, 0, 50, 179, 181, 4000, -61, -4000)
    checked = 0
    for limit in (180, 600):
        for state in states:
            for age in ages:
                for start in starts:
                    mem = [{"mac": "aa", "state": state, "updated_at": _at(age)}]
                    want = OLD_LOST(mem, _at(start), NOW, limit, 60, reports.FINAL)
                    calls = silence.judge_lost(mem, started_at=_at(start), now=NOW, lost_s=limit, jump_s=60,
                                               final=reports.FINAL)
                    got = ([c.target for c in calls if c.verdict == silence.LOST],
                           [c.target for c in calls if c.verdict == silence.FUTURE])
                    assert got == want, (limit, state, age, start, got, want)
                    checked += 1
    assert checked == 2 * 6 * 11 * 8


def test_the_limit_is_strict_and_a_young_wave_is_a_grace():
    assert silence is not None
    mem = [{"mac": "aa", "state": "writing", "updated_at": _at(500)}]
    for age, verdict in ((179, None), (180, None), (181, silence.LOST)):
        got = silence.judge_lost([{"mac": "aa", "state": "writing", "updated_at": _at(age)}],
                                 started_at=_at(4000), now=NOW, lost_s=180, jump_s=60, final=reports.FINAL)
        assert [c.verdict for c in got] == ([verdict] if verdict else []), age
    # גל שרק התחיל נותן לחבר את הסף כולו, גם אם הדיווח האחרון שלו ישן
    assert silence.judge_lost(mem, started_at=_at(10), now=NOW, lost_s=180, jump_s=60, final=reports.FINAL) == []


def test_no_stamp_at_all_is_not_measured_and_never_lost():
    assert silence is not None
    got = silence.judge_lost([{"mac": "aa", "state": "writing", "updated_at": None}], started_at=None, now=NOW,
                             lost_s=180, jump_s=60, final=reports.FINAL)
    assert [c.verdict for c in got] == [silence.UNMEASURED]


# --- S22 דרך room._mark_lost: מסמן, רושם, ושומר על ההתנהגות ---------------------------------------------------------------


def test_a_silent_member_is_marked_lost_and_the_decision_is_recorded_with_its_evidence(conn):
    member(conn, "aa", 400)
    member(conn, "bb", 5)
    mark_lost(conn)
    assert (state_of(conn, "aa"), state_of(conn, "bb")) == ("lost", "writing")
    row = one(rows(conn, "K-LOST"))
    assert (row["verdict"], row["mode"], row["entity_key"], row["round_id"], row["point"]) == \
        ("LOST", "shadow", "aa", W, "S22")
    assert row["outcome"] == "השרת סימן 'אבד'" and row["outcome_state"] == "closed"
    assert any(e.startswith("limit=180s") for e in row["evidence"]) and "state=writing" in row["evidence"]
    assert row["reason"].startswith("לא דווחה התקדמות 4")


def test_the_limit_is_still_the_operators_setting(conn):
    """`lost_wait_seconds` (הגדרה, מותר רק להגדיל) הוא הקלט של הכלל: 250 ש' אינן אובדן כשהסף 300."""
    member(conn, "aa", 250)
    db.set_setting(conn, "lost_wait_seconds", "300")
    mark_lost(conn)
    assert state_of(conn, "aa") == "writing" and rows(conn, "K-LOST") == []
    db.set_setting(conn, "lost_wait_seconds", "180")
    mark_lost(conn)
    assert state_of(conn, "aa") == "lost" and len(rows(conn, "K-LOST")) == 1


@pytest.mark.parametrize("mode, recorded", [("shadow", True), ("enforce", True), ("off", False)])
def test_every_mode_marks_the_same_and_off_only_stops_the_record(conn, mode, recorded):
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, mode)
    mark_lost(conn)
    assert state_of(conn, "aa") == "lost"
    got = rows(conn, "K-LOST")
    assert [r["mode"] for r in got] == ([mode] if recorded else [])


def test_a_report_that_arrives_between_the_decision_and_the_mark_wins_and_the_outcome_says_so(conn, monkeypatch):
    member(conn, "aa", 400)
    monkeypatch.setattr(room, "update_one", lambda *a, **k: 0)         # הדיווח שינה את המצב: התביעה אינה תואמת
    mark_lost(conn)
    row = one(rows(conn, "K-LOST"))
    assert row["verdict"] == "LOST" and "לא סומן" in row["outcome"], row


def test_a_stamp_in_the_future_is_a_clock_step_calibrated_and_recorded_not_lost(conn):
    member(conn, "aa", -3 * 3600)
    got = mark_lost(conn)
    assert state_of(conn, "aa") == "writing" and got is not None
    row = one(rows(conn, "K-LOST"))
    assert row["verdict"] == "FUTURE" and "כוילו" in row["outcome"]


def test_a_brain_that_fails_decides_nothing_the_server_marks_nobody_and_it_says_so_once(conn, monkeypatch):
    """עיקרון 5: "לא הצלחנו להחליט" אינו "אבד" ואינו "תקין". אין סימון, ושורה אחת לגל, לא אחת לכל דופק."""
    assert silence is not None, "server/brain/silence.py חסר"
    member(conn, "aa", 400)
    monkeypatch.setattr(silence, "judge_lost", lambda *a, **k: 1 / 0)
    before = Store(conn).members(W)
    for _ in range(3):
        got = mark_lost(conn)
        assert [m["state"] for m in got] == [m["state"] for m in before]
    assert state_of(conn, "aa") == "writing"
    row = one(rows(conn, "K-LOST"))
    assert row["verdict"] == "UNDECIDED" and "ZeroDivisionError" in row["reason"]
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'client_lost'").fetchone()[0] == 0


# --- S23 דרך room._stream_stalled ------------------------------------------------------------------------------------


def _drawer(stalled_s, state="writing"):
    return {"state": state, "stalled_s": stalled_s}


def _machines(*stalled):
    return [{"drawer_list": [_drawer(s) for s in stalled]}]


@pytest.mark.parametrize("machines, want", [
    (_machines(200), False),                     # מגירה כותבת אחת: זו המגירה, אין ראיה לזרם
    (_machines(200, None), False),               # מגירה שלא נמדדה אינה משתתפת
    (_machines(179, 400), False),
    (_machines(180, 180), True),                 # בגבול: מעל או שווה
    (_machines(400, 999, 181), True),
    ([], False),
])
def test_the_stream_label_is_what_it_was(machines, want):
    assert silence is not None, "server/brain/silence.py חסר"
    assert room._stream_stalled(machines) is want


def test_entering_a_stall_is_recorded_once_and_closed_when_it_ends(conn):
    assert silence is not None, "server/brain/silence.py חסר"
    stalled, moving = _machines(400, 400), _machines(5, 400)
    for _ in range(4):                           # המסך נקרא בלי הרף: שורה אחת, לא ארבע
        assert room._stream_stalled(stalled, conn, R) is True
    row = one(rows(conn, "K-STALL"))
    assert (row["verdict"], row["entity_kind"], row["entity_key"], row["point"], row["mode"]) == \
        ("STREAM_STALLED", "round", R, "S23", "shadow")
    assert row["outcome_state"] == "open" and "limit=180s" in row["evidence"]
    assert room._stream_stalled(moving, conn, R) is False
    row = one(rows(conn, "K-STALL"))
    assert row["outcome"] == "הזרם חזר לזוז"
    room._stream_stalled(stalled, conn, R)       # עצירה חדשה = החלטה חדשה
    assert len(rows(conn, "K-STALL")) == 2


def test_a_label_without_a_round_or_with_the_brain_off_records_nothing(conn):
    assert silence is not None, "server/brain/silence.py חסר"
    assert room._stream_stalled(_machines(400, 400), conn, None) is True
    db.set_setting(conn, veto.SETTING, "off")
    assert room._stream_stalled(_machines(400, 400), conn, R) is True
    assert rows(conn, "K-STALL") == []


def test_a_stall_rule_that_fails_shows_no_label_and_says_so_once(conn, monkeypatch):
    assert silence is not None, "server/brain/silence.py חסר"
    monkeypatch.setattr(silence, "judge_stall", lambda *a, **k: 1 / 0)
    assert [room._stream_stalled(_machines(400, 400), conn, R) for _ in range(3)] == [False] * 3
    row = one(rows(conn, "K-STALL"))
    assert row["verdict"] == "UNDECIDED"


def test_a_drawer_stamp_from_the_future_reads_as_stalled_for_exactly_the_threshold():
    """`_stalled_s` (התצוגה של "כמה זמן המגירה לא זזה"): חותמת עתידית היא קפיצת שעון, ונקראת "עצורה בדיוק לפי הסף", לא קפואה ולא 0.
    הסף עצמו הוא של המוח (`silence.STALL_S`); בבסיס זה 180 ב-`room.STALL_SECONDS`, ולכן המספר כתוב כאן."""
    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
    assert room._stalled_s(future) == 180
    assert room._stalled_s(None) is None and room._stalled_s("garbage") is None


# --- הקטלוג והתצוגה -----------------------------------------------------------------------------------------------------


def test_the_console_line_does_not_claim_the_decision_was_only_recorded(conn):
    """הצל כאן אינו "רק נרשם": השרת מבצע את ההחלטה. השורה בעברית אומרת את זה."""
    member(conn, "aa", 400)
    mark_lost(conn)
    row = one(memory.decisions(conn, rule="K-LOST"))
    assert "רק נרשם" not in row["line_he"] and "השרת ביצע" in row["line_he"], row["line_he"]
    assert "סימון משכפל כאבוד: אבד" in row["line_he"]
