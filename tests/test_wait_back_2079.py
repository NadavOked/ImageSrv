"""#2079 (mockup 02, Nadav 24/10): "חזרה למסך הבחירה" on the cloner's wait.

Whoever missed the boot choice was stuck on "מחובר. ממתין לסבב." -- the only way to a local
clone was a reboot (and F1 with a disk the BIOS stops on). Now Esc, or the button under the
drawers, goes back to "איך להפעיל את המחשב?" with the count stopped, without a reboot;
"מהשרת" there is the wait again. While a round is open for the machine the button is off and
says why.

The way it goes: the GUI says choice-back; the bridge (guibridge.sh) writes the boot choice
(lc/boot "0|2", lc/screen "boot"); the main loop's local_clone_offer takes it, with no count.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import test_native_local_clone as lc
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_cloner_gui import GUI
from test_native_local_clone import drive
from test_usb_settings_1959_1960 import native_gui

import pytest
import subprocess

pytestmark = requires_native(("bash", BASH))
LIB = AGENT / "lib"


def bridge(tmp_path: Path, env: str = "") -> tuple[str, Path]:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_SCREEN=cloner {env}; . {posix(LIB)}/guibridge.sh; '
             'token=choice-back; gui_dispatch; echo "rc=$?"')
    return out, run / "lc"


def test_back_writes_the_boot_choice_with_no_count(tmp_path):
    out, lcd = bridge(tmp_path)
    assert "rc=0" in out, out
    assert (lcd / "boot").read_text().strip() == "0|2" and (lcd / "screen").read_text().strip() == "boot"


@pytest.mark.parametrize("env", ["D_SESSION_STATE=open", "GUI_SCREEN=build"])
def test_no_way_back_while_a_round_is_open_or_off_a_cloner(tmp_path, env):
    out, lcd = bridge(tmp_path, env)
    assert "rc=0" not in out and not (lcd / "screen").exists(), out


def test_the_offer_takes_it_without_a_count(tmp_path):
    run = tmp_path / "run"; (run / "lc").mkdir(parents=True)
    (run / "lc" / "boot").write_text("0|2\n"); (run / "lc" / "screen").write_text("boot\n")
    out = sh(f'export RUN_DIR={posix(run)!r} LC={posix(run / "lc")!r}; . {posix(LIB)}/localclone.sh; '
             'log() { :; }; lc_gui_alive() { return 0; }; attended_secs() { echo 600; }; attended_stop() { :; }; '
             'lc_boot_choice() { echo "PAUSED=$LC_PAUSED"; LC_CHOICE=server; }; local_clone_offer')
    assert "PAUSED=2" in out, out


#: the local clone's driver, with the wait's own handler first and its button among the hits
DRIVER = (lc.DRIVER.replace('{"AGAIN", HIT_LC_AGAIN} };', '{"AGAIN", HIT_LC_AGAIN}, {"WAIT_BACK", HIT_WAIT_BACK} };')
          .replace("app_route(&a); lc_handle(&a, &e);", "app_route(&a); if (!standby_handle(&a, &e)) lc_handle(&a, &e);"))


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    assert DRIVER.count("standby_handle") == 1 and "WAIT_BACK" in DRIVER, "the driver moved"
    d = tmp_path_factory.mktemp("lcdrv2079")
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


WAIT = "cdisk=0|sda|WDC WD5000AAKX|500107862016|passed||3.0|3.0|0|1|SATA 0||\nhello_age=4\nhello_rc=200\n"


@native_gui
def test_esc_on_the_wait_goes_back(driver, tmp_path):
    out = drive(driver, tmp_path, WAIT, "key:ESC")
    assert "choice-back" in out, out


@native_gui
def test_the_button_on_the_wait_goes_back(driver, tmp_path):
    out = drive(driver, tmp_path, WAIT, "hit:WAIT_BACK")
    assert "choice-back" in out, out


@native_gui
def test_no_way_back_while_a_round_is_open(driver, tmp_path):
    out = drive(driver, tmp_path, WAIT + "session=office365 v4|x|g|open" + chr(10), "key:ESC", "hit:WAIT_BACK")
    assert "choice-back" not in out, out
