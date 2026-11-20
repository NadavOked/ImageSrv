"""#3320: K6 (תחילת מחיצה), ה-tick של K1 (KT1/KT2), הקלטת גל וסימולציה חוזרת (R14), והשורה בעברית (R1).

- **K6 טהור** (`partition.decide`) מול שני המקרים האמיתיים: room_714f980d מ-13/11 (I18 א/ב/ג) ושני משכפלים שאחד מהם
  משלים (I19). K6 לעולם אינו מציע FAIL_WAVE.
- **K6 בנקודה** (`veto.check` ב-D16): נרשם ב-``brain_decisions`` ככלל ``K6``, תמיד בצל -- גם ב-enforce.
- **ה-tick:** מעבר מצב נרשם פעם אחת; ALIVE ראשון אינו נרשם; לכל היותר פעם ב-``TICK_S``; ``off`` = כלום.
- **ההקלטה:** כל החלטה נכתבת עם הצילום המלא, ו-`replay` על הקלטה שלא השתנתה מחזיר ריק -- ועל צילום ששונה, לא.
- **R1:** כל החלטה שנקראת נושאת ``line_he``, עם הישות, הפעולה והפסק במילים.

**בקרה שלילית:** הסרת `_partition` מ-`veto.check`, את הקריאה ל-`brain_tick.run` מ-`room.tick`, את `recording.write`
מהווטו ואת ``line_he`` מ-`memory._decision` -- מפילה את הטסטים של הנקודה, של ה-tick, של ההקלטה ושל R1 בהתאמה.
"""

from __future__ import annotations

import itertools
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import decide as brain, memory, partition, recording, say, tick, veto
from server.brain.observe import FETCHED, LISTENING, SERVED, build
from server.rounds import room

NOW = 1_800_000_000.0
CFG = brain.Config(suspect_s=30, lost_s=180, stalled_s=180, ceiling_s=1800)
W = "W-3320"


def iso(age: float, now: float = NOW) -> str:
    return datetime.fromtimestamp(now - age, timezone.utc).isoformat(timespec="seconds")


def snap(members, engine=(), served=(), part=4, unknown=()):
    return build(now=NOW, round_id="room_714f980d", members=members, engine=engine, served=served, part=part,
                 unknown=unknown)


def m(mac, age, state="writing"):
    return {"mac": mac, "state": state, "updated_at": iso(age), "targets_json": None}


# --- K6, טהור: I18 (room_714f980d, 13/11) -------------------------------------------------------------------


def test_I18a_the_1311_machine_with_v6_holds_the_partition_never_fails_the_wave():
    # משכפל אחד, p3 נגמרה, הוא משלים מגירה שחזרה ב-HTTP (V6: השרת הגיש לו לפני 5 ש'), אין listening על p4, progress
    # כל הזמן, עברו 780 ש' של המתנה
    s = snap([m("cl2", 2)], engine=[("cl2", "ended", 790, "chunk-pass p3 pass 2")],
             served=[("cl2", SERVED, 5, "http-images")])
    d = partition.decide(s, CFG, members=["cl2"], held_s=0)
    assert (d.rule, d.verdict) == ("K6", partition.HOLD_PARTITION), d
    assert "FAIL" not in d.verdict and "cl2" in d.reason


def test_I18b_progress_alone_holds_and_after_the_ceiling_parks():
    s = snap([m("cl2", 2)], engine=[("cl2", "ended", 790, "chunk-pass p3 pass 2")])
    assert partition.decide(s, CFG, members=["cl2"], held_s=0).verdict == partition.HOLD
    assert partition.decide(s, CFG, members=["cl2"], held_s=1799).verdict == partition.HOLD
    parked = partition.decide(s, CFG, members=["cl2"], held_s=1800)
    assert parked.verdict == partition.PARK and "לא נמחקות" in parked.reason


def test_I18c_no_evidence_for_the_lost_window_is_lost_and_parked_not_failed():
    s = snap([m("cl2", 181)])
    d = partition.decide(s, CFG, members=["cl2"])
    assert d.verdict == partition.PARK and "LOST" in d.reason and "cl2=LOST" in d.evidence


