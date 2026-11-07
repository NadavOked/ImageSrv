"""#2131 (mockup 03 section 1, Nadav 24/10) -- the end of a local clone is the list of drawers.

The stick shows the local clone's result screen (lc_usb=1 only adds "הפעל מחדש"), so one
screen serves the stick, the cloner and the build machine. The approved mockup: one list,
"יעדים ומקור", a row for every drawer in the machine's order with the source among them,
each row in its own colour -- not a summary card per target with the source's sentence
loose under them.

A driver loads the agent's state into the real parser, draws the real result screen and
prints every text the screen laid out (``text_make`` wrapped at link time), as
test_lc_pulled_gui_2045 does -- so it builds on the code before the change too, which is
what lets the negative control fail on the words and not on a missing symbol.

**Negative control:** with ``screens_lcrun.c`` put back as it was on origin/main (747b05e1)
the three list tests fail on the words ("יעדים ומקור" is not on the screen); the guards
below them (the source sentence once, every line of the cards still said) pass in both.
The one power-off button is test_one_poweroff_2081.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_cloner_gui import GUI, posix
from test_lc_pulled_gui_2045 import driver, pytestmark  # noqa: F401 -- the fixture and the native requirement
from test_native_local_clone import DISKS, FITS

SRC_SENTENCE = "(המקור) נקרא בלבד ולא שונה."
FAIL = ("lc_tgt=sdb|failed|91600000000|3|2||מחיצה 3: שגיאת כתיבה אחרי 91.6 GB|148600000000|0|||0\n")
OK = "lc_tgt=sda|done|148600000000|0|4||||148600000000|1|||0\n"


def texts(driver: Path, tmp_path: Path, tgts: str, *, usb: bool, src: str = "sdc") -> list[str]:
    state = (DISKS.replace("lc_phase=idle", "lc_phase=done") + FITS + ("lc_usb=1\n" if usb else "") +
             f"lc_run={src}|148600000000|148600000000|1065|0|{'usb' if usb else 'sent'}|5|\n" + tgts)
    p = tmp_path / "state.txt"
    p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]


def first(got: list[str], head: str) -> int:
    hits = [i for i, t in enumerate(got) if t.startswith(head)]
    assert hits, (head, got)
    return hits[0]


def test_the_stick_result_is_one_list_of_drawers_with_the_source_among_them(driver, tmp_path):
    got = texts(driver, tmp_path, OK + FAIL, usb=True)
    assert "יעדים ומקור" in got and "1 מתוך 2 יעדים הצליחו" in got, got
    rows = [first(got, "דיסק 1 ·"), first(got, "דיסק 2 · נכשל"), first(got, "דיסק 3 · מקור")]
    assert rows == sorted(rows), got   # the machine's order: 1, 2, 3 -- the source in the third place


def test_a_source_in_the_first_drawer_is_the_first_row(driver, tmp_path):
    got = texts(driver, tmp_path, "lc_tgt=sdb|done|148600000000|0|4||||148600000000|1|||0\n"
                "lc_tgt=sdc|done|148600000000|0|4||||148600000000|1|||0\n", usb=True, src="sda")
    rows = [first(got, "דיסק 1 · מקור"), first(got, "דיסק 2 ·"), first(got, "דיסק 3 ·")]
    assert rows == sorted(rows), got
    assert "2 מתוך 2 יעדים הצליחו" in got, got


def test_the_cloner_gets_the_same_list_without_the_sticks_restart(driver, tmp_path):
    got = texts(driver, tmp_path, OK + FAIL, usb=False)
    assert "יעדים ומקור" in got and "הפעל מחדש" not in got, got
    assert "הפעל מחדש" in texts(driver, tmp_path, OK + FAIL, usb=True)


def test_nothing_the_screen_said_is_lost_and_the_sources_sentence_is_once(driver, tmp_path):
    """Guards, true before and after: the move must not lose or duplicate what the screen said."""
    for usb in (True, False):
        got = texts(driver, tmp_path, OK + FAIL, usb=usb)
        assert "כבה את המחשב" in got, got
        assert sum(t.endswith(SRC_SENTENCE) for t in got) == 1, got
        assert any("fanout: write: Input/output error" in t or "שגיאת כתיבה אחרי" in t for t in got), got
        assert any(t.startswith("4 מחיצות הועתקו") for t in got), got


def test_a_source_the_inventory_does_not_know_keeps_its_sentence_under_the_list(driver, tmp_path):
    """No disk record for the source: no row of it (nothing invented) -- the sentence is said, once."""
    got = texts(driver, tmp_path, OK + FAIL, usb=True, src="sdz")
    assert "יעדים ומקור" in got, got
    assert not any(t.endswith("· מקור") for t in got), got
    assert sum(t.endswith(SRC_SENTENCE) for t in got) == 1, got
