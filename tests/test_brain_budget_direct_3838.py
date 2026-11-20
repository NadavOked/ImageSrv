"""#3838 (ג) -- K7 בהפצה ישירה: השולח הוא מחשב הבנייה והזרם אינו בכרטיס השרת, ולכן K7 אינו מחליט.

גל ישיר עם ירידה אמיתית (הכרטיס נשא 400Mbps מול 700 שהתבקשו): אין CLAMP, התקציב לא זז, ושורה אחת ב-`brain_decisions` עם
`outcome` שאינו ריק. אותה קריאה בגל רגיל: CLAMP כמו היום.
"""

from __future__ import annotations

from server.brain import budget as B, peer, say

MBPS = 1_000_000
CEIL = 900 * MBPS
NA = "NOT_APPLICABLE"
OUTCOME = "n/a: direct — השולח הוא מחשב הבנייה"


class Harness:
    def __init__(self):
        self.t, self.reading, self.rows, self.outcomes = 1000.0, {"served_bytes": 0, "broadcast_bps": 0, "nic_tx_bytes": 0}, [], {}

        def remember(**row):
            self.rows.append(row)
            return len(self.rows)

        self.budget = B.Budget(mode=lambda: B.SHADOW, ceiling=lambda: (CEIL, "הגדרה"),
                               read=lambda: B.Reading(self.t, **self.read()), clock=lambda: self.t,
                               remember=remember, resolve=lambda row, outcome: self.outcomes.__setitem__(row, outcome))

    def read(self) -> dict:
        # a code without the `direct` field sees an ordinary wave (so the control run fails on a value, not on a TypeError)
        return {k: v for k, v in self.reading.items() if k in B.Reading.__dataclass_fields__}

    def tick(self, card_mbps=None, **reading):
        self.t += 10
        if card_mbps is not None:
            self.reading["nic_tx_bytes"] += int(card_mbps * MBPS * 10 / 8)
        self.reading.update(reading)
        return self.budget.tick()


def _idle() -> Harness:
    h = Harness()
    h.tick(broadcast_bps=0)                                     # no stream: the budget is the whole ceiling
    return h


def test_a_direct_wave_with_a_real_drop_is_not_clamped_and_the_budget_does_not_move():
    h = _idle()
    d = h.tick(card_mbps=700, broadcast_bps=700 * MBPS, direct=True)    # the card carries 700: the budget would fall to 200
    assert d.action == NA and d.rate_bps == CEIL, d
    assert not any(r["verdict"] == B.CLAMP for r in h.rows), h.rows
    na = [(i, r) for i, r in enumerate(h.rows, 1) if r["verdict"] == NA]
    assert len(na) == 1 and na[0][1]["outcome"] == OUTCOME and "direct=1" in na[0][1]["evidence"], na
    for _ in range(3):
        assert h.tick(card_mbps=400).rate_bps == CEIL
    assert [r["verdict"] for r in h.rows].count(NA) == 1, "recorded once when it starts, not every cycle"
    assert na[0][0] not in h.outcomes, "an n/a row is not overwritten by a later outcome"


def test_the_same_reading_in_an_ordinary_wave_is_clamped_as_today():
    h = _idle()
    d = h.tick(card_mbps=700, broadcast_bps=700 * MBPS, direct=False)
    assert d.action == B.CLAMP and d.rate_bps == CEIL - 700 * MBPS, d
    assert not any(r["verdict"] == NA for r in h.rows)


def test_after_the_direct_wave_ends_k7_decides_again():
    h = _idle()
    h.tick(card_mbps=700, broadcast_bps=700 * MBPS, direct=True)
    d = h.tick(card_mbps=700, direct=False)
    assert d.action != NA and d.rate_bps < CEIL, d


def test_a_direct_decision_keeps_a_previous_budget_and_is_not_read_as_busy():
    h = Harness()
    h.tick(broadcast_bps=0)
    h.budget._rate_bps = 8 * MBPS                               # a floor left by an earlier ordinary wave
    d = h.tick(broadcast_bps=700 * MBPS, direct=True)
    assert d.action == NA and d.rate_bps == 8 * MBPS
    assert peer.load_of(d).busy is False
    assert NA in say.VERDICT_HE