def test_I18_a_sense_that_was_not_read_is_never_parked_before_the_ceiling():
    s = snap([m("cl2", 400)], unknown=["served_http"])
    assert partition.decide(s, CFG, members=["cl2"]).verdict == partition.HOLD


# --- K6, טהור: I19 (שני משכפלים, אחד משלים) ---------------------------------------------------------------------


def test_I19_a_ready_cloner_starts_and_the_completing_one_goes_on_alone():
    s = snap([m("A", 1), m("B", 1)],
             engine=[("A", LISTENING, 3, "chunk-pass p4 pass 1"), ("B", FETCHED, 2, "fetching p3")])
    d = partition.decide(s, CFG, members=["A", "B"], held_s=0)
    assert d.verdict == partition.START and d.target == "A"
    assert "continue_alone=B" in d.evidence and "HTTP" in d.reason


def test_I19_listening_on_another_partition_is_not_ready_for_this_one():
    s = snap([m("A", 1)], engine=[("A", LISTENING, 3, "chunk-pass p3 pass 1")])
    assert partition.decide(s, CFG, members=["A"]).verdict == partition.HOLD


def test_K6_never_proposes_to_fail_or_delete():
    for members, engine, held in [([m("A", 1)], [], 0), ([m("A", 999)], [], 0), ([m("A", 1)], [], 5000),
                                  ([m("A", 1)], [("A", LISTENING, 1, "chunk-pass p4 pass 1")], 0)]:
        assert partition.decide(snap(members, engine=engine), CFG, held_s=held).verdict in partition.VERDICTS


# --- K6 בנקודה (D16) -----------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    tick.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()
    tick.forget()


def member(conn, mac: str, age: float, state: str = "writing") -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, ?, ?, ?)", (W, mac, state, stamp,
                                             json.dumps([{"dev": "sda", "serial": f"{mac}-1", "state": state}])))
    conn.commit()


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_D16_records_K6_in_shadow_whatever_the_switch(conn, mode):
    db.set_setting(conn, veto.SETTING, mode)
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="later_start_timeout 780s",
               part=4, engine=[("aa", "ended", 790, "chunk-pass p3 pass 2")])
    rows = memory.decisions(conn, rule="K6", limit=10)
    assert len(rows) == 1, rows
    assert rows[0]["mode"] == "shadow" and rows[0]["point"] == "D16" and rows[0]["verdict"] == partition.HOLD
    assert rows[0]["outcome"] == veto.K6_SHADOW_HE


def test_D16_asked_again_inside_the_same_hold_is_not_a_new_K6(conn):
    db.set_setting(conn, veto.SETTING, "enforce")
    member(conn, "aa", 2)
    clock = itertools.count(100.0, 10.0)
    for _ in range(3):
        veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4,
                   mono=lambda: next(clock))
    assert len(memory.decisions(conn, rule="K6", limit=10)) == 1


def test_another_point_is_not_judged_by_K6(conn):
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_DISKS", rule="D15", round_id=W, targets=["aa"], reason="t", part=4)
    assert memory.decisions(conn, rule="K6", limit=10) == []


def test_off_writes_no_K6(conn):
    db.set_setting(conn, veto.SETTING, "off")
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4)
    assert memory.decisions(conn, limit=10) == []


# --- R14: ההקלטה והסימולציה החוזרת ----------------------------------------------------------------------------


def test_the_veto_records_the_full_snapshot_and_a_replay_of_it_changes_nothing(conn, tmp_path):
    rec = recording.Recorder(tmp_path / "waves")
    recording.attach(conn, rec)
    member(conn, "aa", 2)
    member(conn, "bb", 400)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa", "bb"], reason="t", part=4,
               engine=[("aa", LISTENING, 1, "chunk-pass p3 pass 1")])
    entries = recording.load(rec.path(W))
    assert [e["kind"] for e in entries] == ["K2", "K6"]
    assert {c.mac for c in entries[0]["snapshot"].cloners} == {"aa", "bb"}
    assert all(e["ids"] and all(isinstance(i, int) for i in e["ids"]) for e in entries)
    assert recording.replay(rec.path(W)) == []


