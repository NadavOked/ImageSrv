"""#2613 -- the cloner's screen says "נבדק" only for a drawer the agent says was read back.

A drawer that finished (``done``) with ``readback = unverified`` (the image has no check list
yet, ``RB_NONE_KIND`` in readback.sh) used to read "הושלם · נבדק" -- "we could not check" shown
as "we checked" (principle 5). The screen has the agent's verdict since #2046 (``drawer_rbp=``,
field 4); this file draws the REAL screen with a driver and judges the words it laid out and the
orange of the row:

* ``unverified``  -> "הושלם · לא אומת", orange
* ``sampled``     -> "הושלם · נבדק במדגם" (never "אומת", never orange)
* ``verified``    -> "אומת — זהה לאימג׳" (#2046, unchanged)
* ``list_not_fetched`` (#1907) keeps its own words
* no verdict at all -> "הושלם" and nothing about a check
* the hero's closing sentences ("כל היעדים נכתבו ונבדקו", "N דיסקים נכתבו ונבדקו") do not claim
  a check that a row says was not made.

The screens are C and run on the lab (``requires_native``); the one wire test needs bash and jq.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import GUI, PORTS, PRELUDE, env, sh
import test_readback_progress_2046 as T
from test_readback_progress_2046 import cloner, driver, pytestmark_gui  # noqa: F401  (driver is a fixture)

UNVERIFIED = "||unverified|-1"      # what clonergui.sh sends for a disk that has a verdict and no bar
SAMPLED = "||sampled|-1"
VERIFIED = "||verified|-1"
NOT_CHECKED = "הושלם · נבדק"
ORANGE_DRIVER = T.DRIVER.replace(
    '"blue=%ld\\ngreen=%ld\\nred=%ld\\n"', '"blue=%ld\\ngreen=%ld\\nred=%ld\\nwarn=%ld\\n"').replace(
    "count(s, a.theme->danger));", "count(s, a.theme->danger), count(s, a.theme->warn));").replace(
    "return d > -6 && d < 6;", "return d > -40 && d < 40;")


def said(driver: Path, tmp_path: Path, rows, name: str, extra: str = "") -> list[str]:
    return T.show(driver, tmp_path, cloner(rows, extra), name)[1]


@pytestmark_gui
def test_an_unverified_drawer_says_so_and_not_that_it_was_checked(driver, tmp_path):
    lines = said(driver, tmp_path, [("done", UNVERIFIED, "")], "unv")
    assert "הושלם · לא אומת" in lines, lines
    assert not [s for s in lines if s == NOT_CHECKED or s.startswith(NOT_CHECKED)], lines
    assert not any("אומת — זהה" in s for s in lines), lines          # and not the green "verified" row either


@pytestmark_gui
def test_a_sampled_drawer_says_a_sample_and_is_never_verified(driver, tmp_path):
    lines = said(driver, tmp_path, [("done", SAMPLED, "")], "smp")
    assert "הושלם · נבדק במדגם" in lines, lines
    assert not any("לא אומת" in s or "אומת — זהה" in s for s in lines), lines


@pytestmark_gui
def test_a_verified_drawer_is_still_verified(driver, tmp_path):
    """Guard (passes before and after the fix): the full read keeps the words of #2046."""
    lines = said(driver, tmp_path, [("done", VERIFIED, "")], "ver", "rb_mode=full\n")
    assert "אומת — זהה לאימג׳" in lines, lines
    assert not any("לא אומת" in s for s in lines), lines


@pytestmark_gui
def test_the_list_that_did_not_come_down_keeps_its_own_words(driver, tmp_path):
    """Guard (#1907): the more specific reason is not swallowed by the general "לא אומת"."""
    rows = [("done", UNVERIFIED, "")]
    lines = T.show(driver, tmp_path, cloner(rows) + "drawer_rb=sdb|list_not_fetched\n", "nolist")[1]
    assert "הושלם · לא אומת — הרשימה לא הורדה" in lines, lines
    assert "הושלם · לא אומת" not in lines, lines


@pytestmark_gui
def test_a_drawer_with_no_verdict_makes_no_claim_about_a_check(driver, tmp_path):
    """No record from the agent is not "checked": the row says what it knows, that it finished."""
    lines = said(driver, tmp_path, [("done", "", "")], "none")
    assert "הושלם" in lines, lines
    assert not any("נבדק" in s or "אומת" in s for s in lines), lines


@pytestmark_gui
def test_an_unverified_drawer_is_orange_and_a_sampled_one_is_not(driver, tmp_path):
    """The words are not enough: the row is the orange of #943's warning line. Same screen, one verdict apart."""
    drv = tmp_path / "orange-driver"
    (tmp_path / "orange.c").write_text(ORANGE_DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([T.BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", T.posix(GUI), T.posix(drv), T.posix(tmp_path / "orange.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    got = {}
    for name, rb in (("unv", UNVERIFIED), ("smp", SAMPLED)):
        p = tmp_path / f"{name}.txt"
        p.write_text(cloner([("done", rb, "")]), encoding="utf-8", newline="\n")
        r = subprocess.run([str(drv), T.posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                           env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        got[name] = {k: int(v) for k, v in (l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)}
    assert got["unv"]["warn"] > got["smp"]["warn"] + 100, got


def hero(rows) -> list[str]:
    return rows


@pytestmark_gui
def test_the_end_of_the_round_does_not_say_all_were_checked_over_an_unverified_drawer(driver, tmp_path):
    lines = said(driver, tmp_path, [("done", VERIFIED, ""), ("done", UNVERIFIED, "")], "end-unv", "rb_mode=full\n")
    assert "כל היעדים נכתבו — אבל לא כולם נבדקו מול האימג׳." in lines, lines
    assert not any("ונבדקו" in s or "ונבדקה" in s for s in lines), lines
    assert "הכתיבה הסתיימה — ראו את ההערות במגירות" in lines, lines


@pytestmark_gui
def test_the_end_of_the_round_says_checked_only_when_every_drawer_was(driver, tmp_path):
    """Guard (passes before and after): verified and sampled drawers keep the closing sentence."""
    lines = said(driver, tmp_path, [("done", VERIFIED, ""), ("done", SAMPLED, "")], "end-ok", "rb_mode=full\n")
    assert "כל היעדים נכתבו ונבדקו." in lines, lines
    assert "2 דיסקים נכתבו ונבדקו" in lines, lines


@pytestmark_gui
def test_the_end_of_the_round_with_no_verdicts_claims_no_check(driver, tmp_path):
    lines = said(driver, tmp_path, [("done", "", ""), ("done", "", "")], "end-none")
    assert "כל היעדים נכתבו." in lines, lines
    assert not any("ונבדקו" in s or "ונבדקה" in s for s in lines), lines


# --- the wire: the verdict reaches the screen for a disk with no bar -----------------------


@requires_native("jq", why="json_get reads the hello for the mode")
@pytest.mark.parametrize("verdict", ["verified", "sampled", "unverified"])
def test_a_finished_disk_with_no_bar_still_carries_its_verdict(tmp_path, verdict):
    """A disk with no check list has no rb_prog (no invented progress) -- but its verdict (unverified!) must
    reach the screen: this is the record #2613's row reads."""
    lines, _ = T._kiosk(tmp_path, "done", None, verdict)
    assert f"drawer_rbp=sdb|||{verdict}|-1" in lines, lines
