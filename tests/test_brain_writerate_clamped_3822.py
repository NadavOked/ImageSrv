"""#3822: כשהרצפה גוברת על המדידה ב-K-WRITE, הראיה והנימוק אומרים זאת. ההחלטה (הקצב, הפסק) לא השתנתה."""

from __future__ import annotations

from server.brain import writerate


def test_floor_beats_the_measurement_and_the_call_says_so():
    # 33MB/s x 0.9 = 29 < the floor 74: the floor decides, and the evidence names the measured value
    call = writerate.judge_base([("A1", 33), ("B1", 209)], {}, 74)
    assert call.verdict == writerate.PACE and call.kbps == 74 * writerate.KBPS_PER_MBS     # the decision is unchanged
    assert call.clamped is True and call.measured_mbs == 29
    assert "clamped=floor" in call.evidence and "measured=29MB/s" in call.evidence
    assert "הרצפה (74MB/s) גברה על המדידה" in call.reason
    assert "לא מתחת לרצפה" not in call.reason


def test_a_measurement_above_the_floor_is_not_clamped():
    call = writerate.judge_base([("A1", 125), ("B1", 209)], {}, 74)
    assert call.kbps == 112 * writerate.KBPS_PER_MBS and call.clamped is False and call.measured_mbs is None
    assert not any(e.startswith(("clamped", "measured")) for e in call.evidence)
    assert "לא מתחת לרצפה" in call.reason and "גברה" not in call.reason


def test_the_clamp_is_judged_on_the_drives_that_set_the_pace():
    # B1 wrote 20MB/s in an old wave and is left out; A1 (check 120) sets the pace: 108 > 74, so no clamp
    call = writerate.judge_base([("A1", 120), ("B1", 30)], {"B1": (20, "ses_old")}, 74)
    assert call.counted == ("A1",) and call.clamped is False