def test_a_replay_says_where_todays_rules_decide_otherwise(conn, tmp_path):
    rec = recording.Recorder(tmp_path / "waves")
    recording.attach(conn, rec)
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4)
    lines = rec.path(W).read_text(encoding="utf-8").splitlines()
    e = json.loads(lines[0])
    e["decisions"][-1]["verdict"] = "PROCEED"          # מה ש"הוקלט" שונה ממה שהכלל של היום אומר
    rec.path(W).write_text(json.dumps(e, ensure_ascii=False) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8")
    diffs = recording.replay(rec.path(W))
    assert len(diffs) == 1 and diffs[0][1]["verdict"] == "PROCEED" and diffs[0][2]["verdict"] == "HOLD"


def test_a_recording_whose_snapshot_was_altered_is_not_read_as_valid(conn, tmp_path):
    rec = recording.Recorder(tmp_path / "waves")
    recording.attach(conn, rec)
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4)
    e = json.loads(rec.path(W).read_text(encoding="utf-8").splitlines()[0])
    e["snapshot"]["now"] += 1
    rec.path(W).write_text(json.dumps(e) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        recording.load(rec.path(W))


def test_old_recordings_are_pruned(tmp_path):
    rec = recording.Recorder(tmp_path / "waves", keep=2)
    s = snap([m("A", 1)])
    for n in range(4):
        assert rec.write(f"W{n}", kind="K1", snap=s, cfg=CFG, decisions=brain.decide(s, CFG))
    assert sorted(p.name for p in (tmp_path / "waves").iterdir()) == ["W2.jsonl", "W3.jsonl"]


def test_without_a_recorder_nothing_is_written_and_nothing_fails(conn):
    member(conn, "aa", 2)
    assert veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4).proceed


# --- KT1/KT2: ה-tick ---------------------------------------------------------------------------------------------


