"""#3320: האינווריאנטים של המוח (I1–I19, `server/brain/invariants/README.md`), כבדיקות מבוססות מאפיינים.

**בלי ספרייה חדשה:** הצילומים האקראיים נבנים מ-`random.Random` עם זרע קבוע (``SEEDS``), כך שכל כשל משוחזר
בדיוק, והזרע מופיע בהודעה. I1–I5 ו-I18 ברמת המנוע כבר נבדקים ב-`test_brain_decide_3064.py` וב-
`test_brain_fixture_1311_3064.py`; כאן הם נבדקים שוב על צילומים אקראיים, ושאר האינווריאנטים נוספים.

**אינווריאנט שנכשל על הקוד הקיים הוא ממצא** ונשאר אדום: הוא מסומן ``xfail(strict=True)`` עם מספר ה-Issue, כך
שהתיקון יהפוך אותו לירוק-בלתי-צפוי ויחייב להסיר את הסימון. **I14** (#3331) הוא בסוף הקובץ: הוא עובר דרך השרת האמיתי (``POST /api/v1/agent/progress``), לא דרך
צילום, ולכן נשען על ה-fixture ``server``.

**בקרה שלילית:** כל אחד נבדק מול הפרה מכוונת בגוף ה-PR (למשל K2 שמחזיר PROCEED על ראיה חיה מפיל את I7).
"""

from __future__ import annotations

import inspect
import random
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from server.brain import budget, decide as brain, fill, integrity, partition, roundend
from server.brain.observe import ENDED, FETCHED, LISTENING, SERVED, WORK, Proposal, build

NOW = 1_800_000_000.0
CFG = brain.Config(suspect_s=30, lost_s=180, stalled_s=180, ceiling_s=1800)
SEEDS = range(300)
KINDS = (LISTENING, ENDED, FETCHED, SERVED)
RANK = {brain.LOST: 0, brain.SUSPECT: 1, brain.STALLED: 2, brain.ALIVE: 3}


def iso(age: float) -> str:
    return datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(timespec="seconds")


def world(seed: int, *, now: float = NOW):
    """צילום אקראי: 1-5 משכפלים, ראיות בגילים 0-600 ש', לפעמים חוש שלא נקרא, ופעולה הרסנית אחת על חלקם."""
    r = random.Random(seed)
    macs = [f"m{n}" for n in range(r.randint(1, 5))]
    members, ledger, engine = [], [], []
    for mac in macs:
        state = r.choice(["writing", "writing", "lost", "done"])
        members.append({"mac": mac, "state": state, "updated_at": iso(r.uniform(0, 600)) if r.random() > .1 else None,
                        "targets_json": f'[{{"dev": "sda", "serial": "{mac}-1", "state": "{state}"}}]'})
        if r.random() < .5:
            ledger.append({"mac": mac, "updated_at": iso(r.uniform(0, 600))})
        for _ in range(r.randint(0, 3)):
            engine.append((mac, r.choice(KINDS), r.uniform(0, 600), f"chunk-pass p{r.choice([3, 4])} pass 1"))
    targets = tuple(sorted(r.sample(macs, r.randint(1, len(macs)))))
    drawers = tuple((m, f"{m}-1") for m in targets if r.random() < .5)
    own = "readback" if r.random() < .1 else None
    proposal = Proposal("FAIL_WAVE", r.choice(["D15", "D16", "D18", "D19"]), targets, "t", drawers, own,
                        r.choice([0.0, 0.0, 100.0, 1799.0, 1800.0, 4000.0]))
    unknown = ("served_http",) if r.random() < .1 else ()
    snap = build(now=now, round_id="S", members=members, ledger=ledger, engine=engine, part=4,
                 proposals=(proposal,), unknown=unknown)
    return snap, members, ledger, engine, proposal, unknown


# --- I1, I2, I3 על צילומים אקראיים -------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I1_same_snapshot_same_decisions_and_hash_whatever_the_input_order(seed):
    snap, members, ledger, engine, proposal, unknown = world(seed)
    r = random.Random(-seed)
    again = build(now=NOW, round_id="S", members=r.sample(members, len(members)), ledger=r.sample(ledger, len(ledger)),
                  engine=r.sample(engine, len(engine)), part=4, proposals=(proposal,), unknown=unknown)
    assert again.digest() == snap.digest(), seed
    assert brain.decide(again, CFG) == brain.decide(snap, CFG), seed
    assert partition.decide(again, CFG) == partition.decide(snap, CFG), seed


