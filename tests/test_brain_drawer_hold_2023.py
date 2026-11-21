"""#2023 (נדב 15/11), #3064 (K3): ההשלמה של דיסק איטי -- ממשיכה, או שהדיסק ייכתב בגל הבא. ההחלטה של המוח, בצל.

`T_continue` (כל מה שנשאר לדיסק בקצב ההשלמה שנמדד בו) מול `T_redo` (כל האימג' בקצב הגל, ועוד תקורה לכל מחיצה): שתיהן ממדידה,
בלי סף קבוע. מה שלא נמדד הוא `UNDECIDED`, ו-`DROP` אינו נגזר מתקורה שלא נמדדה. המספרים כאן הם מספרים לטסט, לא מדידות:
אימג' של 20GB דחוס בקצב 700Mbps (`waveeta.predict`: 229 שניות למעבר אחד), ו-1 חתיכה לשנייה לדיסק ה"איטי".

**בקרה שלילית:** הקובץ הזה מייבא את `server/brain/drawerhold.py`, שאינו בבסיס -- בבסיס הוא נופל על ה-import, וזו אינה בקרה
שלילית. את הבקרה השלילית עושים הטסטים של `test_chunk_drawer_hold_2023.py` (החיווט, ההגדרה והאפליקציה), שנופלים על התנהגות.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import drawerhold as H, memory

GB = 10 ** 9
IMAGE = dict(chunks_total=10_000, zbytes_total=20 * GB, bits=700 * 10 ** 6, parts=3)
T1 = 20 * GB * 8 / (700 * 10 ** 6)                  # 229 s: the whole image at the wave's rate
# a drive at 1 chunk a second (100 done in 100 s): its remaining chunks are its seconds
SLOW = dict(grown=100, received=None, elapsed_s=100.0, **IMAGE)


def _judge(remaining=1000, later=0, overhead=None, **over):
    return H.judge(**{**SLOW, "remaining": remaining, "later_chunks": later, "overhead_s": overhead, **over})


# --- the criterion ---------------------------------------------------------------------------------------------


def test_the_completion_goes_on_when_it_ends_before_a_rewrite_could_even_start_and_no_overhead_is_needed_to_say_so():
    """100 s to go against 229 s for the image alone: continues whatever a partition's overhead is -- it can only add to T_redo."""
    j = _judge(remaining=100)
    assert j.verdict == H.CONTINUE and j.t_continue_s == pytest.approx(100.0) and j.t_redo_s == pytest.approx(T1)
    assert "overhead=unmeasured" in j.evidence and "t_redo_basis=lower_bound" in j.evidence


def test_drop_is_not_derived_from_an_overhead_nobody_measured():
    """1,000 s to go against 229 s: if the overhead is large enough a rewrite is still longer -- nobody measured it, so UNDECIDED."""
    j = _judge(remaining=1000)
    assert j.verdict == H.UNDECIDED and "תלוי בתקורה" in j.reason, j
    assert "overhead=unmeasured" in j.evidence and j.t_redo_s == pytest.approx(T1)


def test_with_the_overhead_measured_the_shorter_of_the_two_wins():
    """3 partitions x 10 s of overhead = 30 s on top of 229: a rewrite takes 259 s."""
    assert _judge(remaining=250, overhead=10.0).verdict == H.CONTINUE             # 250 s <= 259 s
    j = _judge(remaining=300, overhead=10.0)                                      # 300 s > 259 s
    assert j.verdict == H.DROP and j.t_redo_s == pytest.approx(T1 + 30.0) and j.t_continue_s == pytest.approx(300.0)
    assert j.reason.startswith("ההשלמה איטית מכתיבה מחדש — הדיסק ייכתב בגל הבא"), j.reason
    assert {"overhead_s=10.0", "t_redo_basis=measured"} <= set(j.evidence)


def test_equal_is_a_continue_not_a_drop():
    """800Mbps: the image alone is 200 s; two partitions x 50 s = 300 s. A completion of exactly 300 s continues."""
    image = dict(chunks_total=10_000, zbytes_total=20 * GB, bits=800 * 10 ** 6, parts=2)
    kw = dict(grown=100, received=None, elapsed_s=100.0, later_chunks=0, overhead_s=50.0, **image)
    equal = H.judge(remaining=300, **kw)
    assert equal.t_continue_s == equal.t_redo_s == 300.0 and equal.verdict == H.CONTINUE
    assert H.judge(remaining=301, **kw).verdict == H.DROP


