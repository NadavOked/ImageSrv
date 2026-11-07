"""#2067 -- the red and green bars of mockup 08: the stream's outage, in words.

Nadav 24/10 (mockup 08, approved): when the stream stands the cloner's screen shows a red bar
"הרשת נותקה לפני X שניות", and when it moves again a green one "הרשת חזרה אחרי X שניות" that
stays **five seconds and goes** ("החלון הירוק צריך להופיע חמש שניות לעולם"). Until now only the
tune existed (roundbeep.sh): the 06/11 triage found neither string anywhere in agent/ or native-gui/.

Three layers, each tested where it lives:

* the agent (busybox ash): ``stream_beep_tick`` keeps the two moments the bar needs -- the last
  byte before the outage, and how long it lasted -- and ``stream_net_record`` turns them into
  one state record, ``net=lost|<since>|<ceiling>`` / ``net=back|<seconds>|<epoch>``;
* the wiring: ``cloner_gui_state`` puts that record in the state file the screen reads;
* the screen: the real parser and the real cloner screen, every text it made.

A moment the agent could not save is absent from the record and from the sentence -- never a
guess (principle 5): "לא ידוע כמה זמן" is not "0 שניות".
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_beeps_2067 import BUSYBOX, prelude, run_ash
from test_cloner_gui import BASH, GUI, PORTS, PRELUDE, env, posix, sh, target
from test_cloner_stall_2055 import _GUI, driver  # noqa: F401  (the fixture builds the real screen once)

G = 1_000_000_000
_ASH = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")


def _ticks(tmp_path: Path, body: str) -> list[str]:
    """Run <body> under ash with `date` frozen at $t; every `rec` prints the record (or '-')."""
    out = run_ash(prelude(tmp_path) + """
        t=1000; date() { echo "$t"; }
        rec() { r=$(stream_net_record); echo "${r:--}"; }
    """ + body)
    return out.splitlines()


# --- the agent --------------------------------------------------------------------------


@_ASH
def test_an_outage_is_a_red_record_with_the_moment_of_the_last_byte(tmp_path):
    got = _ticks(tmp_path, """
        t=2000; stream_beep_tick 29; wait; rec     # not yet 30 s: no outage
        stream_beep_tick 45; wait; rec             # 45 s since the last byte: it came at 1955
        t=2010; stream_beep_tick 55; wait; rec     # still the same outage, same moment
    """)
    assert got == ["-", "net=lost|1955|300", "net=lost|1955|300"], got


@_ASH
def test_the_ceiling_is_the_receivers_own(tmp_path):
    """The bar says when the round fails: udp-receiver's --receive-timeout, not a constant of the bar."""
    got = _ticks(tmp_path, "UDPCAST_STALL_TIMEOUT=120; t=2000; stream_beep_tick 45; wait; rec")
    assert got == ["net=lost|1955|120"], got


@_ASH
def test_the_return_is_a_green_record_that_says_how_long_and_goes_after_five_seconds(tmp_path):
    got = _ticks(tmp_path, """
        t=2000; stream_beep_tick 45; wait                # the last byte came at 1955
        t=2052; stream_beep_tick 0; wait; rec            # the first byte after it, now: 97 s away
        t=2056; rec                                      # 4 s later: still there
        t=2057; rec                                      # 5 s: gone
        rec
    """)
    assert got[:2] == ["net=back|97|2052", "net=back|97|2052"], got
    assert got[2:] == ["-", "-"], got
    assert not (tmp_path / "beep.netend").exists()   # not kept for the next outage to find


@_ASH
def test_the_bar_does_not_wait_for_the_speaker(tmp_path):
    """The console's switch and a machine without pcspkr silence the tune, not the screen."""
    out = run_ash(prelude(tmp_path, speaker=False, enabled=False) + """
        t=1000; date() { echo "$t"; }
        t=2000; stream_beep_tick 45; wait; stream_net_record
    """)
    assert out.splitlines()[-1] == "net=lost|1955|300", out


@_ASH
def test_the_end_of_the_stream_phase_ends_the_red_bar(tmp_path):
    """No stream to judge (the round moved on, a failure) is not an outage and not a return."""
    got = _ticks(tmp_path, """
        t=2000; stream_beep_tick 45; wait; rec
        stream_beep_tick ''; wait; rec
        t=2500; stream_beep_tick 45; wait; rec           # a new outage starts from its own moment
    """)
    assert got == ["net=lost|1955|300", "-", "net=lost|2455|300"], got


@_ASH
def test_a_moment_that_was_not_saved_is_absent_not_guessed(tmp_path):
    got = _ticks(tmp_path, """
        echo lost > "$RUN_DIR/beep.net"; rec             # lost, and the start was never saved
        t=2052; stream_beep_tick 0; wait; rec            # back; there is no start to measure from
    """)
    assert got == ["net=lost||300", "net=back||2052"], got


