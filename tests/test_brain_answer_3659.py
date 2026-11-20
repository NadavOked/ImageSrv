"""#3659 (אפיק #3653): מה עונים למשכפל שדיווח על מעבר (S14, `ChunkEngine._answer`) -- המוח מחליט (`repairrule.judge_answer`, כלל K-REPAIR), המנוע בונה את התשובה.

- **ההתנהגות זהה לזו של לפני #3659.** `OLD_ANSWER` הוא העתק מילולי של `_answer` בבסיס (`lab/vm-1114f`, `13828c0e4`), בתור אורקל בטסט בלבד, ומושווה לתשובה
  של המנוע האמיתי על רשת של מצבים: מחיצה סגורה או פתוחה, חוסר, דיווח, תוכנית (מאזין / מוריד / שלם), סבבים, ומאחר שהמכסה שלו נגמרה.
  הטסטים של #2533 (`test_chunksend_2533`) לא השתנו בשביל זה ובודקים את ההתנהלות כולה.
- **תשובה ממתינה אינה נרשמת** (נשאלת בכל דופק); שאר התשובות נרשמות פעם אחת לכל (גל, מחשב, תשובה, מעבר).
- **אין החלטה = ממתינים**, ושורת `UNDECIDED` אחת.

**בקרה שלילית:** בבסיס אין `judge_answer` והמנוע מחליט בעצמו; הטסטים שעוברים דרך המנוע נופלים ב-assert על `brain_decisions` ריק, ושל המודול ב-`assert ... is not None`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import repairrule, silence
from server.rounds import chunksend, repair

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: E402,F401 -- rig is a fixture

OLD_MAX = 5


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


@pytest.fixture(autouse=True)
def _fresh_rule_registers():
    silence.forget()
    getattr(repairrule, "forget", lambda: None)()      # בקרה שלילית: בבסיס אין `forget`, וכשל ב-fixture אינו כישלון התנהגותי
    yield
    silence.forget()
    getattr(repairrule, "forget", lambda: None)()


def decisions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K-REPAIR' AND point = 'S14' ORDER BY id")]


def OLD_ANSWER(lacks, w, part, mac, rnd):
    """`ChunkEngine._answer` בבסיס, מילה במילה (`lacks` כבר נאסף)."""
    if part in w.done_parts and lacks:
        first = w.late_first.setdefault((part, mac), rnd)
        if rnd - first >= OLD_MAX:
            for serial, gap in lacks.items():
                w.failed_disks[(mac, serial)] = (
                    f"לא הצליח לקבל את כל הנתונים אחרי {OLD_MAX} ניסיונות השלמה"
                    f" ({repair.count(gap)} חלקים עדיין חסרים)")
            lacks = {}
    fail = {serial: why for (m, serial), why in w.failed_disks.items() if m == mac}
    out: dict = {"ok": True, **({"fail": fail} if fail else {})}
    if part in w.done_parts:
        if not lacks:
            return {**out, "action": "done"}
        both: repair.Ranges = []
        for gap in lacks.values():
            both = repair.union(both, gap)
        return {**out, "action": "fetch", "pass": rnd + 1, "ranges": [list(r) for r in both]}
    mark = w.marks.get((part, mac))
    waiting = {**out, "action": "wait", "poll": 2,
               **({"whole": True} if mark is not None and not lacks else {}),
               **({"others": True} if mark is not None and mark.state == "ended" and mark.round >= w.round else {})}
    if w.part != part or w.plan is None or w.round <= rnd:
        return waiting
    if mac in w.plan.receive:
        return {**out, "action": "receive", "pass": w.round}
    if mac in w.plan.fetch:
        return {**out, "action": "fetch", "pass": w.round, "ranges": [list(r) for r in w.plan.fetch[mac]]}
    return waiting


def _world(*, done, lacks, mark, same_part, plan, wave_round, late_back):
    part, mac = 3, "m1"
    plans = {"none": None, "receive": repair.Plan(receive={mac}), "fetch": repair.Plan(fetch={mac: [(9, 9)]}),
             "others": repair.Plan(receive={"m2"}, fetch={"m2": [(1, 1)]})}
    marks = {"none": {}, "ended": {(part, mac): SimpleNamespace(state="ended", round=2)},
             "listening": {(part, mac): SimpleNamespace(state="listening", round=1)}}
    return part, mac, SimpleNamespace(
        session_id="ses", done_parts={part} if done else set(), late_first={(part, mac): 2 - late_back}, failed_disks={},
        marks=marks[mark], part=part if same_part else 9, plan=plans[plan], round=wave_round)


LACKS = {"empty": {}, "one": {"S1": [(5, 6)]}, "two": {"S1": [(5, 6)], "S2": [(0, 1), (5, 8)]}}


def test_the_answer_is_exactly_what_the_engine_answered_before():
    assert hasattr(repairrule, "judge_answer"), "server/brain/repairrule.py: judge_answer חסר"
    checked = 0
    for done in (False, True):
        for lacks_name, lacks in LACKS.items():
            for mark in ("none", "ended", "listening"):
                for same_part in (True, False):
                    for plan in ("none", "receive", "fetch", "others"):
                        for wave_round in (1, 2, 3):
                            for rnd in (1, 2):
                                for late_back in (0, OLD_MAX):
                                    kw = dict(done=done, lacks=lacks, mark=mark, same_part=same_part, plan=plan, wave_round=wave_round,
                                              late_back=late_back)
                                    part, mac, old_w = _world(**kw)
                                    _, _, new_w = _world(**kw)
                                    want = OLD_ANSWER(dict(lacks), old_w, part, mac, rnd)
                                    stub = SimpleNamespace(_lacks=lambda w, p, m, lacks=lacks: dict(lacks), brain_conn=None)
                                    got = chunksend.ChunkEngine._answer(stub, new_w, part, mac, rnd)
                                    assert got == want, (kw, rnd)
                                    assert new_w.failed_disks == old_w.failed_disks, (kw, rnd)
                                    checked += 1
    assert checked == 2 * 3 * 3 * 2 * 4 * 3 * 2 * 2


def test_the_engine_records_receive_fetch_and_done_once_each_and_never_a_wait(rig, conn):
    engine = rig.make()
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.tell("a", 1, "ended", {"A1": ALL})["action"] == "wait"
    assert rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})["action"] == "wait"
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]}) == {"ok": True, "action": "receive", "pass": 2}
    assert rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]}) == {"ok": True, "action": "receive", "pass": 2}
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})                        # asked again: the same answer, not another row
    assert [(r["verdict"], r["entity_key"]) for r in decisions(conn)] == [("ANSWER_RECEIVE", "c"), ("ANSWER_RECEIVE", "b")]
    row = decisions(conn)[0]
    assert (row["point"], row["action"], row["mode"], row["entity_kind"], row["round_id"]) == ("S14", "REPAIR_ANSWER", "shadow", "machine", SID)
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    rig.tell("b", 2, "ended", {"B1": ALL})
    assert rig.ask("c", 2, {"C1": [[0, 8]]}) == {"ok": True, "action": "fetch", "pass": 3, "ranges": [[9, 9]]}
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    verdicts = [(r["verdict"], r["entity_key"]) for r in decisions(conn)]
    assert ("ANSWER_FETCH", "c") in verdicts and ("ANSWER_DONE", "c") in verdicts
    assert all(r["verdict"] != "ANSWER_WAIT" for r in decisions(conn))


def test_off_decides_and_does_not_record(rig, conn):
    db.set_setting(conn, "brain_veto_mode", "off")
    engine = rig.make()
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]}) == {"ok": True, "action": "receive", "pass": 2}
    assert decisions(conn) == []


def test_no_decision_is_wait_and_one_undecided_row(rig, conn, monkeypatch):
    def boom(**kw):
        raise RuntimeError("brain down")
    monkeypatch.setattr(repairrule, "judge_answer", boom)
    engine = rig.make()
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for _ in range(3):
        assert rig.tell("a", 1, "ended", {"A1": ALL}) == {"ok": True, "action": "wait", "poll": 2}
    assert [r["verdict"] for r in decisions(conn)] == ["UNDECIDED"]


def test_the_answers_are_registered_where_the_console_looks_for_them():
    from server.brain import say
    assert repairrule.ANSWER_ACTION in say.ACTION_HE
    assert {repairrule.ANSWER_RECEIVE, repairrule.ANSWER_FETCH, repairrule.ANSWER_DONE, repairrule.ANSWER_WAIT} <= set(say.VERDICT_HE)
