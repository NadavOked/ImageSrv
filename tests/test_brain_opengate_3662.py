"""#3662 (S27, B70): פסק אחד של המוח לפני פתיחת סבב (`server/brain/opengate.py`), ו-`room.open_round` מציית.

מה נבדק:

- **ההכרעה (`combine`):** `BLOCK` מנצח; אחרת `WARN`; אחרת `UNDECIDED` (שער שלא הצליח לבדוק אינו `OPEN` וגם אינו `BLOCK`); `OPEN` רק כשכולם
  `OPEN`. הסיבה היא הסיבה של הקוד הקיים, מילה במילה.
- **צל (ברירת המחדל) = התנהגות של היום:** פסק `BLOCK` לא משנה דבר בפתיחה; הוא נרשם אחרי הפתיחה עם `legacy=opened` ו-`agreement=no`.
  אין קריאה מסונכרנת של שערים לפני הפתיחה.
- **`enforce` (כבוי, ממומש):** `BLOCK` מסרב לפתוח (409, הסיבה בטקסט, שום סבב לא נוצר); `WARN`, `UNDECIDED` וקריאה שנכשלה פותחים. רק
  שערי `pre_open` נקראים לפני הפתיחה.
- **המתג:** רק `enforce` בדיוק; כל ערך אחר = צל. `brain_veto_mode=off` = לא נקרא ולא נרשם.
- **התיעוד:** הכלל, הפעולה והמתג מופיעים ב-`docs/architecture/interfaces.md`. (הפריט B70 בקטלוג נבדק ב-`test_brain_catalog`.)

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.db import set_setting
from server.brain import gates, memory, opengate, say, veto
from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN, Reading
from test_server_room import (  # noqa: F401 -- room_server הוא fixture
    CLONER1, CLONER2, room_body, room_server,
)

ROOT = Path(__file__).resolve().parent.parent
RID = "room_test3662"
MAC1 = "aa:bb:cc:00:00:31"
RAID_HE = "מערך md0 אינו פעיל"


def gate_of(gid: str, title: str = "שער לבדיקה", pre_open: bool = True):
    return gates.Gate(gid, gid.lower(), title, lambda c, m, r: [], pre_open=pre_open)


def pairs(*specs):
    """``("B49", BLOCK, "סיבה")`` -> זוגות (שער, קריאה) כמו ש-`gates.run` מחזיר."""
    return [(gate_of(gid, f"שער {gid}"), Reading(verdict, reason)) for gid, verdict, reason in specs]


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "opengate.db")
    yield c
    veto.forget()


def only_gate(monkeypatch, read, *, pre_open=True, gate_id="B49"):
    monkeypatch.setattr(gates, "GATES", (gates.Gate(gate_id, "raid", "RAID של השרת", read, pre_open=pre_open),))


def open_round_http(rs, n=2):
    return rs["deploy"].post("/api/console/room", json=room_body(rs, n, {CLONER1: ["S1", "S2"][:n]}))


def rounds(ctx) -> int:
    return ctx.conn.execute("SELECT COUNT(*) FROM room_rounds").fetchone()[0]


def verdict_rows(ctx):
    return memory.decisions(ctx.conn, rule=opengate.RULE, limit=50)


# --- ההכרעה ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("specs, expected", [
    pytest.param([("B49", BLOCK, "x"), ("B51", WARN, "y"), ("B53", OPEN, "z")], BLOCK, id="block-beats-warn"),
    pytest.param([("B49", BLOCK, "x"), ("B51", UNDECIDED, "y")], BLOCK, id="block-is-not-hidden-by-an-unread-gate"),
    pytest.param([("B49", WARN, "x"), ("B51", UNDECIDED, "y"), ("B53", OPEN, "z")], WARN, id="warn-beats-undecided"),
    pytest.param([("B49", UNDECIDED, "x"), ("B51", OPEN, "y")], UNDECIDED, id="an-unread-gate-is-not-open"),
    pytest.param([("B49", OPEN, "x"), ("B51", OPEN, "y")], OPEN, id="all-open"),
    pytest.param([], UNDECIDED, id="no-gate-answered"),
])
def test_the_single_verdict(specs, expected):
    got, reason, evidence = opengate.combine(pairs(*specs))
    assert got == expected and reason.strip() and evidence, (got, reason)


def test_the_unread_is_never_open_and_never_block():
    got, reason, _e = opengate.combine(pairs(("B49", UNDECIDED, "לא הצלחנו לבדוק (RAID של השרת): ZeroDivisionError")))
    assert got == UNDECIDED and got not in (OPEN, BLOCK) and "לא הצלחנו לבדוק" in reason


def test_the_reason_is_the_existing_text_with_the_gate_named_and_long_lists_are_counted():
    _v, reason, _e = opengate.combine(pairs(("B49", BLOCK, RAID_HE)))
    assert reason == f"פתיחת הסבב חסרת תוחלת או מסוכנת: שער B49: {RAID_HE}"
    _v, many, evidence = opengate.combine(pairs(*[(f"B{n}", WARN, f"ממצא {n}") for n in range(40, 46)]))
    assert many.count("ממצא") == opengate.SHOWN and many.endswith(f"ועוד {6 - opengate.SHOWN}")
    assert "count_warn=6" in evidence                                  # הספירה המלאה נשמרת בראיות, לא רק מה שבטקסט


def test_the_evidence_names_the_worst_verdict_of_each_gate():
    g = gate_of("B54", "כרטיס")
    _v, _r, evidence = opengate.combine([(g, Reading(OPEN, "a")), (g, Reading(WARN, "b")), (g, Reading(OPEN, "c"))])
    assert "B54=WARN" in evidence and "count_open=2" in evidence


# --- המתג ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("enforce", opengate.ENFORCE), ("shadow", opengate.SHADOW), (None, opengate.SHADOW), ("", opengate.SHADOW),
    ("ENFORCE", opengate.SHADOW), ("true", opengate.SHADOW), ("on", opengate.SHADOW), ("garbage", opengate.SHADOW),
])
def test_only_the_exact_word_enforce_turns_the_gate_on(conn, raw, expected):
    if raw is not None:
        set_setting(conn, opengate.SETTING, raw)
    assert opengate.mode(conn) == expected


def test_an_unreadable_switch_is_shadow(conn, monkeypatch):
    monkeypatch.setattr(opengate, "get_setting", lambda c, k: 1 / 0)
    assert opengate.mode(conn) == opengate.SHADOW


def test_the_switch_is_not_writable_from_the_console():
    from server.console import api                                                  # noqa: PLC0415

    assert opengate.SETTING not in api.WRITE_SETTINGS


# --- `gate`: מה קורה לפני הפתיחה ----------------------------------------------------------------------------


def boom(*_a):
    raise AssertionError("השער נקרא לפני הפתיחה")


def test_in_shadow_nothing_is_read_before_the_round_opens(conn):
    got = opengate.gate(conn, [MAC1], read=boom)
    assert (got.verdict, got.block) == (opengate.NOT_RUN, False) and memory.decisions(conn, limit=5) == []


def test_a_global_off_reads_nothing_even_in_enforce(conn):
    set_setting(conn, opengate.SETTING, opengate.ENFORCE)
    set_setting(conn, veto.SETTING, veto.OFF)
    got = opengate.gate(conn, [MAC1], read=boom)
    assert (got.verdict, got.block) == (opengate.NOT_RUN, False) and memory.decisions(conn, limit=5) == []


@pytest.mark.parametrize("verdict, blocks", [(BLOCK, True), (WARN, False), (UNDECIDED, False), (OPEN, False)])
def test_in_enforce_only_block_blocks(conn, verdict, blocks):
    set_setting(conn, opengate.SETTING, opengate.ENFORCE)
    got = opengate.gate(conn, [MAC1], read=lambda c, m, t: pairs(("B49", verdict, "סיבה מהקוד הקיים")))
    assert (got.verdict, got.block) == (verdict, blocks)
    assert got.he == f"הסבב לא נפתח: {got.reason}"
    assert ("סיבה מהקוד הקיים" in got.reason) == (verdict in (BLOCK, WARN))                  # רק פסק עם בעיה נושא את הסיבה של השער
    [d] = memory.decisions(conn, rule=opengate.RULE, limit=5)
    assert (d["mode"], d["point"], d["action"], d["verdict"]) == ("enforce", "open_round", "ROUND_OPEN", verdict)
    assert d["outcome"] == ("הסבב לא נפתח" if blocks else "הסבב נפתח")


def test_a_brain_that_fell_neither_blocks_nor_approves(conn):
    set_setting(conn, opengate.SETTING, opengate.ENFORCE)
    got = opengate.gate(conn, [MAC1], read=lambda c, m, t: 1 / 0)
    assert (got.verdict, got.block) == (UNDECIDED, False)


def test_the_gate_reads_the_roster_it_was_given(conn):
    set_setting(conn, opengate.SETTING, opengate.ENFORCE)
    seen = []
    opengate.gate(conn, ["x", "y"], read=lambda c, m, t: seen.append((m, t)) or pairs(("B49", OPEN, "ok")), timeout_s=3.0)
    assert seen == [(("x", "y"), 3.0)]


# --- אחרי הפתיחה: מה שנרשם ב-shadow -------------------------------------------------------------------------


def test_the_after_open_row_says_what_the_old_code_did_and_whether_the_brain_agrees(conn):
    row = opengate.record_after_open(conn, RID, pairs(("B49", BLOCK, RAID_HE), ("B53", OPEN, "ok")))
    [d] = memory.decisions(conn, round_id=RID, limit=5)
    assert d["id"] == row and (d["rule"], d["action"], d["point"], d["mode"], d["verdict"]) == ("B70", "ROUND_OPEN", "after_open", "shadow", BLOCK)
    assert (d["entity_kind"], d["entity_key"]) == ("round", RID)
    assert {"legacy=opened", "agreement=no", "B49=BLOCK", "B53=OPEN", "gates=2"} <= set(d["evidence"])
    assert RAID_HE in d["reason"] and "המוח היה חוסם" in d["outcome"]
    opengate.record_after_open(conn, "room_other", pairs(("B49", WARN, "x")))
    [w] = memory.decisions(conn, round_id="room_other", limit=5)
    assert "agreement=yes" in w["evidence"] and "היה חוסם" not in w["outcome"]


def test_no_readings_means_no_row_that_is_the_global_off(conn):
    assert opengate.record_after_open(conn, RID, []) is None and memory.decisions(conn, round_id=RID, limit=5) == []


def test_a_recorder_that_fails_does_not_raise(conn, monkeypatch):
    monkeypatch.setattr(opengate.memory, "record", lambda *a, **k: 1 / 0)
    assert opengate.record_after_open(conn, RID, pairs(("B49", OPEN, "ok"))) is None


def test_the_line_in_hebrew_names_the_action_and_the_verdict():
    line = say.line_he({"entity_kind": "round", "entity_key": RID, "action": "ROUND_OPEN", "verdict": BLOCK, "reason": "x", "mode": "shadow"})
    assert line.startswith(f"סבב {RID} → פסק פתיחת הסבב: חסימה") and "ROUND_OPEN" not in line, line


# --- הקריאה לפני הפתיחה: רק שערי pre_open ------------------------------------------------------------------


def test_the_pre_open_gates_are_exactly_the_documented_seven():
    assert sorted(g.id for g in gates.GATES if g.pre_open) == ["B23", "B49", "B51", "B53", "B54", "B57", "B60"]
    assert sorted(g.id for g in gates.GATES if not g.pre_open) == ["B52", "B55", "B56"]     # אלה קוראים את שורת `room_rounds`


def test_read_pre_open_skips_the_gates_that_need_the_round_row_and_gives_them_an_empty_round_id(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(gates, "GATES", (
        gates.Gate("B49", "raid", "RAID", lambda c, m, r: seen.append(("B49", r)) or [Reading(OPEN, "ok")], pre_open=True),
        gates.Gate("B55", "questions", "שאלות", lambda c, m, r: seen.append(("B55", r)) or [Reading(OPEN, "ok")]),
    ))
    got = gates.read_pre_open(conn, [MAC1, MAC1, ""])
    assert [(g.id, r.verdict) for g, r in got] == [("B49", OPEN)] and seen == [("B49", "")]


def test_read_pre_open_turns_a_gate_that_did_not_answer_into_undecided(conn, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: 1 / 0)
    [(g, r)] = gates.read_pre_open(conn, [MAC1])
    assert (g.id, r.verdict) == ("B49", UNDECIDED)


# --- החיבור ל-open_round -------------------------------------------------------------------------------------


def test_in_shadow_a_blocking_verdict_changes_nothing_and_is_recorded_after_the_open(room_server, monkeypatch):
    """ההתנהגות של היום: אותה תשובה, אותו סבב, ושורה אחת שאומרת שהמוח **היה** חוסם."""
    only_gate(monkeypatch, lambda c, m, r: [Reading(BLOCK, RAID_HE)])
    ctx = room_server["ctx"]
    opened = open_round_http(room_server)
    assert opened.status_code == 200, opened.text
    assert gates.wait_idle(10.0)
    assert rounds(ctx) == 1
    [d] = verdict_rows(ctx)
    assert (d["verdict"], d["mode"], d["point"], d["round_id"]) == (BLOCK, "shadow", "after_open", opened.json()["id"])
    assert {"legacy=opened", "agreement=no"} <= set(d["evidence"]) and d["line_he"].endswith("(צל: רק נרשם)")


def test_in_shadow_an_open_verdict_opens_the_same_way(room_server, monkeypatch):
    only_gate(monkeypatch, lambda c, m, r: [Reading(OPEN, "תקין")])
    ctx = room_server["ctx"]
    opened = open_round_http(room_server)
    assert opened.status_code == 200 and set(opened.json()) == {"id", "wave_session_id"}
    assert gates.wait_idle(10.0)
    [d] = verdict_rows(ctx)
    assert (d["verdict"], d["evidence"].count("agreement=yes")) == (OPEN, 1)


def test_off_records_no_verdict_at_all(room_server, monkeypatch):
    set_setting(room_server["ctx"].conn, veto.SETTING, veto.OFF)
    only_gate(monkeypatch, lambda c, m, r: [Reading(BLOCK, RAID_HE)])
    assert open_round_http(room_server).status_code == 200
    assert gates.wait_idle(10.0)
    assert verdict_rows(room_server["ctx"]) == []


def test_in_enforce_a_blocking_verdict_refuses_with_the_reason_and_opens_nothing(room_server, monkeypatch):
    ctx = room_server["ctx"]
    set_setting(ctx.conn, opengate.SETTING, opengate.ENFORCE)
    only_gate(monkeypatch, lambda c, m, r: [Reading(BLOCK, RAID_HE)])
    refused = open_round_http(room_server)
    assert refused.status_code == 409, refused.text
    assert RAID_HE in refused.json()["detail"] and refused.json()["detail"].startswith("הסבב לא נפתח:")
    assert gates.wait_idle(10.0)
    assert rounds(ctx) == 0
    assert ctx.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0          # חריץ הסבב לא נתפס, ולא נשלח WoL
    [d] = verdict_rows(ctx)
    assert (d["verdict"], d["mode"], d["point"], d["round_id"]) == (BLOCK, "enforce", "open_round", None)
    assert d["outcome"] == "הסבב לא נפתח"
    again = open_round_http(room_server)                                                  # אותו מצב: אותה תשובה, בלי תופעות לוואי
    assert again.status_code == 409 and rounds(ctx) == 0


@pytest.mark.parametrize("reading", [
    pytest.param(Reading(OPEN, "תקין"), id="open"),
    pytest.param(Reading(WARN, "מערך פגוע"), id="warn"),
    pytest.param(Reading(UNDECIDED, "לא הצלחנו לבדוק"), id="undecided"),
])
def test_in_enforce_what_is_not_block_opens(room_server, monkeypatch, reading):
    ctx = room_server["ctx"]
    set_setting(ctx.conn, opengate.SETTING, opengate.ENFORCE)
    only_gate(monkeypatch, lambda c, m, r: [reading])
    opened = open_round_http(room_server)
    assert opened.status_code == 200, opened.text
    assert gates.wait_idle(10.0)
    assert rounds(ctx) == 1
    before = [d for d in verdict_rows(ctx) if d["point"] == "open_round"]
    assert [(d["verdict"], d["mode"]) for d in before] == [(reading.verdict, "enforce")]


def test_in_enforce_a_gate_that_crashed_does_not_block_and_is_not_recorded_as_open(room_server, monkeypatch):
    ctx = room_server["ctx"]
    set_setting(ctx.conn, opengate.SETTING, opengate.ENFORCE)
    only_gate(monkeypatch, lambda c, m, r: 1 / 0)
    assert open_round_http(room_server).status_code == 200
    assert gates.wait_idle(10.0)
    [before] = [d for d in verdict_rows(ctx) if d["point"] == "open_round"]
    assert before["verdict"] == UNDECIDED and "לא הצלחנו לבדוק" in before["reason"]


def test_in_enforce_a_gate_that_needs_the_round_row_is_not_read_before_the_open(room_server, monkeypatch):
    ctx = room_server["ctx"]
    set_setting(ctx.conn, opengate.SETTING, opengate.ENFORCE)
    only_gate(monkeypatch, lambda c, m, r: [Reading(BLOCK, "קורא את שורת הסבב")], pre_open=False, gate_id="B52")
    assert open_round_http(room_server).status_code == 200            # B52 אינו pre_open: לא נקרא לפני הפתיחה, ולכן לא חוסם אותה
    assert gates.wait_idle(10.0)
    assert rounds(ctx) == 1 and [d["verdict"] for d in verdict_rows(ctx) if d["point"] == "open_round"] == [UNDECIDED]


def test_the_invariants_still_refuse_first_and_the_brain_is_not_asked(room_server, monkeypatch):
    """דיסק אדום / אימג' שלא נכנס נשארים אינווריאנטים: הם מסרבים לפני הפסק, גם ב-`enforce`, והמוח לא נקרא."""
    ctx = room_server["ctx"]
    set_setting(ctx.conn, opengate.SETTING, opengate.ENFORCE)
    called = []
    only_gate(monkeypatch, lambda c, m, r: called.append(1) or [Reading(OPEN, "ok")])
    bad = room_body(room_server, 2, {CLONER1: ["S1", "S2"]})
    bad["target_slots"] = [{"mac": CLONER2, "ports": [99]}]                               # חריץ שאינו קיים: סירוב של `_validate_target_slots`
    refused = room_server["deploy"].post("/api/console/room", json=bad)
    assert refused.status_code in (400, 409), refused.text
    assert called == [] and rounds(ctx) == 0 and verdict_rows(ctx) == []


# --- התיעוד ---------------------------------------------------------------------------------------------------


def test_the_rule_and_the_action_are_in_the_interfaces_doc():
    # הקטלוג עצמו (`server/brain/*.toml`) פרטי, וטסט ציבורי אינו נוקב בו (`test_publish_to_public`): `test_brain_catalog` בודק שהפריט B70 מצביע על קוד קיים.
    doc = (ROOT / "docs/architecture/interfaces.md").read_text(encoding="utf-8")
    assert opengate.RULE == "B70" and opengate.ACTION == "ROUND_OPEN" and "B70" in doc and "ROUND_OPEN" in doc and opengate.SETTING in doc
