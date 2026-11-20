"""#3837 -- K5 `FILL_SOURCE` נרשם פעם אחת לכל (גל, מגירה, מחיצה), ופעם נוספת רק כשההחלטה (verdict, source, שכנים) משתנה.

לפני התיקון `_record` כתב שורה בכל דיווח התקדמות (15 שורות ב-59 שניות, עם 2 תכנים שונים בלבד). הטסט מזין דיווחים חוזרים דרך ה-API
(כשחלקם נבדלים רק ב-`missing` וב-`away_s`, שמשתנים בכל דיווח) וסופר שורות ב-`brain_decisions`.

**בקרה שלילית:** `server/brain/fill.py` חוזר לבסיס; `test_identical_reports_write_one_row` נופל ב-`assert 1 == 15`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_prodline_3181 import _nine, _open, _progress, _t, _wave  # noqa: E402
from test_server_room import CLONER1, room_server  # noqa: E402,F401


def _rows(rs):
    return rs["ctx"].conn.execute("SELECT * FROM brain_decisions WHERE action = 'FILL_SOURCE' ORDER BY id").fetchall()


def _report(rs, wave, missing=30, away_s=2, neighbors=True):
    targets = [_t("sda", "A1", 1, "writing", rejoin={"part": 3, "missing": missing, "chunks": 18254, "away_s": away_s})]
    if neighbors:
        targets += [_t("sdb", "A2", 2, "done"), _t("sdc", "A3", 3)]
    return _progress(rs, wave, CLONER1, targets)


def _open_wave(rs):
    _nine(rs)
    assert _open(rs["deploy"], 10).status_code == 200
    return _wave(rs)


def test_identical_reports_write_one_row(room_server):
    wave = _open_wave(room_server)
    for i in range(15):
        _report(room_server, wave, missing=30 - (i % 2), away_s=2 + i)    # מה שמשתנה בכל דיווח: חסר ו-away_s
    rows = _rows(room_server)
    assert len(rows) == 1, [dict(r) for r in rows]


def test_a_changed_plan_writes_a_new_row(room_server):
    wave = _open_wave(room_server)
    for _ in range(3):
        _report(room_server, wave)
    assert len(_rows(room_server)) == 1
    _report(room_server, wave, neighbors=False)                           # אין שכנים: מקור/שכנים אחרים
    after = _rows(room_server)
    assert len(after) == 2, [dict(r) for r in after]
    assert after[0]["evidence_json"] != after[1]["evidence_json"]
    for _ in range(3):
        _report(room_server, wave, neighbors=False)
    assert len(_rows(room_server)) == 2
