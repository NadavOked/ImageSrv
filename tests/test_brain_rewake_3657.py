"""#3657 (אפיק #3653): WoL חוזר לגל פתוח (S24, K-REWAKE) -- המוח מחליט (`server/brain/silence.py`), `room._rewake` מבצע.

- **ההתנהגות זהה לזו של לפני #3657.** `OLD_REWAKE` הוא העותק המילולי של ההחלטה כפי שישבה ב-`room._rewake` בבסיס (`lab/vm-1114f`
  r33, `02d2092`), בתור אורקל בטסט בלבד: רשת של גילאים, ספירות ומכונות, והתוצאה (הערה מס' N / ממתין / נכשל / כיול) חייבת להיות זהה.
  הטסטים של #1721, #1771 ו-#3590 (`test_room_rewake_1721`, `test_held_cloner_no_wol_3590`) לא השתנו וממשיכים לבדוק את הביצוע.
- **כל החלטה שמשנה משהו נרשמת** ב-`brain_decisions` עם הראיות, לפני הביצוע, והתוצאה אחריו. "ממתין" אינו נרשם: הוא נשאל כל דופק.
- **אין החלטה = לא עושים כלום** (עיקרון 5): מוח שנפל אינו שולח WoL, אינו מכשיל את הסבב ואינו מכייל, ו"לא ידוע" נרשם פעם אחת.
- `off` מחליט ואינו רושם.

**בקרה שלילית:** בבסיס אין `server/brain/silence.py`; `silence = None` שם. הטסטים שעוברים דרך הסבב נופלים ב-assert על `brain_decisions`
ריק (או על WoL שנשלח כשהמוח נכשל), והטסטים של המודול נופלים ב-assert שהמודול חסר.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, veto
from server.rounds import room as room_mod

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import silence
except ImportError:                     # pragma: no cover
    silence = None

from test_room_rewake_1721 import CAP, STEP, _events, _packets_to, _round, _wave1_partial  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, room_server  # noqa: E402,F401

pytest.importorskip("fastapi")

def one(found):
    """החלטה אחת בדיוק, ואם לא: כשל התנהגותי שאומר מה נמצא (לא ValueError של פירוק רשימה)."""
    assert len(found) == 1, f"expected exactly one brain decision, got {len(found)}: {[(r['rule'], r['verdict']) for r in found]}"
    return found[0]


@pytest.fixture(autouse=True)
def _fresh_rule_registers():
    if silence is not None:
        silence.forget()
    yield
    if silence is not None:
        silence.forget()


#: הערכים של הקוד שהוסר (`room.REWAKE_*`), לאורקל: 60 x 6, ועוד 180 לאתחול
OLD_STEP, OLD_MAX, OLD_BOOT = 60, 6, 180


def OLD_REWAKE(sent, age, since, quiet, waiting):
    """אורקל: ההחלטה של `room._rewake` בבסיס, מילה במילה. מחזיר ("SEND", n) / ("WAIT",) / ("FAIL",) / ("CALIBRATE",).
    ``since`` = כמה שניות מאז ההערה האחרונה, או None (לא נשלחה אחת)."""
    if age < 0:
        last_at = since
        if last_at is None and not quiet:
            return ("CALIBRATE",)
        s = since if since is not None else OLD_STEP
        age = sent * OLD_STEP + max(0.0, s)
    if quiet and sent < OLD_MAX:
        if age >= (sent + 1) * OLD_STEP:
            return ("SEND", sent + 1)
        return ("WAIT",)
    if age >= OLD_MAX * OLD_STEP + OLD_BOOT:
        return ("FAIL",)
    return ("WAIT",)


def test_the_brain_decides_exactly_what_the_server_code_decided_before():
    assert silence is not None, "server/brain/silence.py חסר"
    ages = (-4000, -61, -1, 0, 59, 60, 61, 119, 120, 121, 299, 300, 359, 360, 361, 539, 540, 541, 3600)
    sinces = (None, 0, 30, 59, 60, 61, 3600)
    checked = 0
    for sent in range(0, 9):
        for age in ages:
            for since in sinces:
                for quiet in (0, 1, 2):
                    for waiting in (0, 1):
                        want = OLD_REWAKE(sent, age, since, quiet, waiting)
                        call = silence.judge_rewake(round_id="R", sent=sent, age_s=age, since_last=lambda: since,
                                                    quiet=quiet, waiting=waiting)
                        got = ("SEND", call.n) if call.verdict == silence.SEND else (call.verdict,)
                        assert got == want, (sent, age, since, quiet, waiting, got, want)
                        checked += 1
    assert checked == 9 * len(ages) * len(sinces) * 3 * 2


def test_the_start_values_are_the_ones_that_were_in_room():
    assert silence is not None, "server/brain/silence.py חסר"
    assert (silence.REWAKE_S, silence.REWAKE_MAX, silence.REWAKE_BOOT_S) == (OLD_STEP, OLD_MAX, OLD_BOOT)
    assert (STEP, CAP) == (OLD_STEP, OLD_MAX)


def _rows(ctx) -> list[dict]:
    return list(reversed(memory.decisions(ctx.conn, rule="K-REWAKE", limit=64)))


def _run_to_the_cap(room_server, clock, ticks=CAP + 10):
    ctx = room_server["ctx"]
    for _ in range(ticks):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)


def test_every_resend_and_the_failure_are_recorded_with_their_outcome_and_waiting_is_not(room_server, monkeypatch):
    ctx = room_server["ctx"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    _run_to_the_cap(room_server, clock)
    assert _round(ctx)["state"] == "failed"
    rows = _rows(ctx)
    assert [(r["verdict"], r["entity_kind"], r["point"], r["mode"]) for r in rows] == \
        [("SEND", "round", "S24", "shadow")] * CAP + [("FAIL", "round", "S24", "shadow")], \
        [(r["verdict"], r["outcome"]) for r in rows]
    assert [r["reason"].split(":")[0] for r in rows[:CAP]] == [f"הערה {n} מתוך {CAP}" for n in range(1, CAP + 1)]
    assert all(r["outcome"].startswith("נשלח WoL חוזר ל: ") and r["outcome_state"] == "closed" for r in rows[:CAP])
    assert rows[-1]["outcome"].startswith("הסבב נכשל בשם: ") and "shich-1" in rows[-1]["outcome"]
    assert f"sent={CAP}/{CAP}" in rows[-1]["evidence"] and "quiet=1" in rows[-1]["evidence"]
    assert len(_events(ctx, "room_rewake")) == CAP and len(_events(ctx, "room_wake_failed")) == 1


def test_the_journal_line_counts_n_of_max_as_it_always_did(room_server, monkeypatch):
    ctx = room_server["ctx"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    _run_to_the_cap(room_server, clock)
    lines = _events(ctx, "room_rewake")
    assert [line.split()[2] for line in lines] == [f"n={n}/{CAP}" for n in range(1, CAP + 1)], lines


@pytest.mark.parametrize("mode, recorded", [("shadow", True), ("enforce", True), ("off", False)])
def test_every_mode_sends_the_same_and_off_only_stops_the_record(room_server, monkeypatch, mode, recorded):
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    db.set_setting(ctx.conn, veto.SETTING, mode)
    before = _packets_to(woken, CLONER1)
    _run_to_the_cap(room_server, clock)
    assert _packets_to(woken, CLONER1) - before == CAP and _round(ctx)["state"] == "failed"
    assert bool(_rows(ctx)) is recorded
    assert {r["mode"] for r in _rows(ctx)} <= {mode}


def test_a_brain_that_fails_sends_nothing_fails_nothing_and_says_so_once(room_server, monkeypatch):
    """עיקרון 5: "לא הצלחנו להחליט" אינו "תקין" וגם אינו הכשלה. הסבב נשאר פתוח, ושורה אחת, לא אחת לכל דופק."""
    assert silence is not None, "server/brain/silence.py חסר"
    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    monkeypatch.setattr(silence, "judge_rewake", lambda **k: 1 / 0)
    before = _packets_to(woken, CLONER1)
    _run_to_the_cap(room_server, clock)
    assert _packets_to(woken, CLONER1) == before, "WoL נשלח בלי החלטה של המוח"
    assert _round(ctx)["state"] == "active" and not _events(ctx, "room_wake_failed")
    row = one(_rows(ctx))
    assert row["verdict"] == "UNDECIDED" and "ZeroDivisionError" in row["reason"]


def test_a_stamp_from_the_future_with_nothing_to_measure_from_is_calibrated_and_recorded(room_server, monkeypatch):
    """#3590: גל שממתין רק למחזיק, חותמת הפתיחה שלו בעתיד, ואין הערה שנשלחה: מכיילים ל'עכשיו' ולא מכשילים ולא שולחים."""
    from test_held_console_2031 import held_report                           # noqa: PLC0415
    ctx, anon, woken = room_server["ctx"], room_server["anon"], room_server["woken"]
    clock, wave = _wave1_partial(room_server, monkeypatch)
    held_report(anon)
    future = (clock.now + timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET created_at = ? WHERE id = ?", (future, wave))
    ctx.conn.commit()
    before = _packets_to(woken, CLONER1)
    room_mod.sweep(ctx.conn, ctx.store)
    assert _packets_to(woken, CLONER1) == before and _round(ctx)["state"] == "active"
    row = one(_rows(ctx))
    assert row["verdict"] == "CALIBRATE" and row["outcome"] == "חותמת פתיחת הגל כוילה ל'עכשיו'", row
    stamp = ctx.conn.execute("SELECT created_at FROM sessions WHERE id = ?", (wave,)).fetchone()["created_at"]
    assert datetime.fromisoformat(stamp) <= clock.now + timedelta(seconds=1), "החותמת נשארה בעתיד"


def test_the_console_line_says_the_server_executed_it(room_server, monkeypatch):
    ctx = room_server["ctx"]
    clock, _wave = _wave1_partial(room_server, monkeypatch)
    _run_to_the_cap(room_server, clock, ticks=2)
    row = one(memory.decisions(ctx.conn, rule="K-REWAKE", limit=1))
    assert "WoL חוזר" in row["line_he"] and "רק נרשם" not in row["line_he"] and "השרת ביצע" in row["line_he"], row["line_he"]
