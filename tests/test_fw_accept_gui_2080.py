"""#2080 (mockup 06 section 2, Nadav 25/10): the cloner's wait screen says the SATA connections moved, and
whoever stands at the machine can press "זה מכוון".

The GUI draws what the state file says (`fw_warn=<count>|<text>`, clonergui.sh) and answers a click on the
button with the `fw-accept` record (guibridge.sh posts it). No warning in the state -- or one that cannot be
read -- is no banner and no button: nothing to press, and the screen is the one it was.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import test_wait_back_2079 as wb
from native import requires_native
from test_agent import BASH, posix
from test_native_local_clone import GUI, drive
from test_usb_settings_1959_1960 import native_gui

pytestmark = requires_native(("bash", BASH))

DRIVER = wb.DRIVER.replace('{"WAIT_BACK", HIT_WAIT_BACK} };', '{"WAIT_BACK", HIT_WAIT_BACK}, {"FW_ACCEPT", HIT_FW_ACCEPT} };')


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    assert "FW_ACCEPT" in DRIVER, "the driver moved"
    d = tmp_path_factory.mktemp("fwdrv2080")
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


WARN = "fw_warn=1|דיסק T עבר מ-SATA 1 ל-SATA 2\n"


@native_gui
def test_the_button_on_the_warning_says_fw_accept(driver, tmp_path):
    out = drive(driver, tmp_path, wb.WAIT + WARN, "hit:FW_ACCEPT")
    assert "fw-accept" in out, out


@native_gui
def test_no_warning_no_button(driver, tmp_path):
    out = drive(driver, tmp_path, wb.WAIT, "hit:FW_ACCEPT")
    assert "@ nohit FW_ACCEPT" in out and "fw-accept" not in out, out


@native_gui
@pytest.mark.parametrize("line", ["fw_warn=0|x\n", "fw_warn=|x\n", "fw_warn=banana|x\n", "fw_warn=2|\n", "fw_warn=2\n"])
def test_a_warning_that_cannot_be_read_is_none(driver, tmp_path, line):
    out = drive(driver, tmp_path, wb.WAIT + line, "hit:FW_ACCEPT")
    assert "@ nohit FW_ACCEPT" in out, (line, out)


@native_gui
@pytest.mark.parametrize("ev", ["key:ESC", "hit:WAIT_BACK"])
def test_the_way_back_is_still_there_under_the_warning(driver, tmp_path, ev):
    assert "choice-back" in drive(driver, tmp_path, wb.WAIT + WARN, ev)


@native_gui
def test_the_warning_goes_with_the_next_state(driver, tmp_path):
    out = drive(driver, tmp_path, wb.WAIT + WARN, "state=" + wb.WAIT, "hit:FW_ACCEPT")
    assert "@ nohit FW_ACCEPT" in out and "fw-accept" not in out, out
