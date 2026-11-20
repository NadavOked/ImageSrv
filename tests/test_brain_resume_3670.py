"""#3670 (אפיק #3653, A32 + S16): ההחלטות של ההמשך אחרי כיבוי יושבות במוח; הסוכן ו-`chunksend` מוסרים עובדות ומבצעים.

**מה עבר, ובאילו מספרים (אף סף לא השתנה):**

1. התקרה של הקריאה החוזרת -- 10MB/s ועוד 60 שניות -- ישבה בסוכן. היא נאמרת עכשיו בתשובת `/agent/resume` (`readback`).
2. כמה זמן תיקח הקריאה החוזרת מחושב במוח מהמדידה הגולמית (`read_bytes`, `read_mbps`), באותה נוסחה בדיוק.
3. המעבר הראשון של מחיצה (`chunksend._first_pass`) הוא החלטה של `resume.first_pass`, ונרשמת.

**שקילות התנהגות:** הנוסחה הישנה והאלגוריתם הישן של המעבר הראשון מועתקים לכאן כמות שהם (`_old_*`), והטסטים משווים
אליהם על טווח קלטים -- מוח במצב צל מחליט בדיוק כמו הקוד שהוחלף.

**בקרה שלילית:** החזרת `resume.py` ו-`chunksend.py` לבסיס מפילה את כל הטסטים כאן בכשל התנהגותי -- הנוסחה ב-`resume` אינה
קיימת (`readback_seconds`), ותשובת `/agent/resume` אינה נושאת תקרה. הטסטים של הצד השני (`first_pass` מחליף את הגוף של
`_first_pass`) מנטרלים את `resume.first_pass` עם `raising=False`, כך שעל הבסיס הם נכשלים בהשוואה, לא ב-AttributeError.
"""

from __future__ import annotations

import random
import types

import pytest

pytest.importorskip("fastapi")

from server.brain import resume
from server.rounds import repair

from test_brain_resume_2528 import _ask, _interrupted, _opened, _rows, conn   # noqa: F401 -- conn is a fixture
from test_chunk_ledger_2533 import MANIFEST, SIZE
from test_chunksend_2533 import rig, wait_for                              # noqa: F401 -- rig is a fixture


def _old_readback_s(read_bytes: int, read_mbps: int) -> int:
    """`resume_round` before #3670, in the agent: `_ru_bytes / (_ru_r * 1000000) + 1` -- shell integer arithmetic."""
    return read_bytes // (read_mbps * 1000000) + 1


def _old_first_pass(disks, listening, held, chunks):
    """The body of `chunksend._first_pass` before #3670, copied: `disks` = (mac, serial) of the live listeners."""
    if not disks or set(listening) - {mac for mac, _s in disks}:
        return None
    need = []
    for mac, serial in disks:
        have = held(mac, serial)
        if not have:
            return None
        need = repair.union(need, repair.missing(chunks, have))
    if need != [(0, chunks - 1)]:
        return need
    return None


# --- 1. כמה זמן תיקח הקריאה החוזרת -----------------------------------------------------------------------------------


@pytest.mark.parametrize("read_bytes", [1, 4194304, 5 * 4194304, 76562087936, 18254 * 4194304])
@pytest.mark.parametrize("read_mbps", [1, 9, 10, 134, 210, 550])
def test_the_brain_computes_the_readback_seconds_exactly_as_the_agent_did(read_bytes, read_mbps):
    assert resume.readback_seconds(read_bytes, read_mbps) == _old_readback_s(read_bytes, read_mbps)


@pytest.mark.parametrize("read_bytes, read_mbps", [
    (None, 100), (100, None), (None, None), (0, 100), (100, 0), (-1, 100), (100, -5), (True, 100), (100, True),
    ("76562087936", 100), (100, "134"), ([], 100), (100, {}),
])
def test_a_measurement_that_is_missing_or_not_a_positive_number_is_not_a_readback_time(read_bytes, read_mbps):
    assert resume.readback_seconds(read_bytes, read_mbps) is None


