"""#3463 (K7): לחץ על דיסק השרת אינו ראיה על הרשת -- תקציב ההגשה ב-HTTP נקבע מראיות רשת בלבד.

המקרה (ברזל 15/11, `lab/metal-1115-brain3`): שלושה סבבים, K7 `CUT` ב-24/17/20 החלטות, והתקציב ירד לרצפה 8Mbps בגלל
`io_full` 0.2 בדיסק הספרייה -- בלי אבדן ברשת (0.22-0.43 לאלף). `step` ספר את הדיסק גם סיבת קיצוץ וגם "שקט" שמצדיק עלייה.

- **דיסק לחוץ ורשת נקייה לא מקצץ:** התקציב עולה בצעד, כמו בלי הלחץ.
- **דיסק לחוץ ובלי שום מדידת רשת לא מתקדם ולא יורד:** `HOLD`, "אין מדידה", ולא "שקט" שהדיסק יצר.
- **דיסק לחוץ ואבדן ברשת:** מקצץ -- בגלל האבדן, והסיבה אינה מזכירה את הדיסק.
- **הראיה לא נעלמת:** `io_full` נשאר בראיות, עם משפט שאומר שלא נספר לרשת.
- **`disk_cuts_budget=True` משחזר את ההתנהגות הישנה** (חזרה לאחור בשורה אחת).

**בקרה שלילית:** `server/brain/budget.py` בלבד חוזר לבסיס -- `Config` בלי `disk_cuts_budget` ו-`step` שמקצץ על הדיסק;
הטסטים כאן נופלים (`grep -c disk_cuts_budget` על העץ = 0).
"""

from __future__ import annotations

import pytest

from server.brain import budget as B

MBPS = 1_000_000
CEIL = 900 * MBPS
NET = B.Config(ceiling_bps=CEIL)
BUSY = B.PSI_FULL_MAX + 0.15              # 0.35: מעל הסף של דיסק הספרייה


def old():
    """ההתנהגות שלפני #3463. בפונקציה ולא בקבוע: בקרה שלילית מפילה את הטסט הזה, לא את כל הקובץ בייבוא."""
    return B.Config(ceiling_bps=CEIL, disk_cuts_budget=True)


def sample(broadcast=500 * MBPS, *, rx=None, tx=None, io=None, abandoned=None):
    return B.Sample(broadcast, 0.0, rx, tx, io, abandoned=abandoned)


def test_a_busy_disk_with_a_clean_network_does_not_cut():
    d = B.step(300 * MBPS, NET, sample(rx=0, tx=0, io=BUSY))
    assert d.action == B.RAISE and d.rate_bps == 300 * MBPS + B.STEP_BPS, "the network is quiet: one step up"
    assert "דיסק" not in d.reason, "the reason names what was measured on the network, not the disk"


def test_the_budget_does_not_fall_to_the_floor_on_a_busy_disk_alone():
    # ברזל 15/11: 20+ קיצוצים ברצף על io_full, עד רצפה. עשרים מחזורים של דיסק לחוץ ורשת נקייה -- לא יורדים.
    rate = 300 * MBPS
    for _ in range(20):
        rate = B.step(rate, NET, sample(rx=0, tx=0, io=BUSY)).rate_bps
    assert rate > 300 * MBPS and rate != B.FLOOR_BPS


def test_a_busy_disk_is_not_the_silence_that_raises_the_budget():
    d = B.step(300 * MBPS, NET, sample(io=BUSY))          # שום מונה רשת לא נקרא, רק הדיסק
    assert (d.action, d.rate_bps) == (B.HOLD, 300 * MBPS)
    assert "אין מדידה" in d.reason and "שקט" not in d.reason


@pytest.mark.parametrize("signal,expect", [({"rx": 5}, "המקלטים"), ({"tx": 9}, "כרטיס השרת"), ({"abandoned": 3}, "נטשו")])
def test_network_loss_still_cuts_when_the_disk_is_busy_too(signal, expect):
    d = B.step(300 * MBPS, NET, sample(io=BUSY, **signal))
    assert d.action == B.CUT_ and d.rate_bps == 150 * MBPS and expect in d.reason
    assert "דיסק" not in d.reason, "the cut is for the loss, not for the disk"


def test_the_busy_disk_stays_in_the_evidence_and_says_it_was_not_counted():
    d = B.step(300 * MBPS, NET, sample(rx=0, tx=0, io=BUSY))
    assert f"io_full={BUSY}" in d.evidence or any(e.startswith("io_full=") for e in d.evidence)
    assert any("#3463" in e and "לא נספר" in e for e in d.evidence)


def test_a_disk_below_the_threshold_leaves_no_note():
    d = B.step(300 * MBPS, NET, sample(rx=0, tx=0, io=B.PSI_FULL_MAX))
    assert not any("#3463" in e for e in d.evidence)


def test_the_old_behaviour_is_one_flag_away():
    cut = B.step(300 * MBPS, old(), sample(rx=0, tx=0, io=BUSY))
    assert cut.action == B.CUT_ and cut.rate_bps == 150 * MBPS and "דיסק הספרייה" in cut.reason
    up = B.step(100 * MBPS, old(), sample(io=0.0))           # רק הדיסק נמדד, ונקי: "שקט" כמו לפני #3463
    assert up.action == B.RAISE and "דיסק" in up.reason


def test_the_default_config_keeps_the_disk_out_of_the_budget():
    assert B.Config(ceiling_bps=CEIL).disk_cuts_budget is False
