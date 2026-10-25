"""#2009 -- the local clone's result screen: a done target with ``ntfs_note``
(``lc_tgt``'s 10th field, ``lcgui.sh``) is orange, never green.

A driver loads the agent's state file into the real parser (``lc_state.c``),
draws the real result screen and says what the status bar says, the colour of
its dot, and how many pixels of the theme's orange (``warn``) and green
(``led_ok``) the screen holds. The same state without the note is the green
one -- so a screen that is orange for every result fails too.
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

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

NOTE = ("Windows במקור לא כובה עד הסוף (Fast Startup) — המחיצה הועתקה בלי הגדלה. "
        "כדי להגדיל: לכבות את המקור עם Shift+כיבוי ולשכפל שוב")

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static int near(unsigned v, double c) { int d = (int)v - (int)(c * 255 + .5); return d > -10 && d < 10; }
static long count(cairo_surface_t *s, Rgb c) {
    unsigned char *p = cairo_image_surface_get_data(s); int st = cairo_image_surface_get_stride(s); long n = 0;
    for (int y = 0; y < 800; y++) for (int x = 0; x < 1280; x++) {
        unsigned v = *(unsigned *)(p + y * st + x * 4);
        if (near((v >> 16) & 255, c.r) && near((v >> 8) & 255, c.g) && near(v & 255, c.b)) n++; }
    return n;
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_flush(s);
    Rgb dot; const char *w = lc_status_word(&a, &dot);
    int warn = dot.r == a.theme->warn.r && dot.g == a.theme->warn.g && dot.b == a.theme->warn.b;
    int ok = dot.r == a.theme->led_ok.r && dot.g == a.theme->led_ok.g && dot.b == a.theme->led_ok.b;
    /* only what is drawn -- no struct field of the fix, so the driver builds on the base too (the negative control) */
    printf("view=%d\nstatus=%s\ndot=%s\nwarn_px=%ld\nok_px=%ld\n", lc_view(&a), w, warn ? "warn" : ok ? "ok" : "other",
           count(s, a.theme->warn), count(s, a.theme->led_ok));
    cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcnote")
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


def show(driver: Path, tmp_path: Path, note: str) -> dict:
    state = (DISKS.replace("lc_phase=idle", "lc_phase=done") + FITS +
             "lc_run=sdc|148600000000|148600000000|1065|0|usb|5|\n"
             f"lc_tgt=sda|done|148600000000|4|4|||148600000000|2|{note}\n")
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


def test_a_done_target_with_the_note_is_orange_and_says_why(driver, tmp_path):
    got = show(driver, tmp_path, NOTE)
    assert got["dot"] == "warn" and got["status"] == "הסתיים · עם הערה", got
    green = show(driver, tmp_path, "")
    # the card's accent, ring and check, the status dot: orange where the clean one is green
    assert int(got["warn_px"]) > int(green["warn_px"]) + 400, (got, green)
    assert int(got["ok_px"]) < int(green["ok_px"]) - 400, (got, green)


def test_without_the_note_the_done_target_stays_green(driver, tmp_path):
    got = show(driver, tmp_path, "")
    assert got["dot"] == "ok" and got["status"] == "הסתיים", got


def test_an_old_agent_that_sends_nine_fields_is_green(driver, tmp_path):
    """A kiosk newer than the agent: no 10th field -- no note, never garbage."""
    state = (DISKS.replace("lc_phase=idle", "lc_phase=done") + FITS +
             "lc_run=sdc|148600000000|148600000000|1065|0|usb|5|\n"
             "lc_tgt=sda|done|148600000000|4|4|||148600000000|2\n")
    p = tmp_path / "old.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "dot=ok" in r.stdout and "status=הסתיים\n" in r.stdout, r.stdout