@pytest.mark.parametrize("seed", SEEDS)
def test_I2_deciding_again_without_new_input_gives_nothing_new(seed):
    snap = world(seed)[0]
    first = brain.decide(snap, CFG)
    assert brain.decide(snap, CFG) == first, seed
    # המוח אינו יוזם: כל החלטת K2 היא שיפוט של פעולה שהוצעה, ולא פעולה חדשה
    assert [d.action for d in first if d.rule == "K2"] == [p.action for p in snap.proposals], seed


@pytest.mark.parametrize("seed", SEEDS)
def test_I3_removing_a_cloner_changes_no_decision_about_another(seed):
    snap, members, ledger, engine, proposal, unknown = world(seed)
    if len(members) < 2:
        return
    gone = members[-1]["mac"]
    keep = [m["mac"] for m in members if m["mac"] != gone]
    without = build(now=NOW, round_id="S", members=[m for m in members if m["mac"] != gone],
                    ledger=[x for x in ledger if x["mac"] != gone], engine=[e for e in engine if e[0] != gone],
                    part=4, unknown=unknown)
    before = {d.target: d for d in brain.decide(snap, CFG) if d.rule == "K1" and d.target in keep}
    after = {d.target: d for d in brain.decide(without, CFG) if d.rule == "K1"}
    assert before == after, seed


# --- I4 / I7 / I8: הווטו ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I4_an_action_on_a_done_disk_is_always_held(seed):
    snap, *_rest, proposal, _u = world(seed)
    done = {(d.mac, d.serial) for d in snap.drawers if d.state == "done"}
    k2 = [d for d in brain.decide(snap, CFG) if d.rule == "K2"][0]
    if not proposal.own_evidence and done & set(proposal.drawers):
        assert k2.verdict == brain.HOLD, seed


@pytest.mark.parametrize("seed", SEEDS)
def test_I7_no_destruction_on_a_guess(seed):
    """PROCEED על פעולה שנשענת על שתיקה = כל מי שהיא נוגעת בו LOST אמיתי (בלי ראיה בחלון ובלי חוש שלא נקרא),
    או שההחזקה הגיעה לתקרה, או שלכשל יש ראיה משלו. (#3836: חבר שדיווח done, FINISHED, אינו חוסם ואינו ניחוש.)"""
    snap, *_rest, proposal, _u = world(seed)
    k2 = [d for d in brain.decide(snap, CFG) if d.rule == "K2"][0]
    if k2.verdict != brain.PROCEED or proposal.own_evidence or proposal.held_s >= CFG.ceiling_s:
        return
    for mac in proposal.targets:
        state, why, _ev = brain.liveness(snap.cloner(mac), NOW, CFG, snap.unknown)
        want = "FINISHED" if snap.cloner(mac).state == "done" else brain.LOST     # #3836: done = FINISHED בלבד, אחרת LOST
        assert state == want, (seed, mac, state, why)


@pytest.mark.parametrize("seed", SEEDS)
def test_I8_K6_never_fails_and_lost_is_parked(seed):
    snap = world(seed)[0]
    d = partition.decide(snap, CFG, held_s=random.Random(seed).choice([0, 2000]))
    assert d.verdict in partition.VERDICTS, seed
    states = [brain.liveness(c, NOW, CFG, snap.unknown)[0] for c in snap.cloners]
    if states and all(s == brain.LOST for s in states):
        assert d.verdict == partition.PARK, seed


# --- I5 / I6 / I9: חיות ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I5_work_inside_the_window_is_never_lost_or_proceeded(seed):
    snap, *_rest, proposal, _u = world(seed)
    k2 = [d for d in brain.decide(snap, CFG) if d.rule == "K2"][0]
    for mac in proposal.targets:
        c = snap.cloner(mac)
        if c and any(e.kind in WORK and NOW - e.seen_at <= CFG.lost_s for e in c.evidence):
            assert brain.liveness(c, NOW, CFG)[0] != brain.LOST, seed
            if brain.liveness(c, NOW, CFG)[0] == "FINISHED":
                continue       # #3836 (הכרעת זאוס): done הוא מצב סופי שדווח, אינו חוסם את K2 גם עם ראיית עבודה בחלון
            if not proposal.own_evidence and proposal.held_s < CFG.ceiling_s:
                assert k2.verdict == brain.HOLD, seed


