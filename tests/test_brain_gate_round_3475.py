"""#3475 (K3, קבוצה ד'): שני שערי הסבב והמפעיל לפני פתיחת סבב, בצל -- שאלות פתוחות ותור פס הייצור (B55), מוחזק / probe / F1 (B57).

הקלט הוא של השרת עצמו: סבב שנפתח דרך ה-API, hello של משכפלים, שאלה שנולדת מ-hello באתחול אחר (`interrupt.on_rejoin`), פס ייצור שנפתח
כי יש יותר דיסקים ממגירות, "מוחזק" שנכתב ב-`held.note`, probe שנשמר ב-`probe.record`, וסימון F1 שנכתב ב-`f1_marks.mark`. השער נקרא ישירות
(`GATES` ריק בזמן הפתיחה: B51 מריץ את ה-`collect` האמיתי של `/health`, ובטסטים של חדר זה 13 שניות של פסקי זמן לכל פתיחה).

**הכרעות שנקבעו כאן** (הנימוק בדוח ה-PR): B55 הוא `OPEN` על ראיה חיובית ברגע הפתיחה (שאלה אינה יכולה להיות בו מבנית) ו-`WARN` רק ב-race;
שורות שאלה של סבב אחר אינן אזהרה (רק ראיה); B57 הוא `WARN` למוחזק, לפריט ב-`probe.verdicts` ולדיסק מסומן F1, ו-`UNDECIDED` למה שלא נקרא.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server.agent import held as agent_held, probe as agent_probe
from server.brain import gate_round, gates, memory
from server.brain.gate_core import OPEN, UNDECIDED, WARN
from server.disks import f1_marks
from test_interrupt_question_2528 import interrupted, question                 # noqa: F401 -- helpers, לא טסטים
from test_prodline_3181 import _nine, _open                                    # noqa: F401
from test_server_room import (                                                  # noqa: F401 -- room_server הוא fixture
    CLONER1, CLONER2, cloner_hello, room_body, room_server,
)

RID = "room_test3475"
PROBLEM = {"disk": "sda", "level": "r", "reason": "SMART נכשל"}


@pytest.fixture(autouse=True)
def no_registry_gates(monkeypatch):
    """הפתיחה אינה מריצה את עשרת השערים (B51 מריץ את `collect` האמיתי); השער הנבדק נקרא ישירות."""
    monkeypatch.setattr(gates, "GATES", ())


def open_round(rs, serials=("S1", "S2")) -> str:
    opened = rs["deploy"].post("/api/console/room", json=room_body(rs, len(serials), {CLONER1: list(serials)}))
    assert opened.status_code == 200, opened.text
    # #3620: `open_round` של השרת מפעיל את השערים ברקע (`gates.start`), ו-`gates.run` קורא את `GATES` רק כשהתהליכון מגיע ללולאה. בלי
    # ההמתנה, תהליכון רקע איטי היה קורא את `GATES` שהטסט הציב **אחרי** הפתיחה, ורושם שורה כפולה לשער הנבדק
    assert gates.wait_idle(10.0), "תהליכון הרקע של שערי הסבב לא הסתיים"
    return opened.json()["id"]


def one(readings):
    [reading] = readings
    assert reading.mac is None
    return reading


# --- B55: שאלות פתוחות ותור פס הייצור -----------------------------------------------------------------------


def test_a_round_just_opened_has_no_question_and_that_is_read_not_assumed(room_server):
    ctx = room_server["ctx"]
    rid = open_round(room_server)
    got = one(gate_round.questions(ctx.conn, (CLONER1,), rid))
    assert got.verdict == OPEN and got.reason == "אין שאלה פתוחה לאדם על הסבב", got
    assert {"asks=0", "stale_asks_of_other_rounds=0", "line=no", "queued=0"} == set(got.evidence)


def test_a_round_that_is_not_in_the_database_is_undecided_not_open(room_server):
    got = one(gate_round.questions(room_server["ctx"].conn, (), "room_nothere"))
    assert got.verdict == UNDECIDED and "לא נמצא" in got.reason and "room_nothere" in got.reason, got


def test_a_question_that_was_raised_is_warn_with_the_text_the_screen_shows(room_server):
    # race: הסבב כבר נוצר, ו-hello באתחול אחר הצליח להעלות שאלה לפני שהשער רץ
    ctx = room_server["ctx"]
    interrupted(room_server)
    [rid] = [r["id"] for r in ctx.conn.execute("SELECT id FROM room_rounds")]
    got = one(gate_round.questions(ctx.conn, (CLONER1,), rid))
    assert got.verdict == WARN
    assert got.reason == "המשכפל shich-1 כבה או אותחל באמצע הסבב. 2 דיסקים לא הושלמו." == question(room_server["deploy"])["text_he"]
    assert "asks=1" in got.evidence


def test_a_question_of_another_round_is_evidence_only_never_a_warn(room_server):
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    interrupted(room_server)
    closed = deploy.post("/api/console/room/close", json={"confirm_name": "Office 2024 Standard"})
    assert closed.status_code == 200, closed.text
    assert ctx.conn.execute("SELECT COUNT(*) FROM round_interrupts").fetchone()[0] == 1, "סגירה ידנית אינה מנקה את השאלה"
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    rid = open_round(room_server)
    got = one(gate_round.questions(ctx.conn, (CLONER1,), rid))
    assert got.verdict == OPEN and "stale_asks_of_other_rounds=1" in got.evidence and "asks=0" in got.evidence, got


def test_a_production_line_with_its_queue_is_open_and_says_how_many_wait(room_server):
    ctx = room_server["ctx"]
    _nine(room_server)
    assert _open(room_server["deploy"], 10).status_code == 200
    [rid] = [r["id"] for r in ctx.conn.execute("SELECT id FROM room_rounds")]
    got = one(gate_round.questions(ctx.conn, (CLONER1, CLONER2), rid))
    assert got.verdict == OPEN and got.reason.endswith("פס ייצור: 1 בתור, יעד 10 דיסקים"), got
    assert {"line=yes", "queued=1"} <= set(got.evidence)


def test_the_gate_is_registered_and_records_the_round_in_shadow(room_server, monkeypatch):
    ctx = room_server["ctx"]
    rid = open_round(room_server)
    monkeypatch.setattr(gates, "GATES", (gates.Gate("B55", "questions", "שאלות", gate_round.questions),))
    [(gate, reading)] = gates.run(ctx.conn, rid, [CLONER1])
    assert (gate.id, reading.verdict) == ("B55", OPEN)
    [d] = memory.decisions(ctx.conn, round_id=rid, rule="B55", limit=5)
    assert (d["verdict"], d["mode"], d["entity_kind"], d["point"]) == ("OPEN", "shadow", "round", "questions")


def test_a_slow_background_run_of_open_round_cannot_add_a_second_row_to_the_gate_the_test_patches_later(room_server, monkeypatch):
    """#3620: ההדמיה הדטרמיניסטית של הכשל שנראה פעם אחת על testrunner2: התהליכון של `gates.start` מתחיל מאוחר."""
    ctx = room_server["ctx"]
    real_run = gates.run

    def late_run(*args, **kwargs):
        time.sleep(0.5)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(gates, "run", late_run)
    rid = open_round(room_server)                       # starts the background run, which sleeps before it reads `GATES`
    monkeypatch.setattr(gates, "run", real_run)
    monkeypatch.setattr(gates, "GATES", (gates.Gate("B55", "questions", "שאלות", gate_round.questions),))
    gates.run(ctx.conn, rid, [CLONER1])
    assert gates.wait_idle(10.0)                        # whatever the background run does, it has done it by now
    rows = memory.decisions(ctx.conn, round_id=rid, rule="B55", limit=5)
    assert len(rows) == 1, [(d["id"], d["at"]) for d in rows]