def test_a_drive_the_others_left_behind_completes_the_partitions_after_this_one_too():
    """A drawer 10 chunks short of whole in the first partition looks done -- but the others move on, it gets no stream, and
    it completes everything after it at its own pace: 6,010 s, not 10."""
    near = _judge(remaining=10, later=0, overhead=10.0)
    assert near.verdict == H.CONTINUE and near.t_this_s == pytest.approx(10.0)
    behind = _judge(remaining=10, later=6_000, overhead=10.0)
    assert behind.verdict == H.DROP and behind.t_this_s == pytest.approx(10.0) and behind.t_continue_s == pytest.approx(6_010.0)
    assert "t_continue_this_s=10" in behind.evidence and "later_chunks=6000" in behind.evidence


def test_what_apply_took_is_work_evidence_and_not_the_rate_of_this_drive():
    """#3895 (replaces the #3189 `max(grown, received)`): `received` is the machine's counter, shared by all its drawers and the pass.
    A drive whose ledger did not grow has NO durable rate -- UNDECIDED, the ETA missing -- and what `apply` took is only written down."""
    late_flush = _judge(remaining=1000, overhead=10.0, grown=0, received=100)
    assert late_flush.verdict == H.UNDECIDED and "אין קצב של דיסק" in late_flush.reason, late_flush
    assert late_flush.t_continue_s is None and late_flush.rate_chunks_s is None
    assert "rate_chunks_s=unmeasured" in late_flush.evidence and "received_chunks=100" in late_flush.evidence


@pytest.mark.parametrize("what, over", [
    ("no rate: nothing grew and nothing was taken", dict(grown=0, received=None)),
    ("no rate: not a number of chunks", dict(grown=None, received=None)),
    ("no clock", dict(elapsed_s=0)),
    ("a clock that is not a number", dict(elapsed_s=None)),
    ("what is left on the drive was not read", dict(remaining=None)),
    ("the partitions after were not read", dict(later_chunks=None)),
    ("the image's chunks unknown", dict(chunks_total=None)),
    ("the image's size unknown", dict(zbytes_total=0)),
    ("the wave's rate unknown", dict(bits=0)),
    ("no partitions", dict(parts=None)),
    ("a bool is a flag, not a count", dict(grown=True, received=None)),
])
def test_what_was_not_measured_is_undecided_and_never_a_zero(what, over):
    for overhead in (None, 10.0):
        j = H.judge(**{**SLOW, "remaining": 1000, "later_chunks": 0, "overhead_s": overhead, **over})
        assert j.verdict == H.UNDECIDED and j.reason.startswith("לא נשפט"), (what, j)
        assert j.t_continue_s is None and j.t_redo_s is None, (what, j)


def test_a_silent_drive_is_not_judged_here_the_silence_rule_is_the_one():
    j = _judge(grown=0, received=0)
    assert j.verdict == H.UNDECIDED and "שתיקה נשפטת בכלל השתיקה" in j.reason


# --- the record: only a change, with every number --------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    return c


def _hold(conn, setting="off", mode="shadow"):
    return H.DrawerHold(conn, mode_fn=lambda: mode, hold=lambda: setting)


def _tell(hold, remaining, grown, elapsed, *, later=0, session="W-1", part=3, mac="a", serial="APS", ended=False, received=None,
          drawers=None):
    return hold.evaluate(session_id=session, part=part, mac=mac, ended=ended, received=received, elapsed_s=elapsed, later_chunks=later,
                         drawers=[{"serial": serial, "remaining": remaining, "grown": grown}] if drawers is None else drawers, **IMAGE)


def _rows(conn, action=H.ACTION):
    return [d for d in memory.decisions(conn, rule="K3", limit=100) if d["action"] == action]


