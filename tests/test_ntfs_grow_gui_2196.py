"""#2196 -- the local clone's result card when the table grew and the
filesystem did not (``lc_tgt``'s 13th field, ``lcgui.sh``; the agent side is
``test_ntfs_grow_2196.py``).

Field, 25/10: the card said "partition 1 grew from 225 to 469 GB" and, below
it, "the grow was deferred". The real parser (``lc_state.c``) and the real
result screen are built into a driver; IMAGECTL_GUI_TEXTLOG records every
string the screen lays out. With the mark: the one line the agent wrote, and
no "הוגדלה" over it. Without the mark (or from an older agent with 12 fields):
the partition grew, said as before -- so a screen that never says it fails too.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix
from test_native_local_clone import DISKS, FITS
from test_ntfs_grow_2196 import SAID

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

# --- the result card: one line, not two that contradict each other ----------------------

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
    return 0;
}
'''

ERR = "הושלם, אך ההרחבה נדחתה: מחיצה 3 (ntfs); טבלת המחיצות הוגדלה, מערכת הקבצים לא: " + SAID


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("grow2196")
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


def card_words(driver: Path, tmp_path: Path, tail: str) -> list[str]:
    state = (DISKS.replace("lc_phase=idle", "lc_phase=done") + FITS +
             "lc_run=sdc|148600000000|148600000000|1065|0|usb|5|\n"
             f"lc_tgt=sda|done|148600000000|4|4|3,468952547328,224897344000,0|{ERR}|148600000000|2||0,-1|0{tail}\n")
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGECTL_GUI_TEXTLOG=posix(log))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return log.read_text(encoding="utf-8").splitlines()


def test_a_deferred_grow_is_one_line_on_the_card(driver, tmp_path):
    words = card_words(driver, tmp_path, "|1")
    assert ERR in words, words                                                    # the one line, as the agent wrote it
    assert not any("הוגדלה מ-" in w or "המחיצה האחרונה הוגדלה ל-" in w for w in words), words


@pytest.mark.parametrize("tail", ["|0", ""])   # grown -- or an older agent with 12 fields: as before
def test_without_the_mark_the_card_says_the_partition_grew_as_before(driver, tmp_path, tail):
    words = card_words(driver, tmp_path, tail)
    assert any("הוגדלה מ-" in w for w in words) and any("המחיצה האחרונה הוגדלה ל-" in w for w in words), words
