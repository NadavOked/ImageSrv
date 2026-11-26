"""#3997 -- the local clone's heading names its targets with a space before the "ו".

Metal 22/11, cloner 1, two targets: the heading read "מעתיק לדיסק 1ולדיסק 3 במקביל". The names were joined by
"ול" with no space, and in RTL a digit that ends a name sticks to the word after it. Two targets: "לדיסק 1 ולדיסק 3";
three: "לדיסק 1, לדיסק 2 ולדיסק 3". The screens_lcrun.c heading is the one the cloner's own local clone shows.

A driver draws the real copying screen and prints every text it made (as test_lc_end_list_2131 does), so it builds on the
code before the fix too and the negative control fails on the words, not on a missing symbol.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_cloner_gui import GUI, posix
from test_lc_pulled_gui_2045 import driver, pytestmark  # noqa: F401 -- the fixture and the native requirement
from test_native_local_clone import DISKS, FITS

FOUR = DISKS + "lc_disk=4|sdd|Kingston|S4|480103981056|ok|ok|Ubuntu|2|38000000000|480103981056|1|\n"
WRITING = "lc_tgt={dev}|writing|1000||0||||0||-1,-1|0|0\n"
FAILED = "lc_tgt={dev}|failed|1000|3|2||מחיצה 3: שגיאת כתיבה|1000|0|||0\n"


def heading(driver: Path, tmp_path: Path, *tgts: str) -> str:
    state = (FOUR.replace("lc_phase=idle", "lc_phase=copying") + FITS + "lc_run=sdc|148600000000|1000|60|0||1|\n" + "".join(tgts))
    p = tmp_path / "state.txt"
    p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    got = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    hits = [t for t in got if t.startswith("מעתיק לדיסק") or t.startswith("ממשיך להעתיק לדיסק")]
    assert len(hits) == 1, got
    return hits[0]


def test_two_targets_have_a_space_before_the_vav(driver, tmp_path):
    assert heading(driver, tmp_path, WRITING.format(dev="sda"), WRITING.format(dev="sdd")) == "מעתיק לדיסק 1 ולדיסק 4 במקביל"


def test_three_targets_are_a_list_with_the_vav_on_the_last(driver, tmp_path):
    got = heading(driver, tmp_path, WRITING.format(dev="sda"), WRITING.format(dev="sdb"), WRITING.format(dev="sdd"))
    assert got == "מעתיק לדיסק 1, לדיסק 2 ולדיסק 4 במקביל", got


def test_the_survivors_after_a_failure_are_joined_the_same_way(driver, tmp_path):
    got = heading(driver, tmp_path, WRITING.format(dev="sda"), FAILED.format(dev="sdb"), WRITING.format(dev="sdd"))
    assert got == "ממשיך להעתיק לדיסק 1 ולדיסק 4", got


def test_one_target_is_unchanged(driver, tmp_path):
    """Guard, true before and after: a single target has nothing to join."""
    assert heading(driver, tmp_path, WRITING.format(dev="sda")) == "מעתיק לדיסק 1"