@pytest.mark.parametrize("seed", SEEDS)
def test_I6_silence_never_renews(seed):
    """בלי ראיה חדשה, הזמן רק מחמיר את מצב החיות: ALIVE → STALLED/SUSPECT → LOST, ולעולם לא בכיוון ההפוך."""
    snap = world(seed)[0]
    for c in snap.cloners:
        last = None
        for later in (0, 20, 40, 100, 200, 400, 800):
            state = brain.liveness(c, NOW + later, CFG)[0]
            if last is not None and state in RANK and last in RANK:
                assert RANK[state] <= RANK[last], (seed, c.mac, later, last, state)
            last = state


@pytest.mark.parametrize("wait", [60, 600, 3600, 86400])
def test_I9_waiting_in_the_servers_queue_is_never_suspect_or_lost(wait):
    """משכפל שממתין לתור ההורדות (K7) עדיין מדווח progress: ההמתנה של השרת אינה שתיקה שלו."""
    snap = build(now=NOW, round_id="S", members=[{"mac": "q", "state": "writing", "updated_at": iso(1),
                                                   "targets_json": None}],
                 ledger=[{"mac": "q", "updated_at": iso(wait)}], part=4)
    assert brain.liveness(snap.cloner("q"), NOW, CFG)[0] not in (brain.SUSPECT, brain.LOST)


# --- I10: סבב לא נסגר עם עבודה פתוחה (K10) ----------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I10_no_close_while_work_is_open(seed):
    r = random.Random(seed)
    cloners = [roundend.Cloner(f"m{n}", f"משכפל {n}", r.random() < .7, r.random() < .4, r.random() < .5,
                               r.choice([None, 5.0, 50.0])) for n in range(r.randint(1, 5))]
    close = [f for f in roundend.judge(cloners, 20) if f.action == roundend.ROUND_END]
    if any(c.fresh and not c.held for c in cloners):
        assert close == [], seed


# --- I11: לא-מאומת = כשל (K4) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I11_a_disk_that_is_not_done_is_never_reported_good(seed):
    r = random.Random(seed)
    targets = tuple(integrity.Target(f"m{n}", "sda", r.choice([None, f"S{n}"]),
                                     r.choice(["done", "failed", "writing", "verifying", "unknown"]),
                                     r.choice([None, "verified", "sampled", "failed"]),
                                     r.choice([None, (), ((4, r.randint(0, 3)),)]), r.random() < .2, r.random() < .2)
                    for n in range(r.randint(1, 6)))
    for t, d in zip(sorted(targets, key=lambda t: t.name), integrity.integrity(targets)):
        if t.state != "done":
            assert d.verdict not in (integrity.VERIFIED_OK, integrity.SAMPLED_OK), (seed, t, d)
        assert d.reason, seed


# --- I12 / I13: התקציב (K7) -------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I12_I13_the_budget_never_takes_the_broadcasts_share(seed):
    r = random.Random(seed)
    cfg = budget.Config(ceiling_bps=r.choice([100, 900, 2000]) * 10**6)
    rate = r.choice([None, r.uniform(0, 3e9)])
    s = budget.Sample(r.choice([None, 0, r.randint(1, 3 * 10**9)]), r.uniform(0, 1e9),
                      r.choice([None, 0, 5]), r.choice([None, 0, 5]), r.choice([None, 0.0, 0.5]), (),
                      r.choice([None, 0, 3]))
    d = budget.step(rate, cfg, s)
    assert d.rate_bps <= d.limit_bps, (seed, d)                         # I12: לעולם לא מעל מה שנשאר
    if s.broadcast_bps:
        assert d.limit_bps == max(cfg.ceiling_bps - s.broadcast_bps, cfg.floor), (seed, d)   # I13
    assert d.broadcast_bps == s.broadcast_bps, seed                     # התקציב אינו נוגע בקצב השידור


# --- I15: חלון חסד אחרי עליית השרת -------------------------------------------------------------------------------


