"""‏#1842 — זיכרון הקצב לכל (מחשב שיכפול, מספר מגירות).

ברזל, 19/10, משכפל 2, אימג' של 19.5GB:

    סבב של 4 מגירות יצא ב-432m — ‏`basis=held` מסבב של **2** מגירות
    (‏ses_5002758a). ‏963k שידורים חוזרים (~7%), ‏623 פקיעות, ‏10:27.
    הסבב הבא של 4, אחרי שהכלל ירד ×0.75 ל-324m: ‏437k (~3.3%), ‏78 פקיעות, ‏9:18.

הכלל הקודם: מגל עם **פחות** מגירות — "לכל היותר הקצב שהוכח" — כלומר הקצב
של 2 מגירות כמות שהוא על 4. הכלל החדש (הכרעת נדב 20/10): הקצב שהוכח × 0.75
לכל הכפלה של מספר המגירות — 432m → 324m, הקצב שהברזל הוכיח ל-4 —
‏`_from_previous`, והנימוק והמקור שם.

הטסטים ממלאים את `wave_rates` דרך הפונקציות של הייצור (`record_stream`,
‏`record_wave`) — העזרים של #1758.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from server import db, drive_rates

from test_rate_by_goodput_1758 import (  # noqa: F401 — ה-fixture ‏conn
    CLONER2, OTHER, SENT, _drives, _next, _seed, conn)

STATIC = Path(__file__).resolve().parents[1] / "server" / "static"


def _fast(rate_bits: float, eff: float = 0.92) -> float:
    return SENT / (eff * rate_bits / 8)


def _held(conn, rate: str, drawers: int, mac: str = CLONER2) -> str:
    """גל שהשיא שלו נעצר (‏`held`): הזרם רץ ב-92% מהקצב, אבדן נמוך, ‏6 פקיעות
    לכל GB — מעל TIMEOUTS_PER_GB_HELD. כמו ses_5002758a: ‏432m, ‏held."""
    bits = drive_rates.parse_bitrate(rate)
    return _seed(conn, rate, loss=0.003, wall=_fast(bits), drawers=drawers, mac=mac,
                 timeouts=round(6 * SENT / 10 ** 9))


# --- 1. המקרה מהברזל --------------------------------------------------------


def test_four_drawers_do_not_start_at_the_held_rate_of_two(conn):
    """רק היסטוריה של 2 מגירות (‏432m, ‏held); הגל הבא של 4 — **לא** 432m.
    ‏432 × 0.75 = 324m (הכפלה אחת), והבסיס אומר שהוא נגזר ממספר מגירות אחר."""
    _held(conn, "432m", drawers=2)
    two = _next(conn, drawers=2)
    assert two["max_bitrate"] == "432m" and two["basis"] == "held", two
    four = _next(conn, drawers=4)
    assert four["max_bitrate"] == "324m" and four["basis"] == "drawers", four
    assert four["previous"]["drawers"] == 2 and four["previous"]["mac"] == CLONER2


def test_two_four_two_each_count_starts_from_its_own_memory(conn):
    """תוכנית הברזל, ‏2←4←2: אחרי שהגל של 4 נמדד, ה-4 הבא ממשיך מהזיכרון
    **של 4** (טיפוס מ-324m), וה-2 הבא — מהזיכרון **של 2** (‏432m), בלי
    שהגל של 4 נוגע בו."""
    _held(conn, "432m", drawers=2)
    _seed(conn, "324m", loss=0.003, wall=_fast(324e6), drawers=4)
    four = _next(conn, drawers=4)
    assert four["max_bitrate"] == "424m" and four["basis"] == "climb", four
    assert four["previous"]["drawers"] == 4
    two = _next(conn, drawers=2)
    assert two["max_bitrate"] == "432m" and two["basis"] == "held", two
    assert two["previous"]["drawers"] == 2


def test_the_journal_says_the_rate_came_from_another_drawer_count(conn):
    _held(conn, "432m", drawers=2)
    drive_rates.decide(conn, "ses_four", lambda: _drives("ses_four", 4), None)
    line = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                        " ORDER BY id DESC").fetchone()["detail"]
    assert line.startswith("ses_four 324m basis=drawers(best=432m"), line
    assert "drawers=2/4" in line, line


def test_the_room_screen_explains_the_drawers_basis():
    """הבסיס החדש מוסבר למפעיל במסך החדר, ולא מוצג כמילה גולמית."""
    source = (STATIC / "console.js").read_text(encoding="utf-8")
    table = source[source.index("const WAVE_RATE_WHY"):]
    table = table[:table.index("};")]
    assert re.search(r"\bdrawers:\s*\"[^\"]+\"", table), table


# --- 2. הכלל הקודם (גלים מלפני #1758) ---------------------------------------


def _legacy(conn, session: str, rate: str, drawers: int) -> None:
    """גל מלפני #1758: מונה שולח נקי, בלי `delivered` — אין נקודת goodput,
    ומקלט שלא דיווח (‏`drops` ‏NULL) — ‏`unmeasured`, נשאר."""
    roster = [{"mac": CLONER2, "serial": f"{session}-{k}", "model": "EVO"}
              for k in range(drawers)]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, stream)"
                 " VALUES (?,?,?,?)",
                 (session, json.dumps({"max_bitrate": rate, "roster": roster}),
                  "2026-10-16T10:00:00+00:00",
                  json.dumps({"sent_bytes": SENT, "loss_blocks": 0})))
    conn.commit()


def test_a_legacy_wave_of_two_drawers_is_scaled_for_four_too(conn):
    _legacy(conn, "ses_old", "432m", drawers=2)
    got = _next(conn, drawers=4)
    assert got["max_bitrate"] == "324m" and got["basis"] == "drawers", got


def test_a_row_without_a_roster_is_skipped_not_thrown(conn):
    """רשומה ישנה בלי `roster` אינה של אף מכונה — אינה נזרקת ואינה מפילה
    את הבחירה; המכונה נשענת על מה שכן ידוע עליה."""
    _legacy(conn, "ses_old", "432m", drawers=2)
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                 ("ses_ancient", json.dumps({"max_bitrate": "900m"}),
                  "2026-10-01T10:00:00+00:00"))
    conn.commit()
    got = _next(conn, drawers=4)
    assert got["max_bitrate"] == "324m" and got["previous"]["session_id"] == "ses_old"


# --- 3. הכיוון השני, ו-unsafe_rates -----------------------------------------


def test_from_more_drawers_fewer_are_not_scaled(conn):
    """קצב שעמד ב-4 מגירות בטוח ל-2 — כמות שהוא, והחיפוש ממשיך ממנו."""
    _seed(conn, "300m", loss=0.003, wall=_fast(300e6), drawers=4)
    got = _next(conn, drawers=2)
    assert got["max_bitrate"] == "400m" and got["basis"] == "climb", got
    assert got["previous"]["drawers"] == 4


def test_a_rate_unsafe_with_four_does_not_wall_two(conn):
    """‏532m הפיל מקלט עם 4 מגירות. עם 2 מגירות, שהשיא שלהן 432m — הקצב
    הזה אינו קיר: עולים ל-532m."""
    _seed(conn, "532m", loss=0.003, wall=_fast(532e6), drawers=4, dropped=1)
    _seed(conn, "432m", loss=0.003, wall=_fast(432e6), drawers=2)
    got = _next(conn, drawers=2)
    assert got["max_bitrate"] == "532m" and got["basis"] == "climb", got


def test_a_rate_unsafe_with_two_walls_four(conn):
    """‏532m הפיל מקלט עם 2 מגירות — עם 4 (יותר עומס במקלט) הוא קיר גם כן."""
    _seed(conn, "532m", loss=0.003, wall=_fast(532e6), drawers=2, dropped=1)
    _seed(conn, "432m", loss=0.003, wall=_fast(432e6), drawers=4)
    got = _next(conn, drawers=4)
    assert got["max_bitrate"] == "432m" and got["basis"] == "peak", got


def test_a_derived_rate_stays_below_a_rate_unsafe_with_these_drawers(conn):
    """היסטוריה של 2 מגירות בלבד (‏600m → נגזר 450m ל-4), אבל בזיכרון
    הקבוע (`unsafe_rates`) רשום ש-280m הפיל מקלט עם 4 מגירות — הגל של 4
    יוצא מתחת לו (‏×0.75), לא בקצב הנגזר."""
    _held(conn, "600m", drawers=2)
    conn.execute("INSERT INTO unsafe_rates (mac, drawers, rate_bps, session_id, at)"
                 " VALUES (?,?,?,?,?)",
                 (CLONER2, 4, 280_000_000, "ses_long_ago", "2026-10-01T10:00:00+00:00"))
    conn.commit()
    got = _next(conn, drawers=4)
    assert got["max_bitrate"] == "210m" and got["basis"] == "drawers", got


# --- 4. כמה מחשבי שיכפול ----------------------------------------------------


def test_with_two_cloners_each_uses_its_own_count_and_the_minimum_rules(conn):
    """משכפל 2 עם 4 מגירות (היסטוריה רק של 2: ‏432m → ‏324m) ומשכפל אחר עם
    2 מגירות (היסטוריה של 2: ‏400m, ‏held) — הגל במינימום, 324m, לפי משכפל 2."""
    _held(conn, "432m", drawers=2)
    _held(conn, "400m", drawers=2, mac=OTHER)
    drives = _drives("next", 4, CLONER2) + _drives("next", 2, OTHER)
    got = drive_rates.choose(conn, drives, None)
    assert got["max_bitrate"] == "324m" and got["basis"] == "drawers", got
    assert got["previous"]["mac"] == CLONER2 and got["previous"]["drawers"] == 2