def test_a_decision_is_recorded_once_with_every_number_and_only_a_change_is_recorded_again(conn):
    hold = _hold(conn)
    hold.gap("W-1", 10.0)
    _tell(hold, 300, 100, 100.0)                  # 300 s to go
    (row,) = _rows(conn)
    assert row["verdict"] == "DROP" and row["round_id"] == "W-1" and (row["entity_kind"], row["entity_key"]) == ("disk", "APS")
    ev = row["evidence"]
    assert {"drive=APS", "mac=a", "part=3", "remaining_chunks=300", "later_chunks=0", "done_chunks=100", "elapsed_s=100",
            "rate_chunks_s=1.00", "t_continue_s=300", "t_redo_s=259", "overhead_s=10.0", "overhead_n=1", "overhead_from=wave",
            "chunks=10000", "zbytes=20000000000", "stream_mbps=700", "parts=3", "execution=off"} <= set(ev), ev
    assert "ההשלמה איטית מכתיבה מחדש — הדיסק ייכתב בגל הבא" in row["reason"] and "צל" in row["reason"]
    assert row["outcome"] is None                                              # open: nothing happened to it yet
    _tell(hold, 290, 110, 110.0)                  # still 290 s against 259: the same decision, no new row
    _tell(hold, 280, 120, 120.0)
    assert len(_rows(conn)) == 1
    _tell(hold, 100, 320, 320.0)                  # it sped up: 100 s to go. A change: a new row, and the old one is closed by it
    rows = _rows(conn)
    assert [r["verdict"] for r in rows] == ["CONTINUE", "DROP"]               # newest first
    assert rows[1]["outcome"].startswith("הוחלפה ב-CONTINUE") and rows[0]["outcome"] is None


def test_the_decision_closes_with_what_happened_against_what_was_predicted(conn):
    hold = _hold(conn)
    hold.gap("W-1", 10.0)
    _tell(hold, 300, 100, 100.0)
    _tell(hold, 0, 400, 420.0, drawers=[])        # the drive is whole 320 s later
    (row,) = _rows(conn)
    assert row["outcome"].startswith("הדיסק שלם אחרי עוד 320s") and "חזוי בהחלטה: 300s" in row["outcome"], row["outcome"]


def test_a_completion_that_ends_unfinished_is_not_called_whole(conn):
    hold = _hold(conn)
    _tell(hold, 300, 100, 100.0)
    _tell(hold, 200, 200, 200.0, ended=True)      # the machine says `ended` and the drive still lacks chunks
    (row,) = _rows(conn)
    assert "עוד חסר לו משהו" in row["outcome"] and "בלי שהדיסק שלם" in row["outcome"], row["outcome"]


def test_the_end_of_the_wave_closes_what_is_open_and_writes_the_overhead_it_measured(conn):
    hold = _hold(conn)
    for seconds in (2.0, 4.0, 9.0):
        hold.gap("W-1", seconds)
    _tell(hold, 300, 100, 100.0)
    hold.on_event("send_done", "W-1 {}", "W-1")
    (row,) = _rows(conn)
    assert row["outcome"].startswith("הגל הסתיים ב-send_done")
    (over,) = _rows(conn, H.OVERHEAD)
    assert over["verdict"] == "MEASURED" and over["evidence"][:2] == ["overhead_s=4.0", "samples=3"], over


def test_a_wave_that_failed_or_stopped_writes_no_overhead(conn):
    hold = _hold(conn)
    hold.gap("W-1", 3.0)
    hold.on_event("send_failed", "x", "W-1")
    assert _rows(conn, H.OVERHEAD) == []


def test_the_overhead_of_past_waves_stands_in_until_the_wave_has_a_second_partition(conn):
    """The first partition is where a slow drive hurts most, and the wave has measured no gap yet: the median of the last waves."""
    for sid, seconds in (("W-0", [6.0]), ("W-1", [8.0]), ("W-2", [10.0])):
        past = _hold(conn)
        for s in seconds:
            past.gap(sid, s)
        past.on_event("send_done", "", sid)
    now = _hold(conn)
    assert now._overhead("W-3") == (8.0, 3, "history")
    _tell(now, 300, 100, 100.0, session="W-3")                                   # 300 s against 229 + 3 x 8: a drop
    (row,) = _rows(conn)
    assert row["verdict"] == "DROP" and {"overhead_s=8.0", "overhead_n=3", "overhead_from=history"} <= set(row["evidence"])
    now.gap("W-3", 1.0)                                                          # the wave's own gap comes first, when it has one
    assert now._overhead("W-3") == (1.0, 1, "wave")


def test_no_overhead_anywhere_is_unmeasured_and_the_row_says_so(conn):
    hold = _hold(conn)
    assert hold._overhead("W-1") == (None, 0, "none")
    _tell(hold, 1000, 100, 100.0)
    (row,) = _rows(conn)
    assert row["verdict"] == "UNDECIDED" and {"overhead=unmeasured", "overhead_from=none"} <= set(row["evidence"]), row


