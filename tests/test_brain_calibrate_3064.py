"""KT2: כיול סף החשד לכל משכפל (`server/brain/calibrate.py`) -- "המוח שולט בספים" (נדב 14/11 בערב, זאוס 15/11).

- **התצפית:** ה-tick רואה משכפל שנהיה SUSPECT וחוזר לחיים בלי LOST, ורושם שורת K1 ``RECOVERED`` עם ``silence_s``
  ב-``brain_decisions`` (זיכרון ארוך-הטווח, בלי שינוי סכמה).
- **הלמידה:** מספיק היסטוריה (3 תצפיות מ-3 סבבים בחלון) = האחוזון ה-90 כפול 1.25, לא פחות מערך ההתחלה ולא יותר מחצי
  מ-LOST. לא מספיק, או לא נקרא = 30, והסיבה כתובה בשורת ה-SUSPECT (עיקרון 5).
- **ההחלטה:** `decide.liveness` משתמש בסף של המשכפל; **K2 אינו משתנה** (SUSPECT ו-ALIVE שניהם חוסמים).

**בקרה שלילית:** החזרת `cfg.suspect_s` ל-`decide.liveness` (במקום `cfg.suspect_for`) מפילה את טסטי ההחלטה והלולאה; הסרת
`calibrate.record` מ-`tick.run` מפילה את טסט התצפית; הסרת `calibrate.apply` מ-`veto.config` מפילה את טסט הווטו.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import calibrate, decide as brain, memory, observe, tick, veto
from server.brain.observe import build

MAC = "aa:bb:cc:dd:ee:01"
W = "W-kt2"
NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    tick.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()
    tick.forget()


def _stamp(age: float, now: float | None = None) -> str:
    base = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    return (base - timedelta(seconds=age)).isoformat(timespec="seconds")


def member(conn, mac: str, age: float, session: str = W) -> None:
    conn.execute("INSERT OR REPLACE INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, 'writing', ?, ?)", (session, mac, _stamp(age),
                                                     json.dumps([{"dev": "sda", "serial": f"{mac}-1", "state": "writing"}])))
    conn.commit()


def history(conn, silences, rounds=None, *, mac=MAC, days_ago=1.0):
    """תצפיות `RECOVERED` שכבר נרשמו: שתיקה לכל אחת, בסבב שלה."""
    rounds = rounds or [f"R{n}" for n in range(len(silences))]
    for n, (silence, rnd) in enumerate(zip(silences, rounds)):
        at = _stamp(days_ago * DAY + n)
        memory.record(conn, mode="shadow", rule="K1", point="KT2", action="LIVENESS", verdict="RECOVERED",
                      reason="x", evidence=(f"silence_s={silence}", "threshold_s=30"), round_id=rnd,
                      entity=("machine", mac), outcome=calibrate.RECOVERED_HE, at=at)


def learn(conn, **kw):
    args = dict(now=datetime.now(timezone.utc).timestamp(), start_s=30.0, lost_s=180.0, max_age_s=90 * DAY)
    return calibrate.learn(conn, MAC, **{**args, **kw})


# --- הלמידה ------------------------------------------------------------------------------------------------------


def test_no_history_is_the_start_value_and_says_why(conn):
    got = learn(conn)
    assert got.threshold_s == 30.0 and got.calibrated is False and got.observations == 0
    assert "אין מספיק היסטוריה" in got.reason and "0 שתיקות" in got.reason


@pytest.mark.parametrize("silences, rounds", [([50, 60], None), ([50, 60, 70], ["R1", "R1", "R1"]), ([50, 60, 70], ["R1", "R1", "R2"])])
def test_too_few_observations_or_too_few_rounds_is_not_a_pattern(conn, silences, rounds):
    history(conn, silences, rounds)
    got = learn(conn)
    assert got.threshold_s == 30.0 and not got.calibrated and "אין מספיק היסטוריה" in got.reason


def test_enough_history_gives_the_90th_percentile_with_a_margin(conn):
    history(conn, [40, 44, 48, 50, 52, 54, 56, 58, 60, 62])           # ten rounds; nearest-rank p90 = 60; 60 x 1.25 = 75
    got = learn(conn)
    assert got.threshold_s == 75.0 and got.calibrated and got.observations == 10
    assert "מכויל 75s" in got.reason and "10 תצפיות" in got.reason


def test_the_threshold_is_never_below_the_start_value(conn):
    """תצפית נעשית רק על שתיקה שעברה את הסף: אין ממה ללמוד שהוא צריך לרדת."""
    history(conn, [31, 32, 33])
    got = learn(conn, start_s=60.0)                                   # 33 x 1.25 = 42 < 60
    assert got.threshold_s == 60.0 and not got.calibrated and "קצרות ממנו" in got.reason


def test_the_threshold_never_passes_half_of_lost(conn):
    """חשד חייב לבוא לפני אובדן: מעל חצי מ-LOST הוא כבר לא אזהרה."""
    history(conn, [200, 300, 400])
    got = learn(conn)
    assert got.threshold_s == 90.0 and got.calibrated and "חצי מ-LOST" in got.reason
    assert learn(conn, lost_s=600.0).threshold_s == 300.0


def test_old_observations_are_outside_the_window(conn):
    history(conn, [50, 60, 70], days_ago=100)
    assert not learn(conn).calibrated
    assert learn(conn, max_age_s=200 * DAY).calibrated


def test_one_machines_history_is_not_anothers(conn):
    history(conn, [50, 60, 70], mac="aa:bb:cc:dd:ee:02")
    assert not learn(conn).calibrated


def test_history_that_cannot_be_read_is_the_start_value_and_says_not_read(conn, monkeypatch):
    """"לא נקרא" אינו "אין היסטוריה": שתי סיבות שונות (עיקרון 5)."""
    monkeypatch.setattr(memory, "decisions", lambda *a, **k: 1 / 0)
    got = learn(conn)
    assert got.threshold_s == 30.0 and "ההיסטוריה לא נקראה" in got.reason and "אין מספיק" not in got.reason


# --- ההחלטה ------------------------------------------------------------------------------------------------------


def snap(age: float, mac: str = MAC):
    return build(now=NOW, round_id="S", members=[{"mac": mac, "state": "writing",
                                                  "updated_at": _stamp(age, NOW), "targets_json": None}], part=4)


def test_a_calibrated_machine_is_alive_where_the_start_value_says_suspect():
    plain = brain.Config()
    tuned = dataclasses.replace(plain, suspect_by_mac={MAC: 75.0}, suspect_note={MAC: "מכויל 75s: x"})
    assert brain.liveness(snap(60).cloner(MAC), NOW, plain)[0] == brain.SUSPECT
    assert brain.liveness(snap(60).cloner(MAC), NOW, tuned)[0] == brain.ALIVE
    assert brain.liveness(snap(80).cloner(MAC), NOW, tuned)[0] == brain.SUSPECT


def test_the_suspect_line_says_which_threshold_and_why():
    cfg = dataclasses.replace(brain.Config(), suspect_note={MAC: "ערך התחלה 30s: אין מספיק היסטוריה (0 שתיקות...)"})
    state, reason, _ev = brain.liveness(snap(60).cloner(MAC), NOW, cfg)
    assert state == brain.SUSPECT and "סף חשד 30s: ערך התחלה 30s: אין מספיק היסטוריה" in reason
    assert "סף חשד" not in brain.liveness(snap(60).cloner(MAC), NOW, brain.Config())[1]    # no note: the line of today


def test_a_machine_with_no_entry_uses_the_start_value():
    cfg = dataclasses.replace(brain.Config(), suspect_by_mac={"someone-else": 99.0})
    assert cfg.suspect_for(MAC) == 30.0 and cfg.suspect_for("someone-else") == 99.0


@pytest.mark.parametrize("junk", [0, -5, True, "75", None])
def test_a_junk_threshold_is_not_used(junk):
    assert dataclasses.replace(brain.Config(), suspect_by_mac={MAC: junk}).suspect_for(MAC) == 30.0


@pytest.mark.parametrize("age", [10, 40, 60, 100, 170, 200])
def test_k2_decides_the_same_with_and_without_calibration(age):
    """SUSPECT ו-ALIVE שניהם חוסמים: הכיול משנה תווית, לא החלטה על פעולה הרסנית."""
    proposal = observe.Proposal("FAIL_WAVE", "D16", (MAC,), "t")
    s = build(now=NOW, round_id="S", members=[{"mac": MAC, "state": "writing", "updated_at": _stamp(age, NOW),
                                               "targets_json": None}], proposals=(proposal,), part=4)
    tuned = dataclasses.replace(brain.Config(), suspect_by_mac={MAC: 90.0}, suspect_note={MAC: "x"})
    k2 = lambda cfg: [(d.verdict) for d in brain.decide(s, cfg) if d.rule == "K2"]
    assert k2(tuned) == k2(brain.Config())


def test_the_config_with_its_dicts_survives_the_recording_round_trip():
    """`recording` כותב `asdict(cfg)` ו-`load` קורא `Config(**...)` (R14)."""
    cfg = dataclasses.replace(brain.Config(), suspect_by_mac={MAC: 75.0}, suspect_note={MAC: "מכויל"})
    assert brain.Config(**dataclasses.asdict(cfg)) == cfg
    assert brain.Config(**json.loads(json.dumps(dataclasses.asdict(cfg)))) == cfg


# --- התצפית והלולאה: ה-tick, והווטו --------------------------------------------------------------------------------


def _touch(conn, mac: str, age: float) -> None:
    conn.execute("UPDATE session_members SET updated_at = ? WHERE mac = ?", (_stamp(age), mac))
    conn.commit()


def test_the_tick_records_a_suspect_that_came_back_as_an_observation(conn):
    member(conn, MAC, 60)                                             # SUSPECT: 30 < 60 < 180
    clock = iter([0.0, 20.0, 40.0])
    assert [(d.verdict) for d in tick.run(conn, W, mono=lambda: next(clock))] == [brain.SUSPECT]
    _touch(conn, MAC, 1)
    assert [(d.verdict) for d in tick.run(conn, W, mono=lambda: next(clock))] == [brain.ALIVE]
    [row] = [r for r in memory.decisions(conn, kind="machine", key=MAC, rule="K1", limit=10) if r["verdict"] == "RECOVERED"]
    assert row["point"] == "KT2" and row["round_id"] == W and row["mode"] == "shadow" and row["outcome"]
    silence = int(next(e for e in row["evidence"] if e.startswith("silence_s=")).split("=")[1])
    assert 60 <= silence < 70 and "threshold_s=30" in row["evidence"]    # the age when it turned suspect, plus a moment


def test_a_suspect_that_became_lost_is_not_a_recovery(conn):
    member(conn, MAC, 60)
    clock = iter([0.0, 20.0, 40.0])
    tick.run(conn, W, mono=lambda: next(clock))
    _touch(conn, MAC, 400)                                            # silent past LOST
    assert [(d.verdict) for d in tick.run(conn, W, mono=lambda: next(clock))] == [brain.LOST]
    _touch(conn, MAC, 1)
    tick.run(conn, W, mono=lambda: next(clock))
    assert [r for r in memory.decisions(conn, kind="machine", key=MAC, rule="K1", limit=20) if r["verdict"] == "RECOVERED"] == []


def test_off_records_no_observation(conn):
    db.set_setting(conn, veto.SETTING, "off")
    member(conn, MAC, 60)
    assert tick.run(conn, W, mono=lambda: 0.0) == []
    assert memory.decisions(conn, limit=10) == []


def test_the_whole_loop_the_fourth_round_does_not_flag_what_the_machine_always_does(conn):
    """שלוש שתיקות של ~55 ש' שנגמרו מעצמן בשלושה סבבים -> הסף של המשכפל הזה 69 ש', וב-60 ש' הוא עדיין חי."""
    history(conn, [50, 55, 56])                                       # p90 (nearest rank, n=3) = 56; x 1.25 = 70
    cfg = veto.config(conn, [MAC])
    assert cfg.suspect_for(MAC) == 70.0 and "מכויל 70s" in cfg.suspect_note[MAC]
    other = veto.config(conn, ["aa:bb:cc:dd:ee:09"])
    assert other.suspect_for("aa:bb:cc:dd:ee:09") == 30.0 and "אין מספיק היסטוריה" in other.suspect_note["aa:bb:cc:dd:ee:09"]
    member(conn, MAC, 60)
    assert tick.run(conn, W, mono=lambda: 0.0) == []                  # 60 < 70: alive, and ALIVE first is not a decision
    veto.forget()


def test_the_veto_sees_the_calibrated_threshold_for_its_targets_and_the_decision_is_the_same(conn):
    history(conn, [50, 55, 56])
    member(conn, MAC, 60)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=[MAC], reason="t")
    assert got.proceed is False and got.would_block and f"{MAC}=ALIVE" in got.reason    # held, as it would be with SUSPECT
    assert veto.config(conn).suspect_by_mac == {}                      # no macs asked: the start values


def test_config_without_macs_is_the_plain_start_values(conn):
    history(conn, [50, 55, 56])
    assert veto.config(conn) == brain.Config(lost_s=180)
