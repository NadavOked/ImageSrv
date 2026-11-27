"""#989: room history displays measured wave counts, never inventing zero."""

import re

from console_src import console_js


def _room_history_html() -> str:
    js = console_js()
    return js.split("function roomHistoryHtml() {", 1)[1].split("\n}", 1)[0]


def test_room_history_has_waves_after_written_and_keeps_optional_columns():
    """#3978 (נדב 21/11): "קצב ואיבוד" ירדה, ו"הרשת" נכנסה בין "מצב" ל"תאריך"; שאר העמודות והסדר שלהן כמו קודם."""
    body = _room_history_html()
    columns = re.search(r'const cols = \[([^\]]+)\];', body)
    assert columns is not None
    assert re.findall(r'"([^"]*)"', columns.group(1)) == ["#", "אימג׳", "מחשבים", "נכתבו", "גלים", "משך", "מצב", "הרשת", "תאריך"]
    assert "קצב ואיבוד" not in body and "memFlowCell" not in body
    assert re.search(r"opt:\s*\[6,\s*7,\s*8\]", body)


def test_room_history_waves_require_a_finite_measurement_and_inactive_round():
    body = _room_history_html()
    assert re.search(
        r'live\s*\?\s*"—"\s*:'
        r'\s*h\.waits\s*&&\s*Number\.isFinite\(h\.waits\.waves\)'
        r'\s*\?\s*String\(h\.waits\.waves\)\s*:\s*"—"',
        body,
    )


def test_room_history_does_not_use_native_dialogs():
    assert not re.search(r"\b(?:prompt|confirm)\s*\(", _room_history_html())
