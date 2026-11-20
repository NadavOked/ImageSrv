"""#3596 -- K7 (`server/brain/budget.py`): בגל חתיכות תקציב ה-HTTP ישב על הרצפה כל הגל (17/11, `ses_92a72ae8`).

הסיבות, ומה נבדק:

1. **המבוקש במקום הנמדד.** `derive` לקח `broadcast = asked` (892Mbps) כשהחוט נשא 412: `limit = ceiling - broadcast` יצא רצפה.
   עכשיו השידור הוא מה שהכרטיס נשא פחות הגשת הקבצים שלנו, והמבוקש נרשם בנפרד (`asked`). אפס או כרטיס שלא נקרא אינם מדידה.
2. **"הופחת בחצי" על הרצפה.** `step` רשם `CUT` כשהתקציב כבר 8Mbps (שבע שורות ברצף, ids 5791-5799). עכשיו `AT_FLOOR`, עם אותה ראיה.
3. **אין מדידת תוצאה.** ה-`outcome` של CUT היה "מה החליף אותה". עכשיו נוסף מה שקרה לאובדן במחזור שאחריו, או "לא נמדד".

ההכרעה (shadow/enforce) והגבול העליון לא נגעו.
"""

from __future__ import annotations

import logging

import pytest

from server.brain import budget as B, peer, say

MBPS = 1_000_000
CEIL = 900 * MBPS
CFG = B.Config(ceiling_bps=CEIL)


def reading(at, *, served=0, broadcast=0, nic_tx=None, rx=None, abandoned=None):
    return B.Reading(at, served, broadcast, nic_tx, None, rx, None, abandoned)


def card_bytes(mbps: float, seconds: float = 10.0) -> int:
    return int(mbps * MBPS * seconds / 8)


# --- 1. השידור בפועל ---------------------------------------------------------------------------------------------------------------


def test_j1_the_wire_carried_412_while_892_was_asked_so_the_budget_is_what_is_left_and_not_the_floor():
    a = reading(0, broadcast=892 * MBPS, nic_tx=0)
    b = reading(10, broadcast=892 * MBPS, nic_tx=card_bytes(412))
    s = B.derive(a, b)
    assert s.broadcast_bps == pytest.approx(412 * MBPS, rel=0.001)
    assert any("412Mbps (measured" in n for n in s.notes) and "asked 892Mbps" in s.notes, s.notes
    d = B.step(None, CFG, s)
    assert d.limit_bps == pytest.approx(488 * MBPS, rel=0.001) and d.limit_bps > B.FLOOR_BPS, \
        "asked 892 of 900 leaves the floor; the 412 that was on the wire leaves 488"
    assert d.broadcast_bps == s.broadcast_bps


def test_our_own_files_are_not_part_of_the_broadcast_the_card_carried():
    a = reading(0, broadcast=892 * MBPS, nic_tx=0)
    b = reading(10, served=card_bytes(100), broadcast=892 * MBPS, nic_tx=card_bytes(512))
    assert B.derive(a, b).broadcast_bps == pytest.approx(412 * MBPS, rel=0.001)


def test_a_card_that_carried_more_than_asked_still_keeps_room_for_it():
    a = reading(0, broadcast=300 * MBPS, nic_tx=0)
    b = reading(10, broadcast=300 * MBPS, nic_tx=card_bytes(1100))
    s = B.derive(a, b)
    assert s.broadcast_bps == pytest.approx(1100 * MBPS, rel=0.001) and any("more than asked" in n for n in s.notes)


@pytest.mark.parametrize("a, b, why", [
    (reading(0, broadcast=892 * MBPS, nic_tx=0), reading(10, broadcast=892 * MBPS, nic_tx=0), "the card carried nothing"),
    (reading(0, broadcast=892 * MBPS, nic_tx=None), reading(10, broadcast=892 * MBPS, nic_tx=None), "the card was not read"),
    (reading(0, broadcast=892 * MBPS, nic_tx=10**9), reading(10, broadcast=892 * MBPS, nic_tx=5), "the counter went backwards"),
    (reading(0, broadcast=892 * MBPS, nic_tx=0), reading(10, served=card_bytes(300), broadcast=892 * MBPS, nic_tx=card_bytes(100)),
     "the files were served from another card"),
    (None, reading(10, broadcast=892 * MBPS, nic_tx=card_bytes(412)), "the first cycle has no difference"),
])
def test_without_a_measurement_the_asked_rate_stands_and_says_it_was_not_measured(a, b, why):
    s = B.derive(a, b)
    assert s.broadcast_bps == 892 * MBPS, why
    assert any("asked" in n and "measured on the card" not in n for n in s.notes), (why, s.notes)


