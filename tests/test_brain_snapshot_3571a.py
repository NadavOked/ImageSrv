"""#3571 (פרוסה א): הצילום הקפוא של המוח -- הצורה, האיכות, מה נחשב התקדמות, והשחזור אחרי אתחול.

מה נבדק:
- **דיווחים כפולים:** אותו ``seq`` אינו התקדמות ואינו מקדם את הערך, אבל מעדכן ``alive_at`` (אות חיות נפרד).
- **דיווחים חסרים וישנים:** מדד שלא דווח הוא ``None`` ו-``missing`` (לא 0); דיווח ישן אינו חיים; מדד ישן אינו מצדיק החלטה.
- **אתחול:** איפוס מונה אחרי אתחול של המכונה אינו התקדמות; ואחרי אתחול של **השרת** הצילום וההפניה אליו נקראים מחדש.
- חמשת הנפחים נשארים נפרדים; אותו תוכן = אותו מזהה; תוצאה נקשרת לאותה שורה, ובלי מדידה ``matched`` נשאר NULL.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from server import db
from server.brain import memory, snapshot as S

T0 = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
DISK = S.resource("disk", "SER-1")
BOOT_A = "aaaaaaaa-0000-0000-0000-000000000000"
BOOT_B = "bbbbbbbb-0000-0000-0000-000000000000"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture()
def path(tmp_path):
    return tmp_path / "brain.db"


@pytest.fixture()
def conn(path):
    c = db.connect(path)
    yield c
    c.close()


def snap_of(ledger, now=10.0, event="tick", wave="w1", round_id="r1", rule_version=1):
    rows = [ledger.metric(DISK, name, "B", "agent:progress", at(now)) for name in S.VOLUMES]
    return S.freeze(rule="K-TEST", rule_version=rule_version, round_id=round_id, wave_id=wave, phase="wave", event=event,
                    taken_at=at(now), metrics=rows)


# --- 1. מה נחשב התקדמות ----------------------------------------------------------------------------------------------


def test_a_duplicate_report_is_not_progress_but_is_liveness():
    ledger = S.Ledger()
    assert ledger.observe(DISK, "written", 100, boot_id=BOOT_A, seq=10, at=at(0)) == "first"
    assert ledger.observe(DISK, "written", 300, boot_id=BOOT_A, seq=20, at=at(2)) == "progress"
    assert ledger.observe(DISK, "written", 300, boot_id=BOOT_A, seq=20, at=at(4)) == "duplicate"
    assert ledger.observe(DISK, "written", 999, boot_id=BOOT_A, seq=20, at=at(5)) == "duplicate"   # אותו seq, ערך אחר: לא מתקבל
    assert ledger.progress(DISK, "written") == 200
    assert ledger.alive_at(DISK, "written") == at(5)


def test_a_stale_report_is_neither_progress_nor_liveness():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 500, boot_id=BOOT_A, seq=50, at=at(0))
    assert ledger.observe(DISK, "written", 900, boot_id=BOOT_A, seq=40, at=at(9)) == "stale"
    assert ledger.progress(DISK, "written") == 0
    assert ledger.alive_at(DISK, "written") == at(0)
    assert ledger.metric(DISK, "written", "B", "x", at(1))["value"] == 500


def test_a_counter_reset_after_the_machine_booted_is_not_progress():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 5000, boot_id=BOOT_A, seq=10, at=at(0))
    ledger.observe(DISK, "written", 6000, boot_id=BOOT_A, seq=20, at=at(2))
    assert ledger.observe(DISK, "written", 40, boot_id=BOOT_B, seq=5, at=at(4)) == "reset"
    assert ledger.progress(DISK, "written") == 1000                     # לא -5960 ולא +40
    assert ledger.observe(DISK, "written", 90, boot_id=BOOT_B, seq=15, at=at(6)) == "progress"
    assert ledger.progress(DISK, "written") == 1050
    # דיווח מהאתחול הקודם שהתעכב ברשת: ישן, גם אם הערך שלו גדול
    assert ledger.observe(DISK, "written", 7000, boot_id=BOOT_A, seq=99, at=at(7)) == "stale"
    assert ledger.progress(DISK, "written") == 1050


def test_a_counter_that_fell_inside_one_boot_is_rebased_not_counted():
    ledger = S.Ledger()
    ledger.observe(DISK, "flushed", 800, boot_id=BOOT_A, seq=10, at=at(0))
    assert ledger.observe(DISK, "flushed", 100, boot_id=BOOT_A, seq=20, at=at(2)) == "regress"
    assert ledger.progress(DISK, "flushed") == 0
    assert ledger.observe(DISK, "flushed", 160, boot_id=BOOT_A, seq=30, at=at(4)) == "progress"
    assert ledger.progress(DISK, "flushed") == 60


def test_reports_without_boot_and_seq_are_ordered_by_value_only():
    ledger = S.Ledger()
    assert ledger.observe(DISK, "received", 10, at=at(0)) == "first"
    assert ledger.observe(DISK, "received", 10, at=at(1)) == "same"
    assert ledger.observe(DISK, "received", 25, at=at(2)) == "progress"
    assert ledger.progress(DISK, "received") == 15


def test_an_unreadable_value_is_not_a_report():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 10, boot_id=BOOT_A, seq=1, at=at(0))
    for junk in (None, True, float("nan"), "12", float("inf")):
        assert ledger.observe(DISK, "written", junk, boot_id=BOOT_A, seq=2, at=at(1)) == "stale"
    assert ledger.alive_at(DISK, "written") == at(0)


# --- 2. דיווחים חסרים: חוסר ראיה, לא אפס ------------------------------------------------------------------------------


def test_a_metric_that_was_never_reported_is_missing_not_zero():
    row = S.Ledger().metric(DISK, "verified", "B", "agent", at(10))
    assert row["value"] is None and row["quality"] == "missing" and row["age_s"] is None
    assert not S.usable(row)


def test_quality_is_ok_stale_or_clock_by_the_age_of_the_report():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 7, boot_id=BOOT_A, seq=1, at=at(0))
    assert ledger.metric(DISK, "written", "B", "a", at(3))["quality"] == "ok"
    old = ledger.metric(DISK, "written", "B", "a", at(60))
    assert old["quality"] == "stale" and old["value"] == 7 and old["age_s"] == 60 and not S.usable(old)
    future = S.metric(DISK, "written", 7, "B", "a", at(30), at(0))
    assert future["quality"] == "clock" and future["age_s"] is None and not S.usable(future)


def test_a_repeated_value_is_a_fresh_observation_but_a_duplicate_is_not():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 7, boot_id=BOOT_A, seq=1, at=at(0))
    ledger.observe(DISK, "written", 7, boot_id=BOOT_A, seq=2, at=at(8))            # זז seq, הערך אותו
    assert ledger.metric(DISK, "written", "B", "a", at(9))["age_s"] == 1
    ledger.observe(DISK, "written", 7, boot_id=BOOT_A, seq=2, at=at(30))           # כפילות
    assert ledger.metric(DISK, "written", "B", "a", at(31))["quality"] == "stale"
    assert ledger.alive_at(DISK, "written") == at(30)


def test_a_metric_needs_a_known_unit_and_a_real_resource():
    with pytest.raises(ValueError):
        S.metric(DISK, "written", 1, "MB", "a", at(0), at(1))
    with pytest.raises(ValueError):
        S.resource("cloner", "x")


# --- 3. הצורה ---------------------------------------------------------------------------------------------------------


def test_the_five_volumes_stay_five_separate_rows_and_the_ids_stay_apart():
    ledger = S.Ledger()
    for i, name in enumerate(S.VOLUMES):
        ledger.observe(DISK, name, 1000 * (i + 1), boot_id=BOOT_A, seq=1, at=at(0))
    snap = snap_of(ledger, now=1)
    assert [r["name"] for r in snap["metrics"]] == list(S.VOLUMES)
    assert [S.find(snap, "disk", "SER-1", n)["value"] for n in S.VOLUMES] == [1000, 2000, 3000, 4000, 5000]
    assert snap["round_id"] == "r1" and snap["wave_id"] == "w1" and snap["round_id"] != snap["wave_id"]
    assert snap["resources"] == [DISK] and snap["rule_version"] == 1 and S.find(snap, "machine", "SER-1", "written") is None


def test_the_same_content_is_the_same_id_and_anything_else_is_not():
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 5, boot_id=BOOT_A, seq=1, at=at(0))
    a = snap_of(ledger)
    assert a["id"] == snap_of(ledger)["id"]
    assert len({a["id"], snap_of(ledger, wave="w2")["id"], snap_of(ledger, rule_version=2)["id"],
                snap_of(ledger, event="phase")["id"], snap_of(ledger, now=11)["id"]}) == 5


# --- 4. שחזור: אחרי אתחול של השרת ------------------------------------------------------------------------------------


def test_the_snapshot_and_the_decision_that_used_it_survive_a_restart(path):
    ledger = S.Ledger()
    ledger.observe(DISK, "written", 5, boot_id=BOOT_A, seq=1, at=at(0))
    snap = snap_of(ledger, now=1)
    first = db.connect(path)
    sid = memory.save_snapshot(first, snap)
    assert sid == snap["id"] and memory.save_snapshot(first, snap) == sid          # פעמיים = פעם אחת
    did = memory.record(first, mode="shadow", rule="K-TEST", action="PLAN", verdict="HOLD", reason="x", orphans=False,
                        evidence=["a=1"], entity=("disk", "SER-1"), snapshot=sid, rule_version=1, round_id="r1")
    first.close()

    second = db.connect(path)                                                        # תהליך חדש: אין זיכרון בתהליך
    try:
        assert second.execute("SELECT COUNT(*) FROM brain_memory WHERE kind='snapshot'").fetchone()[0] == 1
        [row] = memory.decisions(second, rule="K-TEST")
        assert row["id"] == did and row["evidence"] == ["a=1"]                       # הקוראים הקיימים רואים ``evidence`` כמו שנכתב
        assert row["snapshot"]["id"] == sid and row["snapshot"]["rule_version"] == 1
        assert memory.snapshot_of(second, row) == snap                               # והצילום חוזר זהה, בייט לבייט
        assert [s["id"] for s in memory.snapshots(second, round_id="r1", wave_id="w1", rule="K-TEST")] == [sid]
        assert memory.snapshots(second, wave_id="w-other") == []
    finally:
        second.close()


def test_a_result_is_tied_to_the_same_row_and_without_a_measurement_it_is_not_a_success(conn):
    sid = memory.save_snapshot(conn, snap_of(S.Ledger()))
    did = memory.record(conn, mode="shadow", rule="K-TEST", action="PLAN", verdict="CUT", reason="x", orphans=False,
                        predicted=50.0, unit="MB/s", snapshot=sid, rule_version=1)
    later = memory.save_snapshot(conn, snap_of(S.Ledger(), now=20))
    assert memory.resolve(conn, [did], "הקוד הקיים בצע אחרת", performed="HOLD", result_snapshot=later) == 1
    [row] = memory.decisions(conn, rule="K-TEST")
    assert row["action"] == "PLAN" and row["result"]["performed"] == "HOLD" and row["result"]["snapshot"] == later
    assert row["matched"] is None and row["actual"] is None and row["predicted"] == 50.0          # הפניה אינה "הצליח"
    assert row["snapshot"]["id"] == sid and row["evidence"] == []
    assert memory.resolve(conn, [did], "שוב", performed="X") == 0                                # אינה נדרסת


def test_a_decision_without_a_snapshot_is_unchanged(conn):
    memory.record(conn, mode="shadow", rule="K-TEST", action="A", verdict="V", reason="r", orphans=False, evidence=["e"])
    [row] = memory.decisions(conn, rule="K-TEST")
    assert row["snapshot"] is None and row["result"] is None and row["evidence"] == ["e"]
    assert memory.snapshot_of(conn, row) is None


def test_a_pruned_snapshot_says_it_was_deleted_and_a_broken_one_says_unreadable(conn, monkeypatch):
    monkeypatch.setattr(memory, "_last_prune", time.monotonic())        # השמירה אינה גוזמת בעצמה: הגיזום הוא הקריאה למטה
    old = snap_of(S.Ledger())
    old["taken_at"] = (datetime.now(timezone.utc) - timedelta(days=memory.SNAPSHOT_DAYS + 5)).isoformat()
    old["id"] = "s1-old"
    memory.save_snapshot(conn, old)
    memory.record(conn, mode="shadow", rule="K-TEST", action="A", verdict="V", reason="r", orphans=False, snapshot="s1-old")
    assert memory.prune_snapshots(conn) == 1
    [row] = memory.decisions(conn, rule="K-TEST")
    assert memory.snapshot_of(conn, row) == {"missing": True, "id": "s1-old"}
    stamp = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO brain_memory (kind,key,source,rows,first_at,last_at,stats_json,updated_at)"
                 " VALUES ('snapshot','s1-bad','snapshot',1,?,?,'{not json',?)", (stamp, stamp, stamp))
    assert memory.load_snapshot(conn, "s1-bad") == {"unreadable": True}
    assert memory.load_snapshot(conn, "s1-nope") is None


def test_a_snapshot_is_not_an_entity(conn):
    memory.save_snapshot(conn, snap_of(S.Ledger()))
    assert all(memory.entities(conn, kind) == [] for kind in memory.KINDS)
    assert "snapshot" not in memory.KINDS
    json.dumps(memory.snapshots(conn))
