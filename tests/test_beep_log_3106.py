"""#3064 (G5), #3106: כל צפצוף שהושמע נרשם ביומן הסוכן — שעה וסוג.

**הפער (`lab/vm-1113c`, cf58981a):** `agent/lib/core/roundbeep.sh` רשם רק צפצוף **שלא** נשמע (`_rb_unheard`:
אין רמקול, כבוי בהגדרות, הכתיבה נכשלה). צליל שכן נשמע לא השאיר שום שורה, ולכן אי אפשר היה לשחזר מה המפעיל
שמע ומתי. ב-#3106 ("הרשת חזרה" נשמע כשהרשת עוד לא עבדה) זו בדיוק השורה שהייתה צריכה להיות ביומן.

**מה נבדק:** כל אחד מחמשת הצלילים שהושמע — שורה אחת, עם חותמת השעה של `log` ושם הצליל. צליל שלא
הושמע — אין שורת "played" (ראיה חיובית בלבד: שורה רק אחרי שכל הטונים נכתבו לרמקול).
"""

from __future__ import annotations

import re

import pytest

from native import requires_native
from test_agent import BASH
from test_round_beep_1967 import _beep, _tones

pytestmark = requires_native(("bash", BASH))

# #3850: no clock.ok in this room, so the stamp is "[HH:MM:SS rtc]"; a line after the server vouched for the clock is "[HH:MM:SS]"
STAMPED = re.compile(r"^\[\d\d:\d\d:\d\d( rtc)?\] beep (\w+): played$")


def _played(log: str) -> list[str]:
    return [m.group(2) for m in map(STAMPED.match, log.splitlines()) if m]


@pytest.mark.parametrize("tune", ["success", "failure", "netlost", "netback", "pulled"])
def test_a_tune_that_was_played_is_one_stamped_line_with_its_name(tmp_path, tune):
    events, log = _beep(tmp_path, tune)
    assert _tones(events), "the tune did not reach the speaker"
    assert _played(log) == [tune], log
    assert "not heard" not in log


@pytest.mark.parametrize("case", ["no_speaker", "off", "classroom"])
def test_a_tune_that_was_not_played_says_why_and_never_played(tmp_path, case):
    events, log = _beep(tmp_path, "success", speaker=case != "no_speaker",
                        ui={"cloner_beep": False} if case == "off" else None,
                        role="classroom" if case == "classroom" else "cloner")
    assert events == []
    assert "beep success:" in log and "-- not heard" in log
    assert _played(log) == [], log


def test_a_speaker_that_cannot_be_written_is_not_logged_as_played(tmp_path):
    # the evdev node is there by name, and a write to it fails: a directory where the device should be
    (tmp_path / "dev" / "input" / "event5").mkdir(parents=True)
    with pytest.raises(OSError):            # the helper reads the "node" back after the run: a directory
        _beep(tmp_path, "failure")
    log = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert "beep failure: writing to" in log and "failed -- not heard" in log
    assert _played(log) == [], log
