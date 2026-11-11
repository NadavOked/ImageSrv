"""#989: room history displays measured wave counts, never inventing zero."""

import re
from pathlib import Path


def _room_history_html() -> str:
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    return js.split("function roomHistoryHtml() {", 1)[1].split("\n}", 1)[0]


def test_room_history_has_waves_after_written_and_keeps_optional_columns():
    body = _room_history_html()
    columns = re.search(r"columns:\s*\[([^\]]+)\]", body)
    assert columns is not None
    assert re.findall(r'"([^"]*)"', columns.group(1)) == [
        "#", "אימג׳", "מחשבים", "נכתבו", "גלים", "משך", "מצב", "תאריך",
    ]
    assert re.search(r"opt:\s*\[6,\s*7\]", body)


def test_room_history_waves_require_a_finite_measurement_and_inactive_round():
    body = _room_history_html()
    assert re.search(
        r'active\s*&&\s*h\s*===\s*active\s*\?\s*"—"\s*:'
        r'\s*h\.waits\s*&&\s*Number\.isFinite\(h\.waits\.waves\)'
        r'\s*\?\s*String\(h\.waits\.waves\)\s*:\s*"—"',
        body,
    )


def test_room_history_does_not_use_native_dialogs():
    assert not re.search(r"\b(?:prompt|confirm)\s*\(", _room_history_html())
