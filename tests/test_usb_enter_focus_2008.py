"""#2008 -- the clone stick's flow, "Enter, Enter" (Nadav 22/10, the USB in the field).

On every screen of the stick's clone flow the focus starts on the main action below:
the choice opens on "המשך לאישור" as soon as the source and the targets can go (before:
on the drawers, and the keyboard had to walk down to it). The end screen was already on
its main action ("כבה את המחשב").

Nadav 22/10, later the same day, REVERSES #2008's safety exception on the stick: the
confirmation that erases the targets opens on the red "מחק והעתק", so Enter-Enter from the
first screen starts the copy (and Enter-Enter-Enter from an orange source: its notice has
"המשך" alone, test_lc_orange_source_2017.py). Esc is still "חזרה". The cloner keeps
"חזרה" on its confirmation.

The cloner's own local clone (no stick) keeps the focus on the drawers: the change is the
stick's only (a->st.lc.usb).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import test_native_local_clone as lc
from test_cloner_gui import BASH, GUI, posix
from test_native_local_clone import DISKS, FITS, drive, records
from test_usb_settings_1959_1960 import native_gui

#: the local clone's driver, and "הגדרות" (the stick's settings button) among its hits
DRIVER = lc.DRIVER.replace('{"AGAIN", HIT_LC_AGAIN} };', '{"AGAIN", HIT_LC_AGAIN}, {"USBSET", HIT_LC_USBSET} };')


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    assert DRIVER != lc.DRIVER, "the hit table moved: the USBSET hit was not added"
    d = tmp_path_factory.mktemp("lcdrv2008")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


USB = "lc_usb=1\n"
LCF_BAY, LCF_NEXT, LCF_GO, LCF_CANCEL = "focus=0", "focus=2", "focus=3", "focus=4"


def states(out: list[str]) -> list[str]:
    return [line for line in out if line.startswith("@ screen")]


@native_gui
def test_the_choice_opens_on_the_main_action_and_enter_enter_starts_the_copy(driver, tmp_path):
    """Nadav 22/10: on the stick the confirmation opens on "מחק והעתק" (it was "חזרה")."""
    out = drive(driver, tmp_path, DISKS + FITS + USB, "move:1,1", "key:ENTER", "key:ENTER")
    first, opened, _started = states(out)
    assert LCF_NEXT in first and "confirm=0" in first, out          # on "המשך לאישור"
    assert "confirm=1" in opened and LCF_GO in opened, out           # the confirmation: on "מחק והעתק"
    assert records(out)[-1] == "lc-start|sdc|sda|0", out


@native_gui
def test_esc_on_the_stick_confirmation_is_still_back(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS + USB, "key:ENTER", "key:ESC")
    assert "confirm=0" in states(out)[-1] and not any(r.startswith("lc-start") for r in records(out)), out


@native_gui
def test_the_cloner_confirmation_still_opens_on_back(driver, tmp_path):
    """The cloner (no stick): Enter, Enter reaches the confirmation on "חזרה" -- nothing starts."""
    out = drive(driver, tmp_path, DISKS + FITS, "key:ENTER", "key:ENTER")
    assert "confirm=1" in states(out)[-1] and LCF_CANCEL in states(out)[-1], out
    assert not any(r.startswith("lc-start") for r in records(out)), out


@native_gui
def test_the_focus_moves_to_the_main_action_when_the_verdicts_arrive_untouched_only(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + USB, "move:1,1", f"state={DISKS + FITS + USB}", "move:1,1")
    assert LCF_BAY in states(out)[0] and LCF_NEXT in states(out)[-1], out   # no verdicts yet: nothing to go to
    out = drive(driver, tmp_path, DISKS + USB, "key:DOWN", f"state={DISKS + FITS + USB}", "move:1,1")
    assert LCF_BAY in states(out)[-1], out                                # the person moved: theirs


@native_gui
def test_the_cloners_local_clone_keeps_the_focus_on_the_drawers(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS, "move:1,1")
    assert LCF_BAY in states(out)[0], out


@native_gui
def test_the_settings_button_is_not_a_touch_of_the_choice(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + USB, "hit:USBSET", f"state={DISKS + FITS + USB}", "move:1,1")
    assert "lc-usbset" in records(out) and LCF_NEXT in states(out)[-1], out
