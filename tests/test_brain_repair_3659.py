"""#3659 (אפיק #3653): ניתוב ההשלמה -- multicast או HTTP (S10), כמה סבבי השלמה (S11) ו"איטי משלים, לא נכשל" (S12), כלל K-REPAIR -- המוח מחליט
(`server/brain/repairrule.py`), `rounds/repair.py` ו-`rounds/chunksend.py` מבצעים.

- **ההתנהגות זהה לזו של לפני #3659.** `OLD_SLOW` הוא העתק מילולי של `repair.slow_reason` בבסיס (`lab/vm-1114f`, `13828c0e4`), בתור אורקל בטסט בלבד.
  הטסטים של #2533 (`test_chunk_repair_2533`, `test_chunksend_2533`) לא השתנו מעבר להחלפת שם הקבוע, ובודקים את הביצוע.
- **`THRESHOLD` ו-`MAX_REPAIRS` הוסרו מהקוד** (לא אליאסים): הערכים (2, 5) הם `repairrule.MULTICAST_MIN` ו-`MAX_ROUNDS`, החלטה של המוח.
- **"דיסק שחסר אחרי המכסה נכשל" נשאר** (עיקרון 5): המוח קובע את המכסה, ואין "כמעט".
- **כל תוכנית השלמה נרשמת** ב-`brain_decisions` עם הראיות. `off` מחליט ואינו רושם. לא הודלק `brain_peer_mode` ולא `brain_carousel`.

**בקרה שלילית:** בבסיס אין `repairrule.py` והסף והמכסה קבועים ב-`repair.py`. הטסטים של המודול נופלים ב-`assert repairrule is not None`, ושל המנוע ב-assert על `brain_decisions` ריק.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import silence
from server.rounds import chunksend, repair

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import repairrule
except ImportError:                     # pragma: no cover
    repairrule = None

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: E402,F401 -- rig is a fixture

REPO = Path(__file__).resolve().parent.parent
OLD_THRESHOLD, OLD_MAX = 2, 5


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


@pytest.fixture(autouse=True)
def _fresh_rule_registers():
    silence.forget()
    yield
    silence.forget()


def decisions(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K-REPAIR' AND point = 'S10/S11/S12' ORDER BY id")]


def OLD_SLOW(taken, skipped, asked_mbps, floor_mbps):
    """`repair.slow_reason` בבסיס, מילה במילה."""
    if not skipped or not asked_mbps or not floor_mbps or taken < 0:
        return None
    wrote = asked_mbps * taken / (taken + skipped)
    if wrote >= floor_mbps:
        return None
    return (f"הדיסק פיגר בשידור הראשון: כתב בכ-{wrote:.0f}MB/s ({skipped} מתוך {taken + skipped}"
            f" החלקים שהגיעו לא נכתבו כי הוא פיגר) — מתחת למהירות המינימלית לכונן ({floor_mbps}MB/s)."
            " הוא אינו נכשל: החלקים החסרים משלימים לו מהמגירה השכנה או מהשרת")


def test_the_module_exists_and_its_values_are_the_old_ones():
    assert repairrule is not None, "server/brain/repairrule.py חסר"
    assert (repairrule.MULTICAST_MIN, repairrule.MAX_ROUNDS) == (OLD_THRESHOLD, OLD_MAX)
    assert (repairrule.threshold(), repairrule.threshold(False), repairrule.threshold(True)) == (OLD_THRESHOLD, OLD_THRESHOLD, 1)


def test_the_cap_is_a_cap_not_an_almost():
    assert repairrule is not None
    assert [repairrule.exhausted(n) for n in range(0, 8)] == [n >= OLD_MAX for n in range(0, 8)]
    assert f"אחרי {OLD_MAX} ניסיונות השלמה (7 חלקים עדיין חסרים)" in repairrule.exhausted_text(7)


def test_slow_not_failed_is_exactly_what_repair_decided_before():
    assert repairrule is not None, "server/brain/repairrule.py חסר"
    checked = 0
    for taken in (-1, 0, 10, 4503, 10546):
        for skipped in (None, 0, 1, 3, 6043, 10000):
            for asked in (None, 0, 74.0, 188.0, 400.0):
                for floor in (None, 0, 50, 74):
                    want = OLD_SLOW(taken, skipped, asked, floor)
                    assert repairrule.slow_reason(taken, skipped, asked, floor) == want
                    assert repair.slow_reason(taken, skipped, asked, floor) == want      # `brain.joinwatch` (B41) reads it through here
                    checked += 1
    assert checked == 5 * 6 * 5 * 4


def test_the_plan_cuts_by_the_threshold_the_brain_gave():
    """חתיכה שחסרה לשני מחשבים משודרת לכולם (ומי שחסר לו גם מהמשותף מאזין, ואת שלו מוריד בסבב הבא); בהפצה ישירה (סף 1) הכול חוזר במולטיקאסט."""
    assert repairrule is not None
    durable = {"a": {"A1": [(0, 9)]}, "b": {"B1": [(0, 4), (7, 9)]}, "c": {"C1": [(0, 4), (7, 8)]}}
    shared = repair.plan(10, durable, repairs_done=1)
    assert [tuple(r) for r in shared.multicast] == [(5, 6)] and shared.fetch == {} and shared.receive == {"b", "c"}
    explicit = repair.plan(10, durable, repairs_done=1, threshold=repairrule.threshold(False))
    assert (explicit.multicast, explicit.fetch, explicit.receive) == (shared.multicast, shared.fetch, shared.receive)
    direct = repair.plan(10, durable, repairs_done=1, threshold=repairrule.threshold(True))
    assert [tuple(r) for r in direct.multicast] == [(5, 6), (9, 9)] and direct.fetch == {}
    alone = repair.plan(10, {"a": {"A1": [(0, 9)]}, "c": {"C1": [(0, 8)]}}, repairs_done=1)
    assert alone.multicast == [] and {m: [tuple(r) for r in rs] for m, rs in alone.fetch.items()} == {"c": [(9, 9)]}


def test_a_disk_still_missing_after_the_cap_is_failed_by_name_and_one_round_before_it_is_not():
    assert repairrule is not None
    durable = {"b": {"B1": [(0, 4)]}}
    assert repair.plan(10, durable, repairs_done=repairrule.MAX_ROUNDS - 1).failed == {}
    failed = repair.plan(10, durable, repairs_done=repairrule.MAX_ROUNDS).failed
    assert failed == {("b", "B1"): repairrule.exhausted_text(5)}


def test_the_old_constants_are_removed_from_the_code_not_aliased():
    assert not hasattr(repair, "THRESHOLD") and not hasattr(repair, "MAX_REPAIRS")
    for path in (REPO / "server" / "rounds" / n for n in ("repair.py", "chunksend.py")):
        code = re.sub(r'(?s)""".*?"""|#.*', "", path.read_text(encoding="utf-8"))
        assert not re.search(r"(?<![\w.])(THRESHOLD|MAX_REPAIRS)(?!\w)", code), path.name
        assert "repair.MAX_REPAIRS" not in code and "repair.THRESHOLD" not in code, path.name


def test_the_engine_records_the_decision_of_each_repair_round_with_its_evidence(rig, conn):
    engine = rig.make()
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})
    assert rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]}) == {"ok": True, "action": "receive", "pass": 2}
    # `record_plan` is two writes (`silence.record`, then `silence.settle`): the row is visible a moment before it has its outcome (#3757)
    assert wait_for(lambda: decisions(conn) and decisions(conn)[0]["outcome"] is not None), "the plan row was never settled"
    first = decisions(conn)[0]
    assert (first["verdict"], first["point"], first["action"], first["mode"], first["entity_kind"], first["entity_key"]) == (
        "PLAN", "S10/S11/S12", "REPAIR_PLAN", "shadow", "round", SID)
    for piece in ("multicast_min=2", "max_rounds=5", "direct=0", "machines=3", "multicast_chunks=2", "round=1", "part=3"):
        assert piece in first["evidence_json"], piece
    assert first["outcome"] == "התוכנית נמסרה לביצוע"
    # הסבב הבא, והסיום: ההחלטה האחרונה היא "אין מה להשלים"
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    rig.tell("b", 2, "ended", {"B1": ALL})
    rig.ask("c", 2, {"C1": [[0, 8]]})
    rig.ask("c", 3, {"C1": ALL})
    rig.ask("a", 1, {"A1": ALL})
    rig.ask("b", 2, {"B1": ALL})
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert [r["verdict"] for r in decisions(conn)][-1] == "DONE"


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


def test_the_rule_is_registered_where_the_console_and_the_forecast_look_for_it():
    from server.brain import forecast, say
    assert repairrule is not None
    assert repairrule.ACTION in say.ACTION_HE and {repairrule.PLAN, repairrule.DONE, repairrule.EXHAUSTED} <= set(say.VERDICT_HE)
    assert repairrule.RULE in say.EXECUTED and forecast.predicts(repairrule.RULE) is not None
