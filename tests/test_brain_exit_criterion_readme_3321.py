"""#3321: ה-README של המוח מחזיק טבלת קריטריון יציאה מצל לכל כלל שנרשם, והיא לא נסחפת מהקוד.

כלל חדש ב-`forecast.PREDICTS` בלי שורה בטבלה נופל כאן -- אחרת "לכל כלל פעיל יש שורה" היה נכון רק ביום שנכתב.
זה **לא** קובע מהי "התאמה" (הכרעת נדב): הוא רק מחזיק את התיעוד צמוד לקוד.

**בקרה שלילית:** על `server/brain/README.md` של הבסיס (בלי הסעיף) כל הטסטים כאן נופלים בהתנהגות.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.brain import forecast

README = (Path(__file__).resolve().parent.parent / "server" / "brain" / "README.md").read_text(encoding="utf-8")
SECTION = README.split("## קריטריון היציאה מצל, לכל כלל (#3321)", 1)
TABLE = SECTION[1].split("\n## ", 1)[0] if len(SECTION) == 2 else ""


def _rows() -> dict[str, list[str]]:
    rows = {}
    for line in TABLE.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| `") and len(cells) == 5:
            rows[cells[0].strip("`")] = cells
    return rows


def test_the_readme_has_the_exit_criterion_section():
    assert len(SECTION) == 2, "אין סעיף 'קריטריון היציאה מצל, לכל כלל (#3321)' ב-README של המוח"


@pytest.mark.parametrize("rule", sorted(forecast.PREDICTS))
def test_every_rule_that_is_recorded_has_a_row(rule):
    assert rule in _rows(), f"{rule} נרשם ב-forecast.PREDICTS ואין לו שורה בטבלת הקריטריון ב-README"


def test_the_table_has_no_row_for_a_rule_the_code_does_not_know():
    assert set(_rows()) == set(forecast.PREDICTS)


def test_only_a_rule_with_a_tolerance_is_described_as_having_a_match_definition():
    """כלל בלי `TOLERANCE` אינו מקבל בטבלה הגדרת התאמה (עיקרון 5: 'לא נמדד' אינו 'התאים')."""
    for rule, cells in _rows().items():
        if rule in forecast.TOLERANCE:
            assert "±" in cells[2], f"{rule} יש לו סטייה מותרת בקוד והטבלה לא אומרת אותה"
        elif rule not in ("K2", "K10", "K8"):
            assert "±" not in cells[2], f"{rule} אין לו סטייה מותרת בקוד, והטבלה טוענת שיש"