def test_an_overhead_that_is_not_a_number_is_not_kept(conn):
    hold = _hold(conn)
    for bad in (None, True, "5", float("nan"), -1.0, float("inf")):
        hold.gap("W-1", bad)
    assert hold._overhead("W-1") == (None, 0, "none")


def test_the_execution_setting_is_in_the_evidence_and_the_row_says_what_it_does(conn):
    """The setting is for the execution: `off` is a shadow ("nothing changes"), `on` is written down as wired and says it executes."""
    off, on = _hold(conn, "off"), _hold(conn, "on")
    off.gap("W-1", 10.0), on.gap("W-2", 10.0)
    _tell(off, 300, 100, 100.0, session="W-1")
    _tell(on, 300, 100, 100.0, session="W-2")
    by = {r["round_id"]: r["evidence"] for r in _rows(conn)}
    assert {"execution=off", "execution_wired=n/a"} <= set(by["W-1"])
    assert {"execution=on", "execution_wired=yes"} <= set(by["W-2"])
    reasons = {r["round_id"]: r["reason"] for r in _rows(conn)}
    assert "צל: שום דבר לא משתנה" in reasons["W-1"] and "מבוצע" in reasons["W-2"] and "צל" not in reasons["W-2"], reasons


def test_off_computes_nothing_and_records_nothing(conn):
    hold = _hold(conn, mode="off")
    hold.gap("W-1", 10.0)
    _tell(hold, 300, 100, 100.0)
    hold.on_event("send_done", "", "W-1")
    assert _rows(conn) == [] and _rows(conn, H.OVERHEAD) == []


def test_nothing_the_brain_is_told_can_raise_into_the_wave(conn):
    hold = _hold(conn)
    hold.evaluate(session_id="W-1", part=3, mac="a", drawers=[None, {"serial": 7}, {"serial": "X"}, "garbage"],
                  received="many", elapsed_s=object(), later_chunks=None, chunks_total=None, zbytes_total=None, bits=None,
                  parts=None)
    hold.evaluate(session_id="W-1", part=3, mac="a", drawers=None, received=None, elapsed_s=None, later_chunks=None,
                  chunks_total=None, zbytes_total=None, bits=None, parts=None)
    hold.gap("W-1", object())
    hold.on_event("send_done", None, "W-1")
    hold.on_event("chunk_disk_slow", "x", "W-1")


def test_the_drives_of_one_cloner_are_judged_each_by_what_it_lacks(conn):
    """Two drives of the same machine: the one that lacks little continues, the other is dropped -- each its own row, by its serial."""
    hold = _hold(conn)
    hold.gap("W-1", 10.0)
    hold.evaluate(session_id="W-1", part=3, mac="a", received=None, elapsed_s=100.0, later_chunks=0, **IMAGE,
                  drawers=[{"serial": "FAST", "remaining": 50, "grown": 100}, {"serial": "APS", "remaining": 600, "grown": 100}])
    got = {r["entity_key"]: r["verdict"] for r in _rows(conn)}
    assert got == {"FAST": "CONTINUE", "APS": "DROP"}


# --- the execution: what the brain hands the engine (#2023, PR 2) ----------------------------------------------------


GOING = dict(session="W-1", part=3, mac="a", serial="APS")


def test_the_setting_off_hands_the_engine_nothing_whatever_the_decision(conn):
    """(neg) A shadow: the rows are written, and what `evaluate` returns is empty -- for a CONTINUE and for a DROP, over and over."""
    hold = _hold(conn, "off")
    hold.gap("W-1", 10.0)
    assert _tell(hold, 100, 100, 100.0) == {} and _tell(hold, 300, 100, 100.0) == {} and _tell(hold, 300, 100, 100.0) == {}
    assert {r["verdict"] for r in _rows(conn)} == {"CONTINUE", "DROP"}                       # it still decided, and wrote it down


def test_a_continue_is_handed_over_at_once_when_the_setting_is_on(conn):
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    got = _tell(hold, 100, 100, 100.0)                                                       # 100 s to go against 239: continue
    assert list(got) == ["APS"] and got["APS"][0] == "CONTINUE" and "ההשלמה ממשיכה" in got["APS"][1], got


