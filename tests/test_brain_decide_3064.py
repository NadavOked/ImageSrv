"""#3064, שלב 1: `server/brain/decide.py` — פונקציה טהורה, על צילומים שנבנים כאן ביד.

(א) דטרמיניזם: אותו צילום → אותן החלטות ואותו hash, בלי תלות בסדר הקלט (I1).
(ב) האינווריאנטים I1–I5 ברמת ההחלטה. הטסטים של אותם אינווריאנטים **בנקודות ה-hook** עצמן (שבלעדיהן הם
    נופלים) נמצאים ב-test_brain_veto_3064.py.
ועוד: K1 לפי OR של ראיות, "לא ידוע" אינו "מת", כשל עם ראיה משלו אינו נחסם, ותקרה משחררת.
"""

from __future__ import annotations

import random

from server.brain import decide as brain
from server.brain.observe import FETCHED, LEDGER, LISTENING, Proposal, build

NOW = 1_800_000_000.0
CFG = brain.Config(suspect_s=30, lost_s=180, stalled_s=180, ceiling_s=1800)


def iso(age: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(timespec="seconds")


def member(mac: str, age: float, state: str = "writing", drawers: str = "") -> dict:
    return {"mac": mac, "state": state, "updated_at": iso(age), "targets_json": drawers or None}


def snap(members, ledger=(), engine=(), proposals=(), unknown=()):
    return build(now=NOW, round_id="S-1", members=members, ledger=ledger, engine=engine, part=4,
                 proposals=proposals, unknown=unknown)


def verdicts(decisions, rule="K1"):
    return {d.target: d.verdict for d in decisions if d.rule == rule}


def fail_wave(*targets, **kw):
    return Proposal("FAIL_WAVE", "D16", tuple(targets), "test", **kw)


# --- K1: liveness ----------------------------------------------------------------------------------------------


def test_k1_is_an_or_of_the_evidence():
    s = snap([member("a", 2), member("b", 60), member("c", 400), member("d", 400)],
             ledger=[{"mac": "d", "updated_at": iso(5)}])
    assert verdicts(brain.decide(s, CFG)) == {"a": brain.ALIVE, "b": brain.SUSPECT, "c": brain.LOST,
                                              "d": brain.ALIVE}   # d: the progress is old, the ledger is not


def test_engine_evidence_counts_and_work_that_stopped_is_stalled():
    s = snap([member("a", 400), member("b", 2)],
             engine=[("a", LISTENING, 3.0, "chunk-pass p4 pass 1"), ("b", FETCHED, 200.0, "fetching p3")])
    assert verdicts(brain.decide(s, CFG)) == {"a": brain.ALIVE, "b": brain.STALLED}


def test_3836_a_member_that_finished_is_not_lost_when_it_goes_quiet():
    s = snap([member("a", 400, state="done"), member("b", 400, state="failed"), member("c", 400, state="writing")])
    got = verdicts(brain.decide(s, CFG))
    assert got == {"a": "FINISHED", "b": brain.LOST, "c": brain.LOST}      # only the reported success is final


def test_a_sense_that_was_not_read_is_unknown_not_lost():
    s = snap([member("a", 400)], unknown=["chunk_ledger"])
    assert verdicts(brain.decide(s, CFG)) == {"a": brain.UNKNOWN}
    assert verdicts(brain.decide(snap([member("a", 400)]), CFG)) == {"a": brain.LOST}   # the control: read, and old


def test_a_member_marked_lost_is_not_alive_by_the_moment_it_was_marked():
    # `room._mark_lost` stamps `updated_at` when it marks: that is not a report of the machine
    s = snap([member("a", 0, state="lost")])
    assert verdicts(brain.decide(s, CFG)) == {"a": brain.LOST}


def test_the_thresholds_come_from_the_config_not_from_the_code():
    s = snap([member("a", 100)])
    assert verdicts(brain.decide(s, CFG)) == {"a": brain.SUSPECT}
    assert verdicts(brain.decide(s, brain.Config(suspect_s=30, lost_s=90))) == {"a": brain.LOST}
    assert verdicts(brain.decide(s, brain.Config(suspect_s=120))) == {"a": brain.ALIVE}


# --- K2: the veto's judgment ----------------------------------------------------------------------------------


def test_a_failure_with_evidence_of_its_own_is_never_held():
    s = snap([member("a", 1)], proposals=[Proposal("FAIL_DISKS", "D14", ("a",), "slow", (("a", "X"),),
                                                   own_evidence="2.1MB/s under 50MB/s for 600s")])
    assert brain.decide(s, CFG)[-1].verdict == brain.PROCEED


def test_the_ceiling_releases_a_hold():
    alive = [member("a", 1)]
    assert brain.decide(snap(alive, proposals=[fail_wave("a", held_s=1799)]), CFG)[-1].verdict == brain.HOLD
    assert brain.decide(snap(alive, proposals=[fail_wave("a", held_s=1800)]), CFG)[-1].verdict == brain.PROCEED


def test_silence_that_is_real_proceeds():
    s = snap([member("a", 400)], proposals=[fail_wave("a")])
    assert brain.decide(s, CFG)[-1].verdict == brain.PROCEED


# --- (א) + I1: determinism --------------------------------------------------------------------------------------


def test_I1_the_same_snapshot_gives_the_same_decisions_and_hash_whatever_the_input_order():
    members = [member(m, age) for m, age in (("a", 2), ("b", 50), ("c", 300), ("d", 10))]
    ledger = [{"mac": "c", "updated_at": iso(7)}, {"mac": "b", "updated_at": iso(90)}]
    engine = [("d", FETCHED, 1.0, "fetching p3"), ("a", LISTENING, 4.0, "chunk-pass p4 pass 1")]
    props = [fail_wave("a", "b"), Proposal("EXPIRE_LEDGER", "D21", ("c",), "lost")]
    first = snap(members, ledger, engine, props)
    rng = random.Random(3064)
    for _ in range(20):
        again = snap(rng.sample(members, len(members)), rng.sample(ledger, len(ledger)),
                     rng.sample(engine, len(engine)), props)
        assert again == first and again.digest() == first.digest()
        assert brain.decide(again, CFG) == brain.decide(first, CFG)


def test_I1_a_different_snapshot_has_a_different_hash():
    assert snap([member("a", 2)]).digest() != snap([member("a", 3)]).digest()


# --- (ב) I2–I5 -----------------------------------------------------------------------------------------------


def test_I2_the_brain_initiates_nothing_and_a_held_action_stays_held():
    # stage 1: without a proposal of the existing code there is no destructive decision at all
    s = snap([member("a", 400), member("b", 1)])
    assert {d.rule for d in brain.decide(s, CFG)} == {"K1"}
    # deciding again on the same snapshot (nothing new arrived) gives nothing new
    held = snap([member("a", 1)], proposals=[fail_wave("a")])
    assert brain.decide(held, CFG) == brain.decide(held, CFG)


def test_I3_removing_cloner_b_changes_no_decision_about_a():
    a, b = member("a", 2), member("b", 400)
    with_b = brain.decide(snap([a, b], proposals=[Proposal("EXPIRE_LEDGER", "D21", ("a",), "x")]), CFG)
    without = brain.decide(snap([a], proposals=[Proposal("EXPIRE_LEDGER", "D21", ("a",), "x")]), CFG)
    assert [d for d in with_b if d.target == "a"] == [d for d in without if d.target == "a"]


def test_I3_a_live_cloner_does_not_hold_an_action_on_another_one():
    # b is alive and writing; a is really silent. Removing a's ledger is not held because of b
    s = snap([member("a", 400), member("b", 1)], proposals=[Proposal("EXPIRE_LEDGER", "D21", ("a",), "x")])
    assert brain.decide(s, CFG)[-1].verdict == brain.PROCEED


def test_I3_a_drawer_that_did_not_fail_is_not_destroyed_because_of_another():
    # 13/11: the wave fails because p4 found no listener -- and the healthy drawer, at 96%, goes with it.
    # As long as the machine that holds it shows life, the wave's failure is held.
    drawers = '[{"dev": "sda", "serial": "OK1", "state": "writing"}, {"dev": "sdc", "serial": "BACK", "state": "failed"}]'
    s = snap([member("cloner2", 1, drawers=drawers)], proposals=[fail_wave("cloner2")])
    out = brain.decide(s, CFG)[-1]
    assert out.verdict == brain.HOLD and "cloner2=ALIVE" in out.reason


def test_I4_an_action_that_names_a_done_disk_is_held_even_past_the_ceiling():
    drawers = '[{"dev": "sda", "serial": "OK1", "state": "done"}]'
    s = snap([member("a", 400, drawers=drawers)],
             proposals=[Proposal("FAIL_DISKS", "D15", ("a",), "silent", (("a", "OK1"),), held_s=99_999)])
    out = brain.decide(s, CFG)[-1]
    assert out.verdict == brain.HOLD and "a/OK1" in out.reason


def test_I5_work_inside_the_window_is_not_failed():
    # silent on the chunk-pass channel for long, but the ledger moved 20 s ago: that is work, not silence
    s = snap([member("a", 400)], ledger=[{"mac": "a", "updated_at": iso(20)}],
             proposals=[Proposal("FAIL_DISKS", "D15", ("a",), "silent", (("a", "X"),))])
    assert brain.decide(s, CFG)[-1].verdict == brain.HOLD
    s = snap([member("a", 400)], engine=[("a", FETCHED, 20.0, "fetching p3")],
             proposals=[Proposal("FAIL_DISKS", "D15", ("a",), "silent", (("a", "X"),))])
    assert brain.decide(s, CFG)[-1].verdict == brain.HOLD


def test_every_decision_carries_a_reason():
    s = snap([member("a", 2), member("b", 400)], ledger=[{"mac": "b", "updated_at": iso(500)}],
             proposals=[fail_wave("a", "b"), Proposal("EXPIRE_LEDGER", "D21", ("b",), "lost")])
    assert all(d.reason for d in brain.decide(s, CFG))


def test_ledger_evidence_is_work_evidence():
    assert LEDGER in brain.WORK and FETCHED in brain.WORK


def test_3836_a_wave_where_everyone_finished_parks_at_once_and_k2_does_not_hold():
    from server.brain import partition
    s = snap([member("a", 400, state="done"), member("b", 400, state="done")], proposals=(fail_wave("a", "b"),))
    assert verdicts(brain.decide(s, CFG)) == {"a": "FINISHED", "b": "FINISHED"}      # the string, not the constant
    assert partition.decide(s, CFG, held_s=0).verdict == "PARK"                      # not HOLD until the 1800s ceiling
    assert set(verdicts(brain.decide(s, CFG), "K2").values()) == {"PROCEED"}          # K2 does not hold for finished members


def test_3836_k6_names_a_finished_member_as_finished_not_as_continuing_alone_over_http():
    from server.brain import partition
    s = snap([member("A", 1), member("B", 1, state="done")], engine=[("A", LISTENING, 2, "chunk-pass p4 pass 1")])
    d = partition.decide(s, CFG, members=["A", "B"], held_s=0)
    assert d.verdict == "START" and d.target == "A"
    assert "B סיים" in d.reason and "ממשיך לבד" not in d.reason
    assert "continue_alone=B" not in d.evidence