@_ASH
def test_the_return_comes_with_its_tune_exactly_once(tmp_path):
    """The bar rides on the tune's own transitions: lost once per outage, back for one tick (a minute apart here: past its 5 s)."""
    run_ash(prelude(tmp_path) + """
        t=1000; date() { echo "$t"; }
        for age in 29 30 45 0 0 31; do
            t=$((t + 60)); stream_beep_tick "$age"; wait
            r=$(stream_net_record); echo "${r:--}" >> "$RUN_DIR/recs"
        done
    """)
    recs = (tmp_path / "recs").read_text().split()
    assert [r.split("|")[0] for r in recs] == ["-", "net=lost", "net=lost", "net=back", "-", "net=lost"], recs


# --- the wiring: the state file the screen reads ------------------------------------------


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_cloner_gui_state_carries_the_record(tmp_path):
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "writing", 20 * G, 0, "1000")
    (run / "beep.net").write_text("lost\n", newline="\n")
    (run / "beep.netsince").write_text("1955\n", newline="\n")
    script = (env(run, gui) + PRELUDE + f'. {posix(GUI.parent / "agent")}/lib/core/roundbeep.sh; '
              'stream_beep_tick() { :; }; ' + PORTS + "cloner_gui_state")   # the tick has its own tests above
    out = sh(script)
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    assert "net=lost|1955|300" in lines, lines


# --- the screen ---------------------------------------------------------------------------


def _texts(driver: Path, tmp_path: Path, net: str | None) -> list[str]:
    """The screen reads the wall clock in whole seconds (`time(NULL)`), and `NOW-n` is n seconds before
    the second this file was written. A run that crosses into the next second sees n+1 -- for n=4 that
    is the other side of the five-second rule (full run of round/1107b, 07/11: `[4-True]` failed). So
    each try starts just after a second turns, and counts only if the second did not turn again before
    the screen answered (positive evidence that the screen saw `now`); otherwise it is repeated."""
    for _ in range(5):
        time.sleep(1.02 - time.time() % 1)
        now = int(time.time())
        state = ("cloner=Office 2024\n"
                 f"drawer=2|sdb|writing|{int(0.86 * 19.5 * G)}|{int(19.5 * G)}||0|-1|2|SATA 1\n"
                 f"drawer_moved=sdb|{now - 60}\n"
                 + (re.sub(r"NOW-(\d+)", lambda m: str(now - int(m.group(1))), net) + "\n" if net else "")
                 + f"updated={now}\n")
        p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
        r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                           env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stderr
        got = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
        assert any("מעתיק" in t for t in got), got   # the cloner's round screen, with or without a bar
        if int(time.time()) == now:
            return got
    pytest.fail("the screen never answered within the second it was given (5 tries): not measured, not passed")


def _net(got: list[str]) -> list[str]:
    return [t for t in got if t.startswith(("הרשת ", "הזרם מהשרת עומד", "הזרם ממשיך"))]


@_GUI
def test_the_red_bar_says_how_long_what_waits_and_when_the_round_fails(driver, tmp_path):
    got = _texts(driver, tmp_path, "net=lost|NOW-40|300")
    title = [t for t in got if t.startswith("הרשת נותקה")]
    assert len(title) == 1 and re.fullmatch(r"הרשת נותקה לפני (4[0-9]) שניות", title[0]), title
    assert ("הזרם מהשרת עומד. הדיסקים מחכים ולא נמחקים. "
            "אם הקשר לא חוזר תוך 5 דקות, הסבב ייכשל.") in got, got
    assert not [t for t in got if t.startswith("הרשת חזרה")], got


@_GUI
def test_the_red_bar_without_a_known_start_or_ceiling_says_only_what_is_known(driver, tmp_path):
    got = _texts(driver, tmp_path, "net=lost||")
    assert "הרשת נותקה" in got, got
    assert not [t for t in got if "לפני" in t and t.startswith("הרשת")], got
    assert "הזרם מהשרת עומד. הדיסקים מחכים ולא נמחקים." in got, got
    assert not [t for t in got if "אם הקשר לא חוזר" in t], got


@_GUI
def test_the_green_bar_says_how_long_it_was_away(driver, tmp_path):
    got = _texts(driver, tmp_path, "net=back|52|NOW-1")
    assert "הרשת חזרה אחרי 52 שניות" in got, got
    assert "הזרם ממשיך מאיפה שעצר." in got, got
    assert not [t for t in got if t.startswith("הרשת נותקה")], got


@_GUI
@pytest.mark.parametrize("ago, shown", [(0, True), (4, True), (5, False), (30, False)])
def test_the_green_bar_is_five_seconds_and_gone(driver, tmp_path, ago, shown):
    """The screen holds the rule too: a state file the agent stopped rewriting does not keep it up."""
    got = _texts(driver, tmp_path, f"net=back|52|NOW-{ago}")
    assert ("הרשת חזרה אחרי 52 שניות" in got) is shown, got


@_GUI
def test_no_record_no_bar(driver, tmp_path):
    """The guard: a round without an outage looks as before -- no red, no green."""
    assert _net(_texts(driver, tmp_path, None)) == []


@_GUI
@pytest.mark.parametrize("record", ["net=sideways|1|2", "net=lost|1", "net=back"])
def test_a_record_the_parser_cannot_read_is_no_bar(driver, tmp_path, record):
    assert _net(_texts(driver, tmp_path, record)) == []
