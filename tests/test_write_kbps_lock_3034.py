"""#3034/#3260: נעילת `--write-kbps` למדידה -- כדי שהשוואה בין המנוע הישן לחדש לא תנוע ב-±20% של בדיקת הכתיבה.

`IMAGESRV_MEASURE_WRITE_KBPS` (משתנה סביבה של השרת; לא הגדרה בקונסולה) קובע את `--write-kbps` בדיוק, ונרשמת שורת
יומן `write_kbps_locked`. בלעדיו -- ההתנהגות של היום: האיטי מבין בדיקות הכתיבה × `WRITE_MARGIN`.
המספרים כאן הם מספרים של הטסט (בדיקות כתיבה של 120-300 MB/s), לא מדידה.

**בקרה שלילית:** `server/rounds/chunksend.py` חזר לבסיס -- הטסטים המסומנים (neg) נופלים: הפקודה יוצאת בקצב שהחישוב
נתן ולא בערך שנקבע, ושורת היומן חסרה. השומרים עוברים בשני המצבים.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")

from server.brain import writerate
from server.rounds import chunksend

from test_chunksend_2533 import rig, wait_for   # noqa: F401 -- rig is a fixture

CHECKS = {"a": 300, "b": 120, "c": 250}                          # MB/s: the write check of each machine's drive
COMPUTED = int(min(CHECKS.values()) * writerate.MARGIN) * 8000     # today: the slowest x the margin
LOCK = 1_234_000
ENV = "IMAGESRV_MEASURE_WRITE_KBPS"


def _first_write_kbps(rig) -> int:
    rig.make()
    for mac, mbps in CHECKS.items():
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=mbps)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    return int(cmd[cmd.index("--write-kbps") + 1])


def test_the_lock_sets_write_kbps_exactly_and_says_so(rig, monkeypatch, caplog):
    """(neg) The value in the environment is `--write-kbps`, whatever the write checks said; one `write_kbps_locked` line."""
    monkeypatch.setenv(ENV, str(LOCK))
    with caplog.at_level(logging.INFO, logger="imagesrv.chunksend"):
        got = _first_write_kbps(rig)
    assert got == LOCK != COMPUTED, got
    assert [r.getMessage() for r in caplog.records if "write_kbps_locked" in r.getMessage()], caplog.text


def test_without_the_lock_it_is_the_slowest_check_as_today(rig, monkeypatch, caplog):
    """Guard, both states: no variable -- the computation, and no `write_kbps_locked` line."""
    monkeypatch.delenv(ENV, raising=False)
    with caplog.at_level(logging.INFO, logger="imagesrv.chunksend"):
        got = _first_write_kbps(rig)
    assert got == COMPUTED, got
    assert "write_kbps_locked" not in caplog.text


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "1.5"])
def test_a_lock_that_is_not_a_positive_number_is_said_and_not_applied(rig, monkeypatch, caplog, bad):
    """Guard, both states for the value: a malformed lock is a warning and the computed rate -- never a silent 0 or a stopped wave."""
    monkeypatch.setenv(ENV, bad)
    with caplog.at_level(logging.INFO, logger="imagesrv.chunksend"):
        got = _first_write_kbps(rig)
    assert got == COMPUTED, got
    assert "write_kbps_locked" not in caplog.text