# --- B57: מוחזק, probe ו-F1 ---------------------------------------------------------------------------------

GOOD_PROBE = {"power": {"on_battery": False}, "rtc": {"skew_seconds": 3}}


@pytest.fixture()
def cloners(room_server):
    """שני משכפלים שדיווחו hello (דיסקים S1,S2 ו-T1) ושה-probe שלהם תקין: המצב הנקי, שממנו כל טסט משנה דבר אחד."""
    ctx = room_server["ctx"]
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    cloner_hello(room_server["anon"], CLONER2, ["T1"])
    for mac in (CLONER1, CLONER2):
        agent_probe.record(ctx.conn, mac, agent_probe.well_formed(GOOD_PROBE))
    return ctx.conn


def read(conn, *macs):
    got = {r.mac: r for r in gate_round.held(conn, macs, RID)}
    assert set(got) == set(macs)
    return got


def test_clean_cloners_are_open_by_positive_evidence_on_all_three_sources(cloners):
    got = read(cloners, CLONER1, CLONER2)
    for mac in (CLONER1, CLONER2):
        assert got[mac].verdict == OPEN and "לא מוחזק, ה-probe וסימוני ה-F1 נקראו ותקינים" in got[mac].reason, got[mac]
        assert {"held=no", "f1=none disks=" + str(2 if mac == CLONER1 else 1)} <= set(got[mac].evidence)
        assert any(e.startswith("probe=ok sampled_at=") for e in got[mac].evidence)


