"""#3064: שני כללים של המוח בצל -- עליית ה-CRC לכל סידורי (D29, LR11), ו-K4: כשל מקור מול כשל דיסק.

מה נבדק:
- CRC עלה מאז הסבב הקודם = WARN; אותו ערך = NO_INCREASE, גם כשהמונה המוחלט גבוה; אין בסיס = NO_BASELINE בשמו;
  מונה שלא נקרא = UNREAD; ירד = COUNTER_RESET. הבסיס הוא של אותו סידורי, מסבב קודם בלבד, ורק מסבב שבו המונה נקרא.
- אותה חתיכה נכשלה בשני דיסקים = SOURCE_FAILURE; בדיסק אחד = DISK_FAILURE; "לא ניתן לאמת" = COULD_NOT_VERIFY,
  גם כשהסוכן אמר done; מדגם = SAMPLED_OK, לא VERIFIED_OK; כתיבה שנעצרה = HALF_WRITTEN בשם הדיסק.
- הרישום: בצל גם ב-enforce, כלום ב-off, רק החלטה שהשתנתה, ובמחזור של K7.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import budget as B, integrity as I, memory, veto
from server.brain.integrity import Target, crc_delta, integrity
from server.brain.served import Served

# --- CRC: הפונקציה הטהורה ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", [42355, 2375, 59165, 0])
def test_a_crc_increase_since_the_previous_round_warns(baseline):
    d = crc_delta("S1", baseline + 3, baseline)
    assert d.verdict == I.WARN and "3" in d.reason


@pytest.mark.parametrize("value", [42355, 2375, 59165, 0])
def test_the_same_counter_does_not_warn_however_high_it_is(value):
    assert crc_delta("S1", value, value).verdict == I.NO_INCREASE


def test_no_baseline_is_its_own_named_state():
    assert crc_delta("new", 59165, None).verdict == I.NO_BASELINE
    assert crc_delta(None, 59165, 0).verdict == I.NO_BASELINE
    assert crc_delta("", 59165, 0).verdict == I.NO_BASELINE


def test_an_unread_counter_and_a_counter_that_fell_are_not_clean():
    assert crc_delta("S1", None, 42).verdict == I.UNREAD
    assert crc_delta("S1", True, 0).verdict == I.UNREAD          # bool אינו מונה
    assert crc_delta("S1", -1, 0).verdict == I.UNREAD
    assert crc_delta("S1", 0, 42).verdict == I.COUNTER_RESET


# --- K4: הפונקציה הטהורה ----------------------------------------------------------------------------------------------


def failed(serial, part=1, chunk=7, dev="sda", mac="m1"):
    return Target(mac, dev, serial, "failed", None, ((part, chunk),))


def test_the_same_chunk_failing_on_two_disks_is_a_source_failure():
    ds = integrity((failed("A"), failed("B", dev="sdb", mac="m2")))
    assert [d.verdict for d in ds] == [I.SOURCE_FAILURE, I.SOURCE_FAILURE]
    assert all("p1/chunk7: A,B" in d.evidence for d in ds)


def test_one_disk_failing_is_a_disk_failure():
    assert integrity((failed("A"),))[0].verdict == I.DISK_FAILURE


@pytest.mark.parametrize("second", [failed("A", dev="sdb"), failed("B", part=2), failed("B", chunk=8), failed(None)])
def test_the_same_serial_a_different_chunk_or_no_serial_do_not_correlate(second):
    assert all(d.verdict == I.DISK_FAILURE for d in integrity((failed("A"), second)))


def test_a_half_written_disk_does_not_count_toward_the_source():
    half = Target("m2", "sdb", "B", "failed", None, ((1, 7),), interrupted=True)
    ds = {d.target: d.verdict for d in integrity((failed("A"), half))}
    assert ds == {"m1/sda/A": I.DISK_FAILURE, "m2/sdb/B": I.HALF_WRITTEN}


@pytest.mark.parametrize("readback", [None, "unverified", "unknown", "evidence_unreadable"])
def test_could_not_verify_is_its_own_state_even_when_the_agent_says_done(readback):
    assert integrity((Target("m", "sda", "A", "done", readback),))[0].verdict == I.COULD_NOT_VERIFY


def test_success_needs_positive_evidence_and_a_sample_is_not_a_full_verification():
    assert integrity((Target("m", "sda", "A", "done", "verified"),))[0].verdict == I.VERIFIED_OK
    assert integrity((Target("m", "sda", "A", "done", "sampled"),))[0].verdict == I.SAMPLED_OK
    assert integrity((Target("m", "sda", "A", "failed", "verified"),))[0].verdict == I.COULD_NOT_VERIFY


def test_half_written_is_named_even_with_a_stale_success():
    d = integrity((Target("mac", "sdb", "B", "failed", "verified", interrupted=True),))[0]
    assert d.verdict == I.HALF_WRITTEN and "mac/sdb/B" in d.reason


# --- K4: מה שהסוכן שולח היום --------------------------------------------------------------------------------------------


def test_todays_readback_failure_phrase_is_a_disk_failure_and_not_run_is_could_not_verify():
    """הסוכן של היום מכשיל את היעד במשפט (`readback.sh`), בלי `readback` ובלי רשימת חתיכות."""
    bad = I.target("m", {"dev": "sda", "serial": "A", "state": "failed",
                         "error": "מחיצה 1: האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"})
    not_run = I.target("m", {"dev": "sdb", "serial": "B", "state": "failed",
                             "error": "האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב"})
    assert [d.verdict for d in integrity((bad, not_run))] == [I.DISK_FAILURE, I.COULD_NOT_VERIFY]


def test_malformed_chunk_evidence_is_not_a_clean_readback():
    t = I.target("m", {"dev": "sda", "state": "done", "readback": "verified",
                       "readback_failed_chunks": [{"partition": 1, "chunk_index": True}]})
    assert integrity((t,))[0].verdict == I.COULD_NOT_VERIFY


def test_an_interruption_phrase_and_a_writer_left_in_a_closed_round_are_half_written():
    stopped = I.target("m", {"dev": "sda", "serial": "A", "state": "failed", "error": "הזרם נקטע לפני סופו"})
    left = I.target("m", {"dev": "sdb", "serial": "B", "state": "writing"}, closed=True)
    assert [d.verdict for d in integrity((stopped, left))] == [I.HALF_WRITTEN, I.HALF_WRITTEN]
    assert I.target("m", {"dev": "sdb", "state": "writing"}) is None, "a writer in an open round is not judged yet"


# --- הרישום -----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "integrity.db")
    c.execute("PRAGMA foreign_keys = OFF")
    # גל שידור אחד פעיל בכל רגע (`one_active_broadcast`): רק "current" רץ, השאר סגורים מזמן
    for name, day in (("older", "09"), ("old", "10"), ("current", "12"), ("future", "13")):
        at = f"2026-11-{day}T00:00:00+00:00"
        c.execute("INSERT INTO sessions (id,group_id,image_id,prefix,expected_clients,wait_seconds,state,"
                  "opened_by,created_at,last_join_at,closed_at) VALUES (?,'g','image','p',1,1,?,'owner',?,0,?)",
                  (name, "running" if name == "current" else "closed", at, None if name == "current" else at))
    c.commit()
    yield c
    veto.forget()


def crc_event(conn, session, serial, crc, verdict="ok", reason=None, disk="sda"):
    conn.execute("INSERT OR REPLACE INTO disk_events (session_id,mac,disk,serial,crc,verdict,reason,updated_at)"
                 " VALUES (?,'m1',?,?,?,?,?,'2026-11-14T00:00:00+00:00')",
                 (session, disk, serial, crc, verdict, reason))
    conn.commit()


def member(conn, session, mac, targets):
    conn.execute("INSERT OR REPLACE INTO session_members (session_id,mac,state,updated_at,targets_json)"
                 " VALUES (?,?,'done','2026-11-12',?)", (session, mac, json.dumps(targets)))
    conn.commit()


def report(serial, chunk=7, dev="sda"):
    return {"dev": dev, "serial": serial, "state": "failed",
            "readback_failed_chunks": [{"partition": 1, "chunk_index": chunk}]}


def test_the_baseline_is_the_serials_previous_round_never_this_one_or_a_later_one(conn):
    crc_event(conn, "old", "A", 42355)
    crc_event(conn, "current", "A", 42356)
    crc_event(conn, "future", "A", 59165)
    assert I.previous_crc(conn, "A", "current") == (42355, "old")
    assert I.previous_crc(conn, "B", "current") is None
    assert I.previous_crc(conn, None, "current") is None
    ds = I.record_round(conn, "current")
    assert [d.verdict for d in ds] == [I.WARN]
    assert "previous_round=old" in ds[0].evidence


def test_an_unread_previous_round_is_skipped_for_the_last_round_that_read_the_counter(conn):
    """crc=0 של שער SMART שלא קרא (#2078) אינו בסיס: הוא היה מייצר "CRC עלה" מזויף."""
    crc_event(conn, "older", "A", 42355)
    crc_event(conn, "old", "A", 0, "unchecked", "health_unread")
    crc_event(conn, "current", "A", 42355)
    assert I.previous_crc(conn, "A", "current") == (42355, "older")
    assert I.record_round(conn, "current")[0].verdict == I.NO_INCREASE


def test_no_earlier_reading_is_no_baseline_and_an_unread_counter_now_is_unread(conn):
    crc_event(conn, "old", "A", 0, "unchecked", "health_unread")
    crc_event(conn, "current", "A", 42355)
    crc_event(conn, "current", "B", 0, "unchecked", "health_unread", disk="sdb")
    assert {d.target: d.verdict for d in I.record_round(conn, "current")} == {
        "m1/sda/A": I.NO_BASELINE, "m1/sdb/B": I.UNREAD}


def test_a_decision_is_recorded_once_and_again_only_when_it_changes(conn):
    member(conn, "current", "m1", [report("A")])
    assert I.record_round(conn, "current")[0].verdict == I.DISK_FAILURE
    I.record_round(conn, "current")
    assert len(memory.decisions(conn, round_id="current", rule="K4")) == 1
    member(conn, "current", "m2", [report("B")])
    assert all(d.verdict == I.SOURCE_FAILURE for d in I.record_round(conn, "current"))
    rows = memory.decisions(conn, round_id="current", rule="K4")
    assert [r["verdict"] for r in rows] == [I.SOURCE_FAILURE, I.SOURCE_FAILURE, I.DISK_FAILURE]
    assert all(r["mode"] == "shadow" and r["outcome"] == I.SHADOW_HE for r in rows)
    assert {(r["entity_kind"], r["entity_key"]) for r in rows} == {("disk", "A"), ("disk", "B")}
    I.record_round(conn, "current")
    assert len(memory.decisions(conn, round_id="current", rule="K4")) == 3


def test_correlation_never_crosses_rounds(conn):
    member(conn, "old", "m1", [report("A")])
    member(conn, "current", "m2", [report("B")])
    assert I.record_round(conn, "current")[0].verdict == I.DISK_FAILURE


def test_enforce_still_only_records_and_off_records_nothing(conn):
    member(conn, "current", "m1", [{"dev": "sda", "serial": "A", "state": "done", "readback": "unverified"}])
    before = conn.execute("SELECT targets_json FROM session_members").fetchone()[0]
    db.set_setting(conn, "brain_veto_mode", "enforce")
    assert I.record_round(conn, "current")[0].verdict == I.COULD_NOT_VERIFY
    assert memory.decisions(conn, round_id="current")[0]["mode"] == "shadow"
    assert conn.execute("SELECT targets_json FROM session_members").fetchone()[0] == before
    db.set_setting(conn, "brain_veto_mode", "off")
    member(conn, "current", "m2", [report("B")])
    assert I.record_round(conn, "current") == []
    assert len(memory.decisions(conn, round_id="current")) == 1


def test_a_closed_round_names_the_interrupted_writer_and_an_empty_report_is_not_verified(conn):
    member(conn, "current", "m1", [{"dev": "sdb", "serial": "B", "state": "writing"}])
    member(conn, "current", "m2", [])
    member(conn, "current", "m3", None)
    conn.execute("UPDATE session_members SET targets_json = '{broken' WHERE mac = 'm3'")
    conn.execute("UPDATE sessions SET state='closed' WHERE id='current'")
    conn.commit()
    ds = {d.target: d.verdict for d in I.record_round(conn, "current")}
    assert ds == {"m1/sdb/B": I.HALF_WRITTEN, "m2/?/?": I.COULD_NOT_VERIFY, "m3/?/?": I.COULD_NOT_VERIFY}


def test_the_observer_reads_open_rounds_and_a_closed_round_once_after_it_closed(conn, monkeypatch):
    observer = I.Observer(conn)
    member(conn, "current", "m1", [report("A")])
    member(conn, "future", "m2", [{"dev": "sdb", "serial": "B", "state": "writing"}])
    conn.execute("UPDATE sessions SET closed_at=? WHERE id='future'", (observer.started,))
    conn.commit()
    seen = []
    real = I.record_round
    monkeypatch.setattr(I, "record_round", lambda c, sid: seen.append(sid) or real(c, sid))
    observer.tick()
    observer.tick()
    assert sorted(seen) == ["current", "current", "future"],         "open = every cycle; closed since startup = once; closed before startup (older, old) = never"
    assert memory.decisions(conn, round_id="current")[0]["verdict"] == I.DISK_FAILURE
    assert memory.decisions(conn, round_id="future")[0]["verdict"] == I.HALF_WRITTEN


def test_the_observer_never_raises_into_the_budget_cycle(conn, monkeypatch):
    member(conn, "current", "m1", [report("A")])
    monkeypatch.setattr(I, "record_round", lambda c, sid: 1 / 0)
    I.Observer(conn).tick()


class _Chunk:
    interface = "eth1"

    def sending_bps(self):
        return 0


class _Sender:
    interface = None

    def status(self):
        return None

    def link(self):
        return {"speed_mbps": 1000}


def test_the_production_budget_cycle_runs_the_rules(conn, tmp_path):
    """החיבור לשרת: המחזור של K7 (`budget.production`) הוא השעון של CRC ו-K4."""
    stats = tmp_path / "eth1" / "statistics"
    stats.mkdir(parents=True)
    (stats / "tx_bytes").write_text("0")
    (stats / "tx_dropped").write_text("0")
    crc_event(conn, "old", "A", 10)
    crc_event(conn, "current", "A", 12)
    member(conn, "current", "m1", [report("A")])
    budget = B.production(conn, served=Served(), chunk=_Chunk(), sender=_Sender(),
                          sys_net=tmp_path, proc=tmp_path / "proc")
    budget.tick()
    got = {r["rule"]: r["verdict"] for r in memory.decisions(conn, round_id="current")}
    assert got == {"D29": I.WARN, "K4": I.DISK_FAILURE}