def test_the_agents_own_readback_s_is_not_believed(conn):
    """הסוכן שולח עוד את `read_bytes` ו-`read_mbps`, אבל אם הגיע רק `readback_s` שחישב בעצמו -- אין מדידה גולמית, והחישוב
    אינו נעשה אצל מי שמבצע: להתחיל מחדש, בשם הסיבה (עיקרון 5)."""
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, ["aa:bb:cc:00:00:21"])
    got = resume.answer(conn, session_id="ses_2", image_id="img_aaaaaa", manifest=MANIFEST,
                        targets=[{"dev": "sda", "serial": "S1", "size_bytes": SIZE, "readback_s": 300}])
    assert got[0]["verdict"] == "restart" and "זמן הקריאה החוזרת לא נמדד" in got[0]["reason"], got


def test_a_raw_measurement_that_the_agent_sends_today_gives_the_same_verdict_as_its_own_seconds(conn):
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, ["aa:bb:cc:00:00:21"])
    for_300 = _ask(conn, readback_s=300)[0]
    assert for_300["verdict"] == "resume"
    assert any("readback_s=300" == e for e in _rows(conn)[0]["evidence"]), _rows(conn)[0]["evidence"]


# --- 2. התקרה שהמוח נותן -------------------------------------------------------------------------------------------------


def test_the_answer_carries_the_ceiling_with_the_numbers_the_agent_kept_and_the_decision_names_them(conn):
    _interrupted(conn)
    resume.snapshot(conn, {"id": "room_1", "image_id": "img_aaaaaa"}, ["aa:bb:cc:00:00:21"])
    got = _ask(conn)[0]
    assert got["readback"] == {"min_mbps": 10, "slack_s": 60}, got
    assert (resume.READBACK_MIN_MBPS, resume.READBACK_SLACK_S) == (10, 60), "an agent threshold moved, not changed (#3670)"
    evidence = _rows(conn)[0]["evidence"]
    assert "readback_min_mbps=10" in evidence and "readback_slack_s=60" in evidence, evidence
    assert resume.readback_limits() == {"min_mbps": 10, "slack_s": 60}


def test_a_restart_answer_carries_the_ceiling_too_so_the_shape_of_an_answer_does_not_depend_on_the_verdict(conn):
    got = _ask(conn)[0]                                   # no ledger kept: restart
    assert got["verdict"] == "restart" and got["readback"] == {"min_mbps": 10, "slack_s": 60}, got


# --- 3. המעבר הראשון -----------------------------------------------------------------------------------------------------