def test_a_held_cloner_is_warn_with_the_precheck_wording_and_the_problem_disk(cloners):
    agent_held.note(cloners, CLONER1, [PROBLEM])
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER1].verdict == WARN
    assert got[CLONER1].reason == "shich-1 דלוק ומחכה ללחיצה על 'כבה' — יש בו דיסק עם בעיה מהסבב הקודם"
    assert "held=yes sda:r:SMART נכשל" in got[CLONER1].evidence
    assert got[CLONER2].verdict == OPEN, "מוחזק הוא של משכפל אחד"


def test_a_held_report_that_went_stale_is_not_held(cloners):
    agent_held.note(cloners, CLONER1, [PROBLEM], now=datetime.now(timezone.utc) - timedelta(seconds=agent_held.FRESH_SECONDS + 30))
    assert read(cloners, CLONER1)[CLONER1].verdict == OPEN


def test_a_probe_verdict_is_warn_in_the_words_of_the_machine_card(cloners):
    agent_probe.record(cloners, CLONER2, agent_probe.well_formed({**GOOD_PROBE, "power": {"on_battery": True}}))
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER2].verdict == WARN and got[CLONER2].reason == "shich-2: המחשב על סוללה — שיכפול/קליטה עלולים להיקטע", got[CLONER2]
    assert "probe:battery=err" in got[CLONER2].evidence and got[CLONER1].verdict == OPEN


def test_a_cloner_that_never_sent_a_probe_is_undecided_not_open(cloners):
    cloners.execute("DELETE FROM machine_probe WHERE mac = ?", (CLONER2,))
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER2].verdict == UNDECIDED and "ה-probe לא נקרא" in got[CLONER2].reason and "probe=none" in got[CLONER2].evidence
    assert got[CLONER1].verdict == OPEN


@pytest.mark.parametrize("probe", [{}, {"power": {"error": "no upower"}, "rtc": None}], ids=["nothing-reported", "only-errors"])
def test_a_probe_with_nothing_measured_is_undecided_an_empty_verdict_list_is_not_ok(cloners, probe):
    agent_probe.record(cloners, CLONER1, agent_probe.well_formed(probe), history=False)
    assert agent_probe.verdicts(agent_probe.latest(cloners, CLONER1)["probe"]) == []
    got = read(cloners, CLONER1)[CLONER1]
    assert got.verdict == UNDECIDED and "לא נמדד בו דבר" in got.reason and "probe=nothing_measured" in got.evidence


