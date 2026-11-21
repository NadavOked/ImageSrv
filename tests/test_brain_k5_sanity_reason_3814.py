"""#3814 (אפיק #3653) -- בדיקת ההיגיון של K5 אומרת **איזה** קלט חסר, והקצב האחרון ששודר נשמר.

ב-19/11 (LabRoot, `brain_decisions`, rule K5, FILL_SOURCE) ב-562 מתוך 993 שורות היה `sanity=unmeasured`: ב-403 מהן `away_s` לא נאמר
(סבב אחד; K6 באותו סבב `away_s=unmeasured`), וב-159 `away_s` נאמר (K6: 81) ו-`stream_bps` חסר -- `sending_bps()` מחזיר 0 כשהגל כבר לא
`sending`/`repairing`, וההשלמה נמשכת אחריו. הטסט הראשון נועל את הסיבה בשורה; השני את הקצב שנשמר.

**בקרה שלילית:** `server/brain/fill.py` חוזר לבסיס: הראשון נופל (`sanity=unmeasured` בלי סיבה), והשני נופל (השורה השנייה `unmeasured`).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_brain_fill_limits_3659 import MIB, _fill, _open_wave, _rejoin_report, _rows  # noqa: E402
from test_server_room import room_server  # noqa: E402,F401


def _sanity(rs):
    out = []
    for row in _rows(rs, "K5"):
        if row["action"] == "FILL_SOURCE":
            out.append([e for e in __import__("json").loads(row["evidence_json"]) if e.startswith("sanity")][0])
    return out


def _need(away):
    return _fill().Need(30, 18254, 3, away)


def test_the_row_says_which_input_is_missing():
    fill = _fill()
    rate = 100 * MIB
    assert "unmeasured" not in fill.decide(_need(21.0), [], 0, 1, rate, rate).evidence[-1]
    for away, stream, want in ((None, rate, "away"), (21.0, None, "stream"), (21.0, 0, "stream"), (None, None, "away+stream")):
        got = fill.decide(_need(away), [], 0, 1, rate, stream).evidence[-1]
        assert got == f"sanity=unmeasured:{want}", (away, stream, got)


def test_the_last_rate_sent_is_held_after_the_wave_stops_sending(room_server, monkeypatch):
    rates = iter([106 * MIB // 1, 0, 0])

    real = room_server["ctx"].chunk

    class Engine:
        def __getattr__(self, name):                 # everything else is the real engine's
            return getattr(real, name)

        def sending_bps(self):
            return next(rates) * 8

    monkeypatch.setattr(room_server["ctx"], "chunk", Engine())
    wave = _open_wave(room_server)
    for i in range(3):                              # #3837: an identical decision is not a new row, so the plan changes each report
        _rejoin_report(room_server, wave, neighbors=i % 2 == 0)
    got = _sanity(room_server)
    assert len(got) >= 1 and all(g.startswith("sanity: away 2s x 106MiB/s") for g in got), got
    assert len(got) == 3, "the 2nd and 3rd reports came after the wave stopped sending: the held rate, not unmeasured"
