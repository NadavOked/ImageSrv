"""24/11 (mypy): `room._rewake` מול גל ש-`created_at` שלו אינו תאריך. הגיל שלו לא ידוע, ולכן אין WoL חוזר -- והשורה נרשמת
ביומן השרת: מה שלא הצלחנו לקרוא נרשם ואינו מופעל, ולא נבלע (בלי הבדיקה: `now - None` זרק TypeError). בנתיב הרגיל `_unseen`
כבר מחזיר רשימה ריקה לגל כזה, ולכן הטסט מבטל אותו.

**בקרה שלילית:** על `server/rounds/room.py` של הבסיס הטסט נופל ב-TypeError.
"""

from __future__ import annotations

import logging

from server.rounds import room as room_mod

CLONER1 = "aa:bb:cc:00:00:21"


def test_rewake_of_a_wave_with_an_unreadable_created_at_sends_nothing_and_says_so(monkeypatch, caplog):
    opened = []

    class Store:
        def on_opened(self, *args):
            opened.append(args)

    def no_decision(*args, **kwargs):
        raise AssertionError("the brain was asked about a wave whose age is not known")

    monkeypatch.setattr(room_mod, "_unseen", lambda conn, store, round_row, wave: [CLONER1])
    monkeypatch.setattr(room_mod.held, "is_held", lambda conn, mac: False)
    monkeypatch.setattr(room_mod.silence, "decide_rewake", no_decision)
    wave, round_row = {"id": "ses_nodate", "created_at": "not a date"}, {"id": "rnd_nodate"}
    with caplog.at_level(logging.WARNING, logger="imagesrv.room"):
        assert room_mod._rewake(None, Store(), round_row, wave) is None
    assert opened == []
    said = [r.getMessage() for r in caplog.records if r.name == "imagesrv.room"]
    assert any("ses_nodate" in m and "created_at" in m for m in said), said