def test_no_stream_still_means_no_card_arithmetic_and_an_unknown_rate_is_still_the_floor():
    got = B.derive(reading(0, nic_tx=0), reading(10, broadcast=0, nic_tx=10**9))
    assert got.broadcast_bps == 0
    unknown = B.derive(reading(0, nic_tx=0), reading(10, broadcast=None, nic_tx=10**9))
    assert unknown.broadcast_bps is None and B.step(None, CFG, unknown).rate_bps == CFG.floor


# --- 2. הרצפה ----------------------------------------------------------------------------------------------------------------------


def test_loss_on_a_budget_that_is_already_the_floor_is_not_a_cut_and_does_not_say_halved():
    s = B.Sample(892 * MBPS, 32 * MBPS, None, None, 0.24, abandoned=565)
    d = B.step(B.FLOOR_BPS, CFG, s)
    assert d.action == "AT_FLOOR" and d.rate_bps == B.FLOOR_BPS == d.limit_bps, d
    assert "565" in d.reason and "אין מה לחתוך" in d.reason and "בחצי" not in d.reason, d.reason
    assert "abandoned=565" in d.evidence, "the same evidence as the cut would have carried"


def test_the_seven_cuts_of_17_11_on_asked_892_are_no_longer_cuts():
    """ids 5791-5799: `broadcast 892Mbps (asked)`, תקציב 8, הנטישות 106, 407, 123, 71, 388, 107, 565."""
    rate = None
    for lost in (106, 407, 123, 71, 388, 107, 565):
        d = B.step(rate, CFG, B.Sample(892 * MBPS, 32 * MBPS, None, None, 0.24, abandoned=lost))
        rate = d.rate_bps
        assert d.action == "AT_FLOOR" and rate == B.FLOOR_BPS, (lost, d)


def test_a_real_cut_above_the_floor_still_halves_and_one_step_above_the_floor_is_still_a_cut():
    d = B.step(200 * MBPS, CFG, B.Sample(500 * MBPS, 0.0, 12, 0, 0.0, abandoned=0))
    assert (d.action, d.rate_bps) == (B.CUT_, 100 * MBPS) and "בחצי" in d.reason
    just_above = B.step(B.FLOOR_BPS + 1_000_000, CFG, B.Sample(500 * MBPS, 0.0, 12, 0, 0.0, abandoned=0))
    assert just_above.action == B.CUT_ and just_above.rate_bps == B.FLOOR_BPS


def test_a_clean_cycle_on_the_floor_is_not_at_floor_it_still_raises_when_there_is_room():
    d = B.step(B.FLOOR_BPS, CFG, B.Sample(300 * MBPS, 0.0, 0, 0, 0.0, abandoned=0))
    assert d.action == B.RAISE and d.rate_bps == B.FLOOR_BPS + B.STEP_BPS


def test_what_reads_the_verdict_keeps_working_on_at_floor():
    """הורדות ההשלמה: B29 קורא את הפסק, התקרה המקבילית נחתכת כמו קודם, והקונסולה יודעת לכתוב אותו בעברית."""
    d = B.step(B.FLOOR_BPS, CFG, B.Sample(892 * MBPS, 0.0, 3, 0, 0.0, abandoned=0))
    assert peer.load_of(d).busy is True, "on the floor with loss is a busy server for the peer choice, as the CUT was"
    conc = B.Conc()
    conc.cap = 8
    assert conc.step("AT_FLOOR") == (8, 4), "loss on the floor still halves the concurrency cap (recorded only)"
    assert "AT_FLOOR" in say.VERDICT_HE


def test_at_floor_is_logged_as_a_warning_like_the_loss_it_reports(caplog):
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    h = Harness(ceiling=900 * MBPS)
    h.tick(broadcast_bps=892 * MBPS, rx_abandoned=0)
    h.tick(broadcast_bps=892 * MBPS, rx_abandoned=50)
    lines = [r for r in caplog.records if r.getMessage().startswith("brain {")]
    assert '"action": "AT_FLOOR"' in lines[-1].getMessage() and lines[-1].levelno == logging.WARNING


# --- 3. מדידת התוצאה של חיתוך ---------------------------------------------------------------------------------------------------


class Harness:
    def __init__(self, ceiling=CEIL):
        self.t, self.reading, self.rows, self.outcomes = 1000.0, {"served_bytes": 0, "broadcast_bps": 0}, [], {}

        def remember(**row):
            self.rows.append(row)
            return len(self.rows)

        self.budget = B.Budget(mode=lambda: B.SHADOW, ceiling=lambda: (ceiling, "הגדרה"),
                               read=lambda: B.Reading(self.t, **self.reading), clock=lambda: self.t,
                               remember=remember, resolve=lambda row, outcome: self.outcomes.__setitem__(row, outcome))

    def tick(self, **reading):
        self.t += 10
        self.reading.update(reading)
        return self.budget.tick()