def test_a_drop_is_handed_over_only_on_the_second_consecutive_evaluation(conn):
    """(neg) The hysteresis (the coordinator, 15/11): one noisy measurement does not turn a decision. DROP, DROP -> handed over;
    and it stays handed over while it stays DROP."""
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    assert _tell(hold, 300, 100, 100.0) == {}                                                # the first DROP: recorded, not handed over
    second = _tell(hold, 290, 110, 110.0)
    assert list(second) == ["APS"] and second["APS"][0] == "DROP" and "ייכתב בגל הבא" in second["APS"][1], second
    assert list(_tell(hold, 280, 120, 120.0)) == ["APS"]
    assert len(_rows(conn)) == 1                                                             # one decision, one row: the count is not a row


@pytest.mark.parametrize("between, remaining, grown, elapsed", [
    ("a CONTINUE", 100, 330, 330.0),                  # it sped up: 100 s to go against 239
    ("an UNDECIDED", 1000, 0, 120.0),                 # nothing grew: no rate, nothing decided
])
def test_a_drop_that_something_else_came_between_is_a_first_drop_again(conn, between, remaining, grown, elapsed):
    """(neg) Two DROPs that are not consecutive are not two in a row: DROP, <something else>, DROP hands nothing over."""
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    assert _tell(hold, 300, 100, 100.0) == {}
    mid = _tell(hold, remaining, grown, elapsed)
    assert "DROP" not in {v for v, _ in mid.values()}, (between, mid)
    assert _tell(hold, 300, 100, 340.0) == {}, f"the DROP after {between} was counted with the one before it"
    assert list(_tell(hold, 290, 110, 350.0)) == ["APS"]                                      # now two in a row


def test_an_undecided_is_never_handed_over(conn):
    hold = _hold(conn, "on")
    hold.gap("W-1", None)                                      # no overhead measured: DROP is not derived from it
    for _ in range(3):
        assert _tell(hold, 1000, 100, 100.0) == {}
    assert {r["verdict"] for r in _rows(conn)} == {"UNDECIDED"}


def test_the_veto_mode_off_hands_nothing_even_with_the_setting_on(conn):
    hold = H.DrawerHold(conn, mode_fn=lambda: "off", hold=lambda: "on")
    assert _tell(hold, 100, 100, 100.0) == {} and _rows(conn) == []


def test_each_drive_has_its_own_count_of_drops(conn):
    """Two drives of one machine: the one that is dropped on its second report does not carry the count of the other."""
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    both = [{"serial": "FAST", "remaining": 50, "grown": 100}, {"serial": "APS", "remaining": 600, "grown": 100}]
    first = _tell(hold, 0, 0, 100.0, drawers=both)
    assert list(first) == ["FAST"]                                                           # APS: DROP #1; FAST continues
    second = _tell(hold, 0, 0, 110.0, drawers=[{"serial": "FAST", "remaining": 40, "grown": 110}, {"serial": "APS", "remaining": 590, "grown": 110}])
    assert {k: v[0] for k, v in second.items()} == {"FAST": "CONTINUE", "APS": "DROP"}


def test_what_the_engine_did_closes_the_decision_with_that_and_not_with_whole(conn):
    """(neg) `executed`: the row is closed with what was done (held, or failed by name), and a later `ended` does not re-close it."""
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    _tell(hold, 100, 100, 100.0)
    hold.executed("W-1", 3, "a", "APS", "CONTINUE")
    (row,) = _rows(conn)
    assert row["outcome"] == "בוצע: המגירה הוחזקה -- ההשלמה ממשיכה ברקע והשאר עברו למחיצה הבאה", row["outcome"]
    _tell(hold, 0, 400, 420.0, drawers=[], ended=True)
    assert _rows(conn)[0]["outcome"].startswith("בוצע: המגירה הוחזקה")
    hold.executed("W-1", 3, "a", "APS", "DROP")                                              # nothing open: nothing to say, and no raise
    hold.executed("W-1", "x", None, object(), "DROP")


def test_a_wave_that_ends_forgets_the_count_of_drops(conn):
    hold = _hold(conn, "on")
    hold.gap("W-1", 10.0)
    _tell(hold, 300, 100, 100.0)
    hold.on_event("send_failed", "x", "W-1")
    assert _tell(hold, 300, 100, 100.0, session="W-1") == {}                                  # the same ids in a new wave: a first DROP