def test_a_disk_marked_f1_is_warn_and_names_the_disk(cloners):
    f1_marks.mark(cloners, "S2", f1_marks.MANUAL, "noc", CLONER1)
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER1].verdict == WARN
    assert got[CLONER1].reason.startswith("shich-1: נצפה F1 בהדלקה בדיסק Drawer SSD (S2) -- סומן 20")
    assert "f1:S2=manual" in got[CLONER1].evidence and got[CLONER2].verdict == OPEN


def test_an_f1_mark_on_a_disk_that_is_in_another_cloner_is_not_this_ones(cloners):
    f1_marks.mark(cloners, "T1", f1_marks.MANUAL, "noc", CLONER1)        # סומן כשהיה ב-1, והדיסק יושב עכשיו ב-2
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER1].verdict == OPEN and got[CLONER2].verdict == WARN


def test_f1_marks_that_could_not_be_read_are_undecided_for_everyone(cloners):
    cloners.execute("DROP TABLE disk_f1_marks")
    got = read(cloners, CLONER1, CLONER2)
    assert {m: r.verdict for m, r in got.items()} == {CLONER1: UNDECIDED, CLONER2: UNDECIDED}
    assert all("סימוני ה-F1 של הדיסקים לא נקראו" in r.reason and "f1=unread" in r.evidence for r in got.values())


def test_a_cloner_whose_disks_were_never_read_is_undecided_on_f1(cloners):
    cloners.execute("UPDATE net_devices SET disks_json = NULL WHERE mac = ?", (CLONER1,))
    got = read(cloners, CLONER1)[CLONER1]
    assert got.verdict == UNDECIDED and "אי אפשר לבדוק סימוני F1" in got.reason


def test_a_mac_the_server_has_no_row_for_is_undecided(cloners):
    got = read(cloners, "aa:bb:cc:00:00:99")["aa:bb:cc:00:00:99"]
    assert got.verdict == UNDECIDED and "held=unread" in got.evidence and "probe=none" in got.evidence, got


def test_one_broken_source_does_not_swallow_what_another_found(cloners):
    agent_held.note(cloners, CLONER1, [PROBLEM])
    cloners.execute("UPDATE net_devices SET disks_json = '{' WHERE mac = ?", (CLONER1,))        # JSON פגום: ה-F1 לא נקרא
    got = read(cloners, CLONER1, CLONER2)
    assert got[CLONER1].verdict == WARN and "דלוק ומחכה ללחיצה" in got[CLONER1].reason, got[CLONER1]
    assert "f1=error" in got[CLONER1].evidence and got[CLONER2].verdict == OPEN


def test_held_and_a_missing_probe_together_are_warn_and_the_probe_is_in_the_evidence(cloners):
    agent_held.note(cloners, CLONER2, [PROBLEM])
    cloners.execute("DELETE FROM machine_probe WHERE mac = ?", (CLONER2,))
    got = read(cloners, CLONER2)[CLONER2]
    assert got.verdict == WARN and "probe=none" in got.evidence and "ה-probe" not in got.reason


def test_the_gate_records_the_held_by_name_and_folds_the_clean_ones(cloners, monkeypatch):
    agent_held.note(cloners, CLONER1, [PROBLEM])
    monkeypatch.setattr(gates, "GATES", (gates.Gate("B57", "held", "מוחזק", gate_round.held),))
    gates.run(cloners, RID, [CLONER1, CLONER2])
    by = {(d["entity_kind"], d["entity_key"]): d for d in memory.decisions(cloners, round_id=RID, rule="B57", limit=10)}
    assert set(by) == {("machine", CLONER1), ("round", RID)}, by.keys()
    assert by[("machine", CLONER1)]["verdict"] == "WARN" and by[("round", RID)]["verdict"] == "OPEN"
    assert "נבדקו 1 משכפלים" in by[("round", RID)]["reason"] and by[("round", RID)]["evidence"] == [f"mac={CLONER2}"]
