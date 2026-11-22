"""#1842 — זיכרון הקצב לכל (מחשב שיכפול, מספר מגירות).

ברזל, 19/10, משכפל 2, אימג' של 19.5GB:

    סבב של 4 מגירות יצא ב-432m — `basis=held` מסבב של **2** מגירות
    (ses_5002758a). 963k שידורים חוזרים (~7%), 623 פקיעות, 10:27.
    הסבב הבא של 4, אחרי שהכלל ירד ×0.75 ל-324m: 437k (~3.3%), 78 פקיעות, 9:18.

הכלל (הכרעת נדב 20/10): הקצב שהוכח × 0.75 לכל הכפלה של מספר המגירות — 432m → 324m — `_from_previous`.

#3173 (נדב 21/11): udpcast נמחק. הגלים כאן הם גלי מנוע החתיכות (`decision.engine`), והקצב שהגל הקודם "מוריש" הוא
הקצב שהוא יצא בו (`unmeasured`); הכללים של udpcast (`held`, `_climb`, `unsafe_rates`, הכלל מלפני #1758) יצאו איתו.
#1842 עצמו נבדק גם ב-`test_learn_no_udpcast_3173`.
"""

from __future__ import annotations

import json
import re

import pytest

from console_src import console_js
from server import db
from server.rounds import drive_rates

CLONER2 = "aa:bb:cc:00:00:41"
OTHER = "aa:bb:cc:00:00:42"


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _drives(session: str, drawers: int, mac: str = CLONER2) -> list[dict]:
    return [{"serial": f"{session}-{mac[-2:]}-{k}", "model": "EVO 870", "mac": mac, "port": k + 1}
            for k in range(drawers)]


def _chunk_wave(conn, session: str, rate: str, drawers: int, mac: str = CLONER2) -> None:
    """גל חתיכות שיצא ב-`rate` עם `drawers` מגירות למכונה — כפי ש-`decide` שומר אותו (`engine: chunk`)."""
    roster = [{"mac": d["mac"], "serial": d["serial"], "model": d["model"]} for d in _drives(session, drawers, mac)]
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?, ?, ?)",
                 (session, json.dumps({"max_bitrate": rate, "roster": roster, "engine": "chunk"}),
                  "2026-11-21T08:00:00+00:00"))
    conn.commit()


def _next(conn, drawers: int, mac: str = CLONER2) -> dict:
    return drive_rates.choose(conn, _drives("next", drawers, mac), None)


def test_four_drawers_do_not_start_at_the_rate_of_two(conn):
    _chunk_wave(conn, "ses_two", "432m", drawers=2)
    two = _next(conn, 2)
    assert (two["max_bitrate"], two["basis"]) == ("432m", "unmeasured"), two
    four = _next(conn, 4)
    assert (four["max_bitrate"], four["basis"]) == ("324m", "drawers"), four
    assert four["previous"]["drawers"] == 2 and four["previous"]["mac"] == CLONER2


def test_two_four_two_each_count_starts_from_its_own_memory(conn):
    _chunk_wave(conn, "ses_a", "432m", drawers=2)
    _chunk_wave(conn, "ses_b", "324m", drawers=4)
    assert _next(conn, 2)["max_bitrate"] == "432m"
    assert _next(conn, 4)["max_bitrate"] == "324m"


def test_the_journal_says_the_rate_came_from_another_drawer_count(conn):
    _chunk_wave(conn, "ses_two", "432m", drawers=2)
    drive_rates.decide(conn, "ses_four", lambda: _drives("ses_four", 4), None)
    line = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                        " ORDER BY id DESC").fetchone()["detail"]
    assert line.startswith("ses_four 324m basis=drawers from=ses_two 432m"), line
    assert "drawers=2/4" in line, line


def test_the_room_screen_explains_the_drawers_basis():
    """הבסיס מוסבר למפעיל במסך החדר, ולא מוצג כמילה גולמית."""
    source = console_js()
    table = source[source.index("const WAVE_RATE_WHY"):]
    table = table[:table.index("};")]
    assert re.search(r"\bdrawers:\s*\"[^\"]+\"", table), table


def test_from_more_drawers_fewer_are_not_scaled(conn):
    _chunk_wave(conn, "ses_four", "324m", drawers=4)
    got = _next(conn, 2)
    assert (got["max_bitrate"], got["basis"], got["previous"]["drawers"]) == ("324m", "unmeasured", 4), got


def test_a_row_without_a_roster_is_skipped_not_thrown(conn):
    conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?, ?, ?)",
                 ("ses_bare", json.dumps({"max_bitrate": "432m", "engine": "chunk"}), "2026-11-21T08:00:00+00:00"))
    conn.commit()
    assert _next(conn, 2)["basis"] == "start"


def test_with_two_cloners_each_uses_its_own_count_and_the_minimum_rules(conn):
    _chunk_wave(conn, "ses_c2", "432m", drawers=2)
    _chunk_wave(conn, "ses_o", "500m", drawers=1, mac=OTHER)
    drives = _drives("next", 4) + _drives("next", 1, OTHER)
    got = drive_rates.choose(conn, drives, None)
    assert (got["max_bitrate"], got["basis"], got["previous"]["mac"]) == ("324m", "drawers", CLONER2), got
