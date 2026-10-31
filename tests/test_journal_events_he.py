"""‏#292: כל אירוע יומן שנכתב בקוד חייב תווית עברית ב-`EVENTS_HE`.

‏`translate` נופל בכוונה למפתח עצמו (`EVENTS_HE.get(event, event)`) — ולכן
אירוע חדש בלי תווית לא נשבר, הוא רק מגיע למפעיל כ-`room_tick_failed` בתוך
ממשק עברי. כשנפתח ה-Issue היו ארבעה כאלה; כשנסגר — 54. ההצלבה כאן היא מה
שמונע את הסחיפה: מפתח מילולי ב-`journal(conn, "…")` שאינו בטבלה מפיל את
החבילה **בשם**. מפתחות שמגיעים ממשתנה (‏`verdict.event`, ‏`on_event` של
השולח, ‏`sessions.py`) אינם נראים כאן — הם מכוסים במקום שבו הם מוגדרים."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.console import journal_he

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"
# ‏journal(<conn>, "<event>" …) — הארגומנט הראשון הוא חיבור בכל צורותיו
# (‏conn / ctx.conn / self.conn / runtime.conn), השני מחרוזת מילולית.
_CALL = re.compile(r'\bjournal\(\s*[A-Za-z_][\w.]*\s*,\s*(?:"([a-z0-9_]+)"|\'([a-z0-9_]+)\')')
# ‏journal(conn, "a" if x else "b", …) — שני המפתחות נכתבים בפועל.
_TERNARY = re.compile(r'\bjournal\(\s*[A-Za-z_][\w.]*\s*,\s*"([a-z0-9_]+)"\s+if\s+[^,]+?\s+else\s+"([a-z0-9_]+)"')


def written_events() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(SERVER.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for m in _CALL.finditer(line):
                found.setdefault(m.group(1) or m.group(2), []).append(f"{path.relative_to(REPO)}:{line_no}")
            for m in _TERNARY.finditer(line):
                found.setdefault(m.group(2), []).append(f"{path.relative_to(REPO)}:{line_no}")
    return found


def test_the_scan_sees_the_events_it_was_written_for():
    found = written_events()
    assert "room_tick_failed" in found, "הסריקה לא רואה את האירוע שפתח את #292"
    assert "user_enabled" in found, "הסריקה לא רואה את הצד השני של ternary"
    assert len(found) > 100, len(found)


def test_every_written_event_has_a_hebrew_label():
    found = written_events()
    missing = {event: where for event, where in found.items() if event not in journal_he.EVENTS_HE}
    assert not missing, "אירועי יומן בלי תווית עברית (המפעיל רואה מפתח באנגלית): " + ", ".join(
        f"{event} ({where[0]})" for event, where in sorted(missing.items()))


@pytest.mark.parametrize("event", sorted(journal_he.EVENTS_HE))
def test_labels_are_hebrew_and_not_the_key(event):
    label = journal_he.EVENTS_HE[event]
    assert label != event
    assert re.search(r"[֐-׿]", label), f"{event}: התווית אינה בעברית: {label!r}"


# --- ‏#1991: הגדרות שהקונסולה כותבת — `setting_change` מתרגם לפי SETTINGS_HE ---

def test_every_console_setting_has_a_hebrew_label():
    """‏`SETTINGS_HE.get(key, key)` נופל למפתח עצמו, ולכן הגדרה חדשה בלי תווית
    לא נשברת — היא מגיעה ליומן העברי כ-`readback_full: פעיל`. ההצלבה מול
    `WRITE_SETTINGS` (מה שהקונסולה באמת כותבת) היא מה שמונע את הסחיפה."""
    pytest.importorskip("fastapi")
    from server.console import console_api
    assert len(console_api.WRITE_SETTINGS) > 10, "ההצלבה לא רואה את ההגדרות שנכתבה בשבילן"
    missing = sorted(k for k in console_api.WRITE_SETTINGS if k not in journal_he.SETTINGS_HE)
    assert missing == [], f"הגדרות בלי תווית עברית ב-SETTINGS_HE: {missing}"


@pytest.mark.parametrize("key", sorted(journal_he.SETTINGS_HE))
def test_setting_labels_are_hebrew_and_not_the_key(key):
    label = journal_he.SETTINGS_HE[key]
    assert label != key
    assert re.search(r"[֐-׿]", label), f"{key}: התווית אינה בעברית: {label!r}"
