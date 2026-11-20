"""#3446: `brain_decisions.predicted` ריק בכל החלטות הצל -- מי חוזה מה נרשם במקום אחד (`forecast.PREDICTS`), ובלי
להגדיר "התאמה" (זו הכרעת נדב ב-#3321).

**בקרה שלילית:** עם `server/brain/forecast.py` בבסיס אין `PREDICTS`, והטסטים כאן נופלים בהתנהגות (לא ב-import של מודול
שנעלם). הטסטים של B5 ו-K5 בקובצי ה-3064 שלהם נופלים כש-`parallelhttp.py` ו-`route.py` בבסיס.
"""

from __future__ import annotations

import pytest

from server.brain import forecast

#: מי שנרשם ב-`brain_decisions` בשלושת הסבבים על הברזל (15/11, `771ddfe2b`): `brainreport`, סעיף 1
LAB_RULES = ("B5", "B23", "B31", "D29", "K4", "K5", "K7", "K8", "K9", "K10", "LINE", "S-AGENT")


@pytest.mark.parametrize("rule", LAB_RULES)
def test_every_rule_the_lab_recorded_says_what_it_predicts_or_why_it_does_not(rule):
    got = forecast.predicts(rule)
    assert got is not None, f"{rule} נרשם בגלים ואין לו שורה ב-PREDICTS: האם הוא חוזה מספר?"
    unit, what = got
    assert isinstance(what, str) and len(what) > 8, "אין הסבר"
    assert unit is None or (isinstance(unit, str) and unit), "יחידה ריקה"


def test_the_rules_that_predict_a_number_are_the_ones_that_record_one():
    """K3 (כבר), B5 ו-K5 (#3446). כלל חדש שהופך מספר לתחזית מוסיף את עצמו כאן."""
    numeric = {rule for rule, (unit, _) in forecast.PREDICTS.items() if unit is not None}
    assert numeric == {"K3", "K5", "B5"}


def test_a_destructive_or_binary_rule_is_not_given_a_number_to_look_measured():
    """K2/K10 הן פעולות הרסניות: אפס מקרים, ו-`judge` מחזיר None. התחזית שלהן אינה "0"."""
    for rule in ("K2", "K10", "K4", "K9"):
        assert forecast.predicts(rule)[0] is None
        assert forecast.judge(rule, 5, 5) is None


def test_the_tolerance_is_not_part_of_this_change():
    """ההגדרה של "התאמה" היא של נדב (#3321): רק K3 יש לו ניחוש זמני, ושום כלל חדש לא קיבל אחד."""
    assert set(forecast.TOLERANCE) == {"K3"}