def test_a_cut_gets_the_loss_of_the_cycle_after_it_as_its_outcome_not_only_what_replaced_it():
    h = Harness()
    h.tick(broadcast_bps=300 * MBPS, rx_abandoned=0)
    assert h.tick(rx_abandoned=565).action == B.CUT_               # the cycle that cut: abandoned 565
    h.tick(rx_abandoned=565)                                        # the cycle after: nothing more was abandoned
    cut_row = next(i for i, r in enumerate(h.rows, 1) if r["verdict"] == B.CUT_)
    outcome = h.outcomes[cut_row]
    assert outcome.startswith("RAISE") or outcome.startswith("HOLD"), outcome        # what replaced it: as before
    assert "abandoned 565 -> 0" in outcome, outcome
    assert "rcvbuf לא נמדד" in outcome and "tx_dropped לא נמדד" in outcome, "not measured is said, not a number"


def test_a_cut_whose_next_cycle_did_not_measure_the_loss_says_not_measured_and_not_a_number():
    h = Harness()
    h.tick(broadcast_bps=300 * MBPS, rx_abandoned=0)
    h.tick(rx_abandoned=565)
    h.tick(rx_abandoned=None)                                       # the receivers stopped reporting
    cut_row = next(i for i, r in enumerate(h.rows, 1) if r["verdict"] == B.CUT_)
    assert "abandoned לא נמדד" in h.outcomes[cut_row] and "abandoned 565 ->" not in h.outcomes[cut_row]


def test_a_cut_followed_by_a_cut_is_measured_by_the_cycle_that_followed_it():
    h = Harness()
    h.tick(broadcast_bps=300 * MBPS, rx_abandoned=0)
    h.tick(rx_abandoned=100)                                        # cut: 100
    h.tick(rx_abandoned=160)                                        # cut again: 60, which is the "after" of the first
    cuts = [i for i, r in enumerate(h.rows, 1) if r["verdict"] == B.CUT_]
    assert len(cuts) == 2 and "abandoned 100 -> 60" in h.outcomes[cuts[0]], h.outcomes


def test_a_cut_that_is_still_in_force_has_no_outcome_yet_and_the_brain_switched_off_says_not_measured():
    h = Harness()
    h.tick(broadcast_bps=300 * MBPS, rx_abandoned=0)
    h.tick(rx_abandoned=100)
    cut_row = next(i for i, r in enumerate(h.rows, 1) if r["verdict"] == B.CUT_)
    assert cut_row not in h.outcomes
    h.budget._mode_fn = lambda: B.OFF
    h.tick()
    assert h.outcomes[cut_row].startswith("המוח כובה") and "abandoned לא נמדד" in h.outcomes[cut_row]


def test_at_floor_is_remembered_once_when_it_starts_not_every_cycle():
    h = Harness()
    h.tick(broadcast_bps=892 * MBPS, rx_abandoned=0)
    for lost in (100, 300, 400, 900):
        h.tick(rx_abandoned=lost)
    assert [r["verdict"] for r in h.rows].count("AT_FLOOR") == 1, [r["verdict"] for r in h.rows]
    assert not any(r["verdict"] == B.CUT_ for r in h.rows), "nothing was cut: the budget was the floor all along"


# --- #3838: רעש של הכרטיס אינו שידור, וירידה קטנה מצעד אינה CLAMP ----------------------------------------------------------------------


def test_3838_card_noise_does_not_replace_what_was_asked_and_a_budget_that_did_not_drop_is_not_clamped():
    """הפצה ישירה: השולח הוא מחשב הבנייה, והכרטיס של השרת נושא עשרות Kbps. המבוקש נשאר; התקציב 900 לא ירד, ולכן אין CLAMP."""
    a = reading(0, broadcast=150 * MBPS, nic_tx=0)
    b = reading(10, broadcast=150 * MBPS, nic_tx=int(50_000 * 10 / 8))        # 50Kbps
    s = B.derive(a, b)
    assert s.broadcast_bps == 150 * MBPS and any("asked; the card measured 0Mbps" in n for n in s.notes), s.notes
    d = B.step(CEIL, CFG, B.Sample(0, 0.0, 0, 0, 0.0, ()))  # אין שידור: כל הגבול
    assert d.rate_bps == CEIL
    # תקציב שכבר יושב על מה שנשאר (+0.2Mbps מעל) אינו מוכרז כ-CLAMP
    s2 = B.Sample(int(0.2 * MBPS), 0.0, 0, 0, 0.0, ())
    d2 = B.step(CEIL, CFG, s2)
    assert d2.action != B.CLAMP and d2.rate_bps == pytest.approx(CEIL - 0.2 * MBPS, abs=1), (d2.action, d2.reason)


def test_3838_a_real_drop_of_a_step_or_more_is_still_clamp():
    s = B.Sample(150 * MBPS, 0.0, 0, 0, 0.0, ())
    d = B.step(CEIL, CFG, s)
    assert d.action == B.CLAMP and d.rate_bps == 750 * MBPS