def test_the_tick_records_a_transition_once_and_alive_first_is_not_a_decision(conn):
    member(conn, "aa", 2)
    member(conn, "bb", 60)                        # SUSPECT: 30 < 60 < 180
    clock = iter([0.0, 5.0, 20.0, 40.0])
    moved = tick.run(conn, W, mono=lambda: next(clock))
    assert [(d.target, d.verdict) for d in moved] == [("bb", brain.SUSPECT)]
    assert tick.run(conn, W, mono=lambda: next(clock)) == []          # 5 ש' < TICK_S: לא רץ
    assert tick.run(conn, W, mono=lambda: next(clock)) == []          # רץ, אבל אין מעבר
    conn.execute("UPDATE session_members SET updated_at = ? WHERE mac = 'bb'",
                 ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds"),))
    conn.commit()
    moved = tick.run(conn, W, mono=lambda: next(clock))
    assert [(d.target, d.verdict) for d in moved] == [("bb", brain.ALIVE)]
    rows = memory.decisions(conn, kind="machine", key="bb", rule="K1", limit=10)
    # #KT2 (כיול): חשוד שחזר לחיים נרשם גם כתצפית `RECOVERED` (`calibrate.record`), לפני שורת המעבר
    assert [r["verdict"] for r in rows] == [brain.ALIVE, "RECOVERED", brain.SUSPECT]
    moves = [r for r in rows if r["verdict"] != "RECOVERED"]
    assert all(r["mode"] == "shadow" and r["point"] == "KT1" and r["outcome"] == tick.SHADOW_HE for r in moves)
    assert memory.decisions(conn, kind="machine", key="aa", rule="K1", limit=10) == []


def test_the_suspect_threshold_is_KT2_from_the_code_not_from_a_setting(conn):
    """נדב 14/11 בערב: ספים טכניים אינם בהגדרות. ערך ישן ששמור (100) אינו נקרא: 60 ש' בלי ראיה הן SUSPECT (30)."""
    db.set_setting(conn, "brain_suspect_seconds", "100")
    member(conn, "bb", 60)
    moved = tick.run(conn, W, mono=lambda: 0.0)
    assert [(d.target, d.verdict) for d in moved] == [("bb", brain.SUSPECT)]


def test_off_ticks_nothing(conn):
    db.set_setting(conn, veto.SETTING, "off")
    member(conn, "bb", 400)
    assert tick.run(conn, W, mono=lambda: 0.0) == []
    assert memory.decisions(conn, limit=10) == []


def test_a_tick_that_fails_never_raises(conn, monkeypatch, caplog):
    monkeypatch.setattr(tick.observe, "from_db", lambda *a, **k: 1 / 0)
    assert tick.run(conn, W, mono=lambda: 0.0) == []
    assert "the K1 tick" in caplog.text


class Store:
    def __init__(self, conn):
        self.conn = conn

    def members(self, wave_id):
        return self.conn.execute("SELECT * FROM session_members WHERE session_id = ?", (wave_id,)).fetchall()


def test_the_room_pulse_of_a_running_wave_ticks_the_brain(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(tick, "run", lambda c, wave_id, **kw: seen.append(wave_id) or [])
    monkeypatch.setattr(room.clockstep, "guard", lambda *a: None)
    monkeypatch.setattr(room, "active_round", lambda c: {"wave_session_id": W, "id": "R-1"})
    monkeypatch.setattr(room.roundend, "settle", lambda *a: None)
    monkeypatch.setattr(room, "_mark_lost", lambda c, store, wave, members: members)
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
                 " opened_by, created_at, last_join_at) VALUES (?, 'g', 'img', 'p', 1, 60, 'running', 't', 'x', 0)",
                 (W,))
    conn.commit()
    room.tick(conn, Store(conn))
    assert seen == [W]


# --- R1: שורה בעברית ---------------------------------------------------------------------------------------------


def test_every_decision_read_carries_a_hebrew_line(conn):
    memory.record(conn, mode="shadow", rule="K2", point="D16", action="FAIL_WAVE", verdict="HOLD",
                  reason="aa=ALIVE: ראיה לפני 2s", round_id=W, entity=("machine", "aa"), outcome="x")
    memory.record(conn, mode="enforce", rule="K7", action="HTTP_BUDGET", verdict="CUT", reason="המקלטים זרקו")
    by_rule = {d["rule"]: d["line_he"] for d in memory.decisions(conn, limit=10)}
    assert by_rule["K2"] == "משכפל aa → הכשלת הגל: החזקה. aa=ALIVE: ראיה לפני 2s (צל: רק נרשם)"
    assert by_rule["K7"] == "המוח (כללי) → תקציב ההורדות: הפחתה. המקלטים זרקו"


def test_an_action_without_a_label_keeps_its_name_not_unknown():
    line = say.line_he({"entity_kind": "disk", "entity_key": "S1", "action": "NEW_THING", "verdict": "ZZ",
                        "reason": "", "mode": "enforce"})
    assert line == "דיסק S1 → NEW_THING: ZZ"


def test_every_action_the_brain_records_today_has_a_hebrew_label():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "server"
    found = set()
    for f in [*root.glob("brain/*.py"), root / "rounds" / "chunk_rate.py", root / "rounds" / "room.py"]:
        found |= set(re.findall(r'action="([A-Z_]+)"', f.read_text(encoding="utf-8")))
    found |= {partition.ACTION, "FAIL_WAVE", "FAIL_DISKS", "EXPIRE_LEDGER"}
    assert found - set(say.ACTION_HE) == set()


def test_K6_log_line_is_structured(conn, caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    member(conn, "aa", 2)
    veto.check(conn, action="FAIL_WAVE", rule="D16", round_id=W, targets=["aa"], reason="t", part=4)
    k6 = [json.loads(r.getMessage()[6:]) for r in caplog.records
          if r.getMessage().startswith("brain ") and '"rule": "K6"' in r.getMessage()]
    assert len(k6) == 1 and k6[0]["snap"] and k6[0]["reason"] and k6[0]["mode"] == "shadow"
