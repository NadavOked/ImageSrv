"""#2849: the cloner's drawers on a small screen (a KVM switch passes no EDID).

The work's cloner (HP 8100, 11/11) sat behind a KVM switch: no EDID, a
fallback mode. At 800x600 the rows were squeezed to 56px and lcbay.c dropped
the "נכתב עד כה" line; at 640x480 the third drawer was drawn under the status
bar. Here the real ``screen_cloner`` is drawn (through ``app_draw``) at
640x480, 800x600 and 1024x768 and two things are measured, not assumed:

* every writing drawer's "בטל…" target -- one per row, centred on it -- lies
  inside the screen, above the status bar;
* the row says how much was written: two states that differ ONLY in the
  drawer's bytes (no total, so no percent or bar) must differ in the row's
  name column. A row that dropped the line draws the same pixels twice.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

STATUS_H = 36   # N_STATUS_H, theme.c

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static cairo_surface_t *draw(App *a, const char *path, int W, int H) {
    FILE *f = fopen(path, "r"); if (!f) { perror(path); exit(2); }
    memset(&a->st, 0, sizeof a->st); state_parse(&a->st, f); fclose(f);
    a->force_cloner = 1; app_route(a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, W, H); cairo_destroy(cr);
    cairo_surface_flush(s);
    return s;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    int W = atoi(argv[1]), H = atoi(argv[2]);
    cairo_surface_t *sb = draw(&a, argv[4], W, H);
    cairo_surface_t *sa = draw(&a, argv[3], W, H);   /* a's hits are the ones left */
    if (argc > 5) cairo_surface_write_to_png(sa, argv[5]);
    printf("@ screen %d\n", a.screen);
    unsigned char *pa = cairo_image_surface_get_data(sa), *pb = cairo_image_surface_get_data(sb);
    int stride = cairo_image_surface_get_stride(sa);
    for (int h = 0; h < a.nhits; h++) {
        int id = a.hits[h].id;
        if (id < HIT_BAY_CANCEL_BASE || id >= HIT_BAY_CANCEL_BASE + 16) continue;
        Rect r = a.hits[h].r; int cy = (int)(r.y + r.h / 2), diff = 0;
        for (int y = cy - 24; y <= cy + 24; y++) {
            if (y < 0 || y >= H) continue;
            for (int x = (int)(W * 0.60); x < (int)(W * 0.84); x++)
                if (memcmp(pa + y * stride + x * 4, pb + y * stride + x * 4, 3)) diff++;
        }
        printf("@ row %d %.0f %.0f %.0f %.0f %d\n", id - HIT_BAY_CANCEL_BASE, r.x, r.y, r.w, r.h, diff);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("smallscreen")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    assert "src/screens_cloner.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def state(written: int, drawers: int = 3) -> str:
    """The cloner mid-stream, as cloner_gui_state writes it: no total (no
    percent, no bar), so only "נכתב עד כה" carries the bytes."""
    rows = "".join(f"drawer={p}|sd{chr(96 + p)}|writing|{written}|0||-1|-1|{p}|SATA {p - 1}||\n"
                   for p in range(1, drawers + 1))
    return ("cloner=Win11-Lab\nmachine_name=1\nscreen=cloner\nrb_mode=full\n"
            f"drawer_count={drawers}\n{rows}updated=1794417933\n")


def rows(driver: Path, tmp_path: Path, w: int, h: int, drawers: int = 3) -> list[tuple[int, ...]]:
    a, b = tmp_path / f"a{w}.txt", tmp_path / f"b{w}.txt"
    a.write_text(state(40_000_000_000, drawers), encoding="utf-8", newline="\n")
    b.write_text(state(12_000_000_000, drawers), encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), str(w), str(h), posix(a), posix(b), posix(tmp_path / f"shot-{w}x{h}.png")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    out = [line.split() for line in run.stdout.splitlines() if line.startswith("@ row ")]
    return [tuple(int(float(v)) for v in f[2:]) for f in out]


@pytest.mark.parametrize("size", [(640, 480), (800, 600), (1024, 768), (1280, 800)])
def test_every_drawer_fits_and_says_what_was_written(driver, tmp_path, size):
    w, h = size
    found = rows(driver, tmp_path, w, h)
    assert sorted(r[0] for r in found) == [0, 1, 2], found       # three rows were drawn, each its own target
    for idx, x, y, rw, rh, diff in found:
        assert 0 <= x and x + rw <= w, (size, idx, x, rw)
        assert y >= 0 and y + rh <= h - STATUS_H, f"{size}: drawer {idx + 1} under the status bar ({y}+{rh})"
        assert diff > 0, f"{size}: drawer {idx + 1} does not say how much was written (נכתב עד כה)"


@pytest.mark.parametrize("size", [(640, 480), (800, 600)])
def test_more_than_three_drawers_keep_a_row_each_on_a_small_screen(driver, tmp_path, size):
    """More drawers than three on a small screen: at least the row (name, percent, bar) of each."""
    w, h = size
    found = rows(driver, tmp_path, w, h, drawers=5)
    assert sorted(r[0] for r in found) == [0, 1, 2, 3, 4], found
    for idx, x, y, rw, rh, _diff in found:
        assert y >= 0 and y + rh <= h - STATUS_H, f"{size}: drawer {idx + 1} under the status bar"
