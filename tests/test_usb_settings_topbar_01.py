"""Mockup 01, section 2 (Nadav 24/10): on the USB stick "הגדרות" moves from the corner of the
content (#1914) to the top bar, next to the theme switch, with F4.

The fixed buttons sit left to right in the order of their keys: F2 (dark mode), F4 (settings).
"כלים" (F3, the toolbox #649) is not built now -- it will go in between. F2 stays dark mode.

Behavioural, on the real GUI (lab only): F4 on the stick's source screen asks for the settings
(the same `lc-usbset` the button sends), and the drawn frame has the button in the top bar --
the stick has no clock there, so before this the bar was empty right of the switch.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import test_usb_enter_focus_2008 as u2008
from test_cloner_gui import BASH, GUI, _build_gui, _png_rgb, posix
from test_native_local_clone import DISKS, FITS, drive, records
from test_usb_settings_1959_1960 import native_gui

#: the 2008 driver (the USBSET hit), with F4 among its keys
DRIVER = u2008.DRIVER.replace(': KEYSYM_ESC;', ': !strcmp(k, "F4") ? KEYSYM_F4 : KEYSYM_ESC;', 1)


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    assert DRIVER != u2008.DRIVER, "the driver's key table moved: F4 was not added"
    d = tmp_path_factory.mktemp("lcdrv01")
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


@native_gui
def test_f4_on_the_stick_source_screen_opens_the_settings(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS + u2008.USB, "key:F4")
    assert "lc-usbset" in records(out), out


@native_gui
def test_f4_is_nothing_on_the_cloners_local_clone(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS, "key:F4")
    assert "lc-usbset" not in records(out), out


@native_gui
def test_the_settings_button_is_still_a_hit_and_not_a_touch(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + u2008.USB, "hit:USBSET")
    assert "lc-usbset" in records(out), out


@native_gui
def test_the_settings_button_is_drawn_in_the_top_bar_next_to_the_theme_switch(tmp_path):
    binary = _build_gui(tmp_path)
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "c"), "--size", "1280x800",
                          "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
                         capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-2000:]
    w, h, ch, px = _png_rgb(tmp_path / "c-lcsource-usb-light.png")
    # the top bar (48px) right of the theme switch (16 + 56): white strokes = the outline, F4, the label
    white = sum(1 for y in range(8, 40) for x in range(16 + 56 + 4, 16 + 56 + 180)
                if all(px[(y * w + x) * ch + c] > 230 for c in range(3)))
    assert white > 50, f"no button in the top bar ({white} white pixels)"   # 101 drawn; 0 when the bar is empty