def test_I15_no_destruction_in_the_grace_window_after_the_server_starts():
    """שרת שעלה עכשיו לא ראה אף משכפל: "אין ראיה" אצלו הוא "לא הסתכלתי", לא "שתק". 300 ש' (KT5) בלי הרסני.
    (#3330: היה xfail(strict) עד שהצילום נשא את רגע העלייה, `Snapshot.up_at`.)"""
    snap = build(now=NOW, round_id="S", members=[{"mac": "a", "state": "writing",
                                                         "updated_at": iso(10_000), "targets_json": None}],
                 proposals=(Proposal("FAIL_WAVE", "D16", ("a",), "t"),), part=4, up_at=NOW - 10)
    assert [d for d in brain.decide(snap, CFG) if d.rule == "K2"][0].verdict == brain.HOLD


@pytest.mark.parametrize("seed", SEEDS)
def test_I15_whatever_the_snapshot_a_silence_based_action_is_held_inside_the_window(seed):
    """על כל צילום אקראי: בתוך 300 ש' מהעלייה, פעולה בלי ראיה משלו ובלי תקרה מוחזקת; מחוץ לחלון -- ההחלטה של היום."""
    snap, *_rest, proposal, _u = world(seed)
    inside = replace(snap, up_at=NOW - 299.0)
    k2 = [d for d in brain.decide(inside, CFG) if d.rule == "K2"][0]
    if not proposal.own_evidence and proposal.held_s < CFG.ceiling_s:
        assert k2.verdict == brain.HOLD, seed
    outside = replace(snap, up_at=NOW - CFG.grace_s)
    assert brain.decide(outside, CFG) == brain.decide(snap, CFG), seed


# --- I16: כל החלטה עם סיבה --------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_I16_every_decision_has_a_rule_a_reason_and_its_evidence(seed):
    snap = world(seed)[0]
    for d in [*brain.decide(snap, CFG), partition.decide(snap, CFG)]:
        assert d.rule in ("K1", "K2", "K6") and d.reason.strip() and d.target, (seed, d)
        assert isinstance(d.evidence, tuple), seed


# --- I17: סוגי פעולות מותרים בלבד --------------------------------------------------------------------------------

#: כל הפסקים שהכללים של המוח מוציאים היום. סוג חדש נוסף כאן במודע, אחרי שנבדק מול I17.
ALLOWED = {
    "K1": {brain.ALIVE, brain.SUSPECT, brain.STALLED, brain.LOST, brain.UNKNOWN, "FINISHED"},   # FINISHED: #3836
    "K2": {brain.HOLD, brain.PROCEED},
    "K5": {fill.FEW, fill.MANY, fill.REPASS},
    "K6": set(partition.VERDICTS),
    "K7": {budget.OPEN, budget.CUT_, budget.RAISE, budget.HOLD, budget.CLAMP},
    "K10": {roundend.CLOSE, roundend.DEFER},
}


@pytest.mark.parametrize("seed", SEEDS)
def test_I17_the_rules_emit_only_the_allowed_kinds(seed):
    snap = world(seed)[0]
    for d in brain.decide(snap, CFG):
        assert d.verdict in ALLOWED[d.rule], (seed, d)
    assert partition.decide(snap, CFG).verdict in ALLOWED["K6"]


def test_I17_no_rule_chooses_by_the_number_of_cloners():
    """אף כלל טהור אינו מקבל את מספר המשכפלים כקלט (בחירה בין multicast ל-HTTP לפי ספירת מחשבים)."""
    for fn in (brain.decide, brain.liveness, partition.decide, fill.decide, budget.step, roundend.judge,
               integrity.integrity):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"cloners_count", "n_cloners", "cloner_count", "machines"}, fn.__qualname__


# --- I18 / I19: ה-fixtures (K6) -----------------------------------------------------------------------------------


def _1311(served: bool, age: float = 2, held_s: float = 0.0):
    snap = build(now=NOW, round_id="room_714f980d",
                 members=[{"mac": "cl2", "state": "writing", "updated_at": iso(age), "targets_json": None}],
                 engine=[("cl2", ENDED, 790, "chunk-pass p3 pass 2")],
                 served=[("cl2", SERVED, 5, "http-images")] if served else [], part=4)
    return partition.decide(snap, CFG, members=["cl2"], held_s=held_s)