def _ranges(rnd: random.Random, chunks: int) -> list[tuple[int, int]]:
    """A normalised list (sorted, no overlap, no touching) of ranges inside [0, chunks-1], possibly empty."""
    marks = sorted(rnd.sample(range(chunks + 1), rnd.randint(0, min(chunks + 1, 6)) // 2 * 2))
    return [(marks[i], marks[i + 1] - 1) for i in range(0, len(marks), 2) if marks[i + 1] - 1 >= marks[i]]


def test_the_brains_first_pass_is_the_old_first_pass_on_a_thousand_random_cases():
    """שקילות: אותם דיסקים, אותו ledger -- אותו `need`, כולל None ו-[] -- על 1000 מקרים קבועים (seed), בלי DB."""
    rnd = random.Random(3670)
    seen = {"none": 0, "empty": 0, "partial": 0}
    for _ in range(1000):
        chunks = rnd.randint(1, 12)
        macs = ["a", "b", "c"][: rnd.randint(1, 3)]
        listening = [m for m in macs if rnd.random() < 0.9] or macs[:1]
        disks = [(m, f"{m.upper()}{k}") for m in macs if m in listening and rnd.random() < 0.9 for k in range(rnd.randint(1, 2))]
        held = {d: _ranges(rnd, chunks) for d in disks}
        want = _old_first_pass(disks, listening, lambda m, s: held.get((m, s), []), chunks)
        got = resume.first_pass(None, session_id="ses_2", part=3, chunks=chunks, listening=listening, disks=disks,
                                held=lambda m, s: held.get((m, s), [])).need
        assert got == want, (chunks, listening, disks, held)
        seen["none" if want is None else "empty" if want == [] else "partial"] += 1
    assert all(seen.values()), f"the cases must reach all three answers, or the equivalence proves little: {seen}"


def test_a_partial_first_pass_is_recorded_once_with_its_evidence_and_a_whole_one_is_not(conn):
    held = {("a", "A1"): [(0, 5)]}
    got = resume.first_pass(conn, session_id="ses_2", part=3, chunks=10, listening=["a"], disks=[("a", "A1")],
                            held=lambda m, s: held.get((m, s), []))
    assert got.need == [(6, 9)]
    rows = _rows(conn)
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["mode"], row["verdict"], row["point"], row["action"], row["round_id"], row["entity_kind"]) == \
        ("shadow", "RESUME", "chunksend.first_pass", "RESUME_OR_RESTART", "ses_2", "round")
    assert row["evidence"] == ["part=3", "send=4/10", "disks=1", "machines=1"], row["evidence"]
    assert row["outcome"] and "רק עם מה שחסר" in row["outcome"]
    assert "המעבר הראשון של מחיצה 3 משדר 4 מתוך 10" in row["reason"]

    # whole = today's default: no row (it is every partition of every ordinary wave)
    whole = resume.first_pass(conn, session_id="ses_2", part=4, chunks=10, listening=["a"], disks=[("a", "A1")], held=lambda m, s: [])
    assert whole.need is None and len(_rows(conn)) == 1
    nothing_held_by_one = resume.first_pass(conn, session_id="ses_2", part=5, chunks=10, listening=["a", "b"],
                                            disks=[("a", "A1"), ("b", "B1")], held=lambda m, s: [(0, 5)] if m == "a" else [])
    assert nothing_held_by_one.need is None and len(_rows(conn)) == 1


def test_the_first_pass_follows_the_switch_it_is_recorded_in_enforce_and_not_at_all_when_off(conn):
    from server import db
    held = lambda m, s: [(0, 5)]                                   # noqa: E731
    args = dict(session_id="ses_2", part=3, chunks=10, listening=["a"], disks=[("a", "A1")], held=held)
    db.set_setting(conn, resume.SETTING, "enforce")
    assert resume.first_pass(conn, **args).need == [(6, 9)] and _rows(conn)[0]["mode"] == "enforce"
    db.set_setting(conn, resume.SETTING, "off")
    off = resume.first_pass(conn, **{**args, "part": 4})
    assert off.need == [(6, 9)], "off does not change what is sent: the evidence is the read-back, not the switch"
    assert len(_rows(conn)) == 1, "off records nothing"


def test_a_first_pass_that_cannot_be_weighed_goes_out_whole_and_does_not_throw(conn):
    def broken(mac, serial):
        raise RuntimeError("the ledger's table is gone")
    got = resume.first_pass(conn, session_id="ses_2", part=3, chunks=10, listening=["a"], disks=[("a", "A1")], held=broken)
    assert got.need is None and "משודרת כולה" in got.reason and _rows(conn) == []


# --- 4. chunksend מבצע את תשובת המוח, ולא מחליט בעצמו ---------------------------------------------------------------------


def test_the_engine_sends_what_the_brain_answers_not_what_it_would_have_computed_itself(rig, monkeypatch):
    """ה-ledger אומר ש-0-5 עליו (והמעבר היה "6-9"). המוח עונה 0-1: זה מה שיוצא. הצד השני, מוח ששולח הכול -- הכול."""
    asked = []

    def brain(conn, **kw):
        asked.append((kw["part"], kw["chunks"], kw["listening"], kw["disks"]))
        return types.SimpleNamespace(need=[(0, 1)])

    monkeypatch.setattr(resume, "first_pass", brain, raising=False)
    rig.members = ["a"]
    rig.make()
    _opened(rig)
    assert rig.tell("a", 1, "listening", {"A1": [[0, 5]]})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.runner.lists[0] == "0-1", rig.runner.lists
    assert asked == [(3, 10, ["a"], [("a", "A1")])], asked
    assert rig.named("chunk_first_pass_from_ledger")


def test_a_brain_that_says_whole_means_a_whole_pass_whatever_the_ledger_holds(rig, monkeypatch):
    monkeypatch.setattr(resume, "first_pass", lambda conn, **kw: types.SimpleNamespace(need=None), raising=False)
    rig.members = ["a"]
    rig.make()
    _opened(rig)
    rig.tell("a", 1, "listening", {"A1": [[0, 5]]})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.runner.lists[0] is None, rig.runner.lists
    assert not rig.named("chunk_first_pass_from_ledger")
