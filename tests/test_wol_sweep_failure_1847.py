"""#1847: שעון החדר ממשיך גם כש-`wol_sweep.sweep` נכשל -- הכשל נרשם בלוג ואינו נזרק.

`test_learn_wol_1847.py` בודק את השליחה החוזרת וההתראה. כאן הצד האחר: טבלת `wol_wakes` שאי-אפשר לקרוא (DB פגום, מיגרציה
באמצע) אינה מפילה את הלולאה שמריצה גם את שאר משימות החדר, והכשל אינו נבלע בלי עקבות: שורת לוג בשם `wol sweep failed`
עם החריגה. ושום חבילה לא יוצאת על בסיס מצב שלא נקרא.

**בקרה שלילית:** ראו את גוף ה-PR -- מוטציה בקוד הייצור שמפילה את הטסט על ההתנהגות, לא על ייבוא.
"""

from __future__ import annotations

import logging

import pytest

from server import db
from server.learn import wol_sweep

MAC = "78:ac:c0:9b:11:c2"
NOW = 1_760_000_000.0


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def test_a_sweep_that_cannot_read_the_wakes_does_not_raise_send_nothing_and_says_so(conn, caplog):
    conn.execute("DROP TABLE wol_wakes")
    conn.commit()
    sent = []

    with caplog.at_level(logging.ERROR, logger="imagesrv.learn_wol"):
        assert wol_sweep.sweep(conn, send=sent.append, now=NOW) is None

    assert sent == [], "מצב שלא נקרא אינו סיבה לשלוח חבילה"
    assert "wol sweep failed" in caplog.text
    assert "wol_wakes" in caplog.text, "הלוג נושא את החריגה, כדי שאפשר יהיה לדעת מה נשבר"


def test_a_healthy_sweep_logs_no_failure(conn, caplog):
    with caplog.at_level(logging.ERROR, logger="imagesrv.learn_wol"):
        assert wol_sweep.sweep(conn, send=lambda packet: None, now=NOW) is None
    assert "wol sweep failed" not in caplog.text
