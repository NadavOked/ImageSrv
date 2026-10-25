"""#2045 -- the local clone's result card of a pulled drawer says "pulled", not "broken".

Metal D2 (23/10): the card of the disk pulled mid-copy said "the disk will not
boot and is not usable. Do not put it in a classroom computer -- check or replace
it". Wrong for a pull: nothing says the disk is bad. Nadav: "the disk was pulled
mid-copy -- not written to the end. It can be cloned to again".

A driver loads the agent's state into the real parser (``lc_state.c``), draws the
real result screen, and prints every text the screen made (``text_make`` wrapped
at link time) -- so it builds on the code before the fix too (the negative
control). ``lc_tgt``'s 12th field (``lcgui.sh``) is the agent's "pulled".
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

PULLED = "הדיסק נשלף באמצע — לא נכתב עד הסוף. אפשר לשכפל אליו שוב."
BROKEN = "אל תכניסו אותו למחשב בכיתה"
PULL_WHY = "מחיצה 3: הכונן נעלם מהמערכת (נשלף או נותק)"

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    printf("view=%d\n", lc_view(&a));
    cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcpulled")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def texts(driver: Path, tmp_path: Path, tgts: str) -> list[str]:
    state = (DISKS.replace("lc_phase=idle", "lc_phase=done") + FITS +
             "lc_run=sdc|148600000000|148600000000|1065|0|usb|5|\n" + tgts)
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]


def test_the_pulled_disk_says_pulled_and_the_broken_one_keeps_its_warning(driver, tmp_path):
    """sdb was pulled (field 12 = 1); sda failed on a write with no pull (0)."""
    got = texts(driver, tmp_path,
                f"lc_tgt=sdb|failed|21200000000|3|2||{PULL_WHY}|148600000000|0|||1\n"
                "lc_tgt=sda|failed|30000000000|3|2||מחיצה 3: שגיאת כתיבה|148600000000|0|||0\n")
    assert PULLED in got, got
    assert sum(BROKEN in t for t in got) == 1, got   # sda's, and only sda's


def test_an_agent_with_eleven_fields_keeps_the_old_text(driver, tmp_path):
    """A kiosk newer than the agent: no 12th field -- not pulled, the stricter text."""
    got = texts(driver, tmp_path, f"lc_tgt=sdb|failed|21200000000|3|2||{PULL_WHY}|148600000000|0||\n")
    assert PULLED not in got and any(BROKEN in t for t in got), got
