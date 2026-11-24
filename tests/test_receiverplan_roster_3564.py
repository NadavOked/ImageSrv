"""#3564 / #3565: רשימת מגירות שאינה שלמה אינה מדידה -- `NOT_MEASURED` / `incomplete_roster`, בשני הכללים, בלי הצעה.

`test_receiver_limit_3564.py` בודק גל שלם, מגירה שחסרה בדיווח וגל קטן. כאן הצד של **ההחלטה על הגל**: השרת רשם רשימת מגירות
שבה רשומה בלי MAC, בלי סידורי, או שאינה אובייקט. אי-אפשר לדעת מי הייתה בגל, ולכן אין "מקלט מייצג" ואין חלוקה למהירויות --
גם אם הדיווח עצמו נראה שלם. שני הכללים (`#3564`, `#3565`) נרשמים עם אותו פסק ואותה סיבה, ובראיה אין `proposed_bps`.

**בקרה שלילית:** ראו את גוף ה-PR -- מוטציה בקוד הייצור שמפילה את הטסט על הפסק, לא על ייבוא.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.brain import receiverplan

GOOD = {"mac": "aa:bb:cc:00:00:01", "serial": "S1"}
SAMPLE = {"asked_bps": 90_000_000, "sent_bps": 80_000_000}


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _decisions(conn, wave):
    rows = conn.execute("SELECT rule, action, verdict, reason, evidence_json, outcome FROM brain_decisions"
                        " WHERE round_id = ? ORDER BY id", (wave,)).fetchall()
    return {row["rule"]: row for row in rows}


@pytest.mark.parametrize("bad", [
    {"mac": "aa:bb:cc:00:00:02"},
    {"serial": "S2"},
    {"mac": "", "serial": "S2"},
    {"mac": "aa:bb:cc:00:00:02", "serial": ""},
    "aa:bb:cc:00:00:02",
    None,
])
def test_a_roster_entry_without_a_mac_and_a_serial_makes_the_wave_not_measured(conn, bad):
    receiverplan.observe(conn, "w-bad", {"roster": [GOOD, bad]}, SAMPLE)

    rows = _decisions(conn, "w-bad")
    assert set(rows) == {"#3564", "#3565"}
    assert rows["#3564"]["action"] == "REPRESENTATIVE_RECEIVER" and rows["#3565"]["action"] == "RECEIVER_SPEED_STREAMS"
    for row in rows.values():
        assert row["verdict"] == "NOT_MEASURED" and row["reason"] == "incomplete_roster"
        assert row["outcome"] == "shadow"
        evidence = json.loads(row["evidence_json"])[0]
        assert evidence["receiver_bps"] == {} and "proposed_bps" not in evidence and "streams" not in evidence
        assert evidence["asked_bps"] == 90_000_000 and evidence["sent_bps"] == 80_000_000


def test_an_empty_roster_is_not_the_same_reason_as_a_broken_one(conn):
    """רשימה ריקה אינה רשומה פגומה: היא נופלת על חברות הגל, לא על הרשימה. הפער בין שתי הסיבות נשאר גלוי ב-`reason`."""
    receiverplan.observe(conn, "w-empty", {"roster": []},
                         {**SAMPLE, "ended": "send_done", "away_macs": [], "cloners": 0})
    rows = _decisions(conn, "w-empty")
    assert rows["#3564"]["verdict"] == "NOT_MEASURED" and rows["#3564"]["reason"] == "incomplete_membership"