def test_I18_the_1311_fixture():
    assert _1311(served=True).verdict == partition.HOLD_PARTITION               # (א)
    assert _1311(served=False).verdict == partition.HOLD                        # (ב)
    assert _1311(served=False, held_s=1800).verdict == partition.PARK            # (ב) אחרי 30 דק'
    assert _1311(served=False, age=181).verdict == partition.PARK                # (ג)


def test_I19_two_cloners_one_completing():
    snap = build(now=NOW, round_id="S2", members=[
        {"mac": "A", "state": "writing", "updated_at": iso(1), "targets_json": None},
        {"mac": "B", "state": "writing", "updated_at": iso(1), "targets_json": None}],
        engine=[("A", LISTENING, 2, "chunk-pass p4 pass 1"), ("B", FETCHED, 1, "fetching p3")],
        ledger=[{"mac": "B", "updated_at": iso(3)}], part=4)
    d = partition.decide(snap, CFG, members=["A", "B"], held_s=0)
    assert (d.verdict, d.target) == (partition.START, "A") and "continue_alone=B" in d.evidence
    # A אינו מחכה: גם אחרי החזקה ארוכה ההחלטה היא להתחיל, לא להמתין ל-B
    assert d == partition.decide(snap, CFG, members=["A", "B"], held_s=1799)


def test_the_random_world_covers_every_liveness_state_and_both_vetos():
    """בלי זה, אינווריאנט שנבדק רק "כש..." היה עובר בלי לבדוק דבר (עיקרון 5)."""
    states, verdicts = set(), set()
    for seed in SEEDS:
        snap = world(seed)[0]
        for d in brain.decide(snap, CFG):
            (states if d.rule == "K1" else verdicts).add(d.verdict)
    assert states == set(ALLOWED["K1"]) and verdicts == {brain.HOLD, brain.PROCEED}


# --- I14: דיווח ישן לא מחזיר אחורה (#3331) --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
def test_I14_a_late_progress_report_never_moves_the_state_back(seed, server, monkeypatch):
    """דיווחי התקדמות שמגיעים בסדר אחר (עיכוב ברשת) ושליחות חוזרות: המצב ב-DB הוא תמיד של הדיווח עם ה-`seq` הגבוה
    ביותר שהגיע עד כה, והיומן (`chunkledger.note_report`) אינו רואה דיווח ישן ממה שכבר ראה. ה-`seq` של דיווח שווה
    מתקבל (שליחה חוזרת). הזרע במזהה הסבב ובהודעה."""
    pytest.importorskip("fastapi")
    from server.rounds import chunkledger                                      # noqa: PLC0415
    from test_closed_session_report import _session                           # noqa: PLC0415

    boot = "5b1f6f0e-3c0a-4b52-9d4e-0d6a2f1c7e88"
    sid = f"ses_i14_{seed}"
    mac = _session(server, sid)
    seen_by_ledger: list[int] = []
    real = chunkledger.note_report
    monkeypatch.setattr(chunkledger, "note_report",
                        lambda ctx, who, body: seen_by_ledger.append(body["seq"]) or real(ctx, who, body))
    r = random.Random(seed)
    reports = [(n, 100 * n) for n in range(1, 8)]                              # (seq, bytes): both rise with time
    arrival = r.sample(reports, len(reports)) + [r.choice(reports) for _ in range(3)]
    highest = 0
    for seq, written in arrival:
        reply = server["anon"].post("/api/v1/agent/progress", json={
            "session_id": sid, "mac": mac, "state": "writing", "boot_id": boot, "seq": seq,
            "targets": [{"dev": "sda", "bytes_written": written, "bytes_total": 1000, "state": "writing"}]}).json()
        assert reply["ok"] is True and reply.get("stale", False) == (seq < highest), (seed, seq, highest, reply)
        highest = max(highest, seq)
        row = server["ctx"].conn.execute("SELECT bytes_written FROM session_members WHERE session_id = ?",
                                         (sid,)).fetchone()
        assert row["bytes_written"] == 100 * highest, (seed, seq, highest)
    assert seen_by_ledger == sorted(seen_by_ledger), (seed, seen_by_ledger)
