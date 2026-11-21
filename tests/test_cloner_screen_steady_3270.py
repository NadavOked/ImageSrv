"""#3270 (the screen's part) and #3274 -- the cloner's "שיכפול במקביל" on metal, 14/11 (cloner 2).

Nadav saw, in a round whose stream stopped and started every few seconds:

1. the "נכשל" tag of a drawer bigger than the wait screen's "SMART תקין". The rule
   (Nadav): every disk-state tag is that size -- 24px, 12/600, ``sb_bay_pill(..., 0)``,
   which is what the wait screen draws "SMART תקין" with. The row keeps its colours;
2. the whole screen jumping when the writing drawer's line went "כותב · 15 MB/s" <->
   "ממתין לזרם מהשרת · 0 MB/s": the hero's end side was three lines with an end and
   one line without, so the hero's height, and every row under it, moved;
3. no end while the stream stood ("ממתין לזרם מהשרת" replaced it). The end stays now --
   the agent's average of the wave so far -- with "(הזרם ממתין)" beside it;
4. (#3274) "0 MB/s" in the waiting line read "MB/s 0": no bidi isolate.

Everything here is measured on the real sources: the real parser, ``screen_cloner``
through ``app_draw``, ``lc_bay_draw`` and ``sb_bay_pill``; pixels, hit rectangles and
the texts the screen made.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, _png_rgb, posix

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

G = 1_000_000_000

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "screens_rounds.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    if (argc == 3 && !strcmp(argv[1], "pill")) {
        /* a failed row as the cloner draws it, and the wait screen's "SMART תקין" size beside it */
        const Theme *t = a.theme;
        cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 400);
        cairo_t *cr = cairo_create(s);
        draw_set(cr, t->porcelain); cairo_paint(cr);
        LcBay g = {0}; g.kind = BAY_FAILED; g.drawer = 1; g.share = -1; g.pill = "נכשל";
        snprintf(g.name, sizeof g.name, "SATA 1"); snprintf(g.pct, sizeof g.pct, "—");
        snprintf(g.line, sizeof g.line, "נכשל: הדיסק נעלם באמצע הכתיבה");
        lc_bay_draw(&a, cr, (Rect){ 20, 20, 940, 104 }, &g);
        sb_bay_pill(cr, 700, 250, "נכשל", t->on_ink, t->danger, 0);
        draw_set(cr, t->danger); cairo_rectangle(cr, 0, 390, 10, 10); cairo_fill(cr);   /* the colour, as cairo writes it */
        cairo_surface_flush(s);
        int rc = cairo_surface_write_to_png(s, argv[2]) == CAIRO_STATUS_SUCCESS ? 0 : 4;
        cairo_destroy(cr); cairo_surface_destroy(s);
        return rc;
    }
    if (argc < 2) return 2;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    a.force_cloner = 1; app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_flush(s);
    if (argc > 2) cairo_surface_write_to_png(s, argv[2]);
    printf("@ screen %d\n", a.screen);
    for (int h = 0; h < a.nhits; h++) {
        int id = a.hits[h].id;
        if (id < HIT_BAY_CANCEL_BASE || id >= HIT_BAY_CANCEL_BASE + 16) continue;
        Rect r = a.hits[h].r;
        printf("@ row %d %.1f %.1f %.1f %.1f\n", id - HIT_BAY_CANCEL_BASE, r.x, r.y, r.w, r.h);
    }
    cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("steady3270")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    assert "src/screens_cloner.c" in srcs and "src/lcbay.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _run(driver: Path, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run([str(driver), *args], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return r


# --- 1. the tag's size ------------------------------------------------------------------

def _bbox(image, colour, x0, x1, y0, y1):
    width, _h, ch, px = image
    xs, ys = [], []
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = (y * width + x) * ch
            if tuple(px[p:p + 3]) == colour:
                xs.append(x); ys.append(y)
    assert xs, (colour, x0, x1, y0, y1)
    return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def test_a_failed_drawer_tag_is_the_smart_ok_size(driver, tmp_path):
    png = tmp_path / "pill.png"
    _run(driver, "pill", posix(png))
    image = _png_rgb(png)
    width, _h, ch, px = image
    p = (395 * width + 5) * ch
    danger = tuple(px[p:p + 3])
    # the row (20,20,940,104): its name column ends 96+1+22 px inside the right edge, 220 wide; inside the 3px frame
    fr = 20 + 940 - 96 - 1 - 22
    row_w, row_h = _bbox(image, danger, fr - 220, fr, 24, 120)
    ref_w, ref_h = _bbox(image, danger, 500, 720, 220, 280)   # sb_bay_pill(..., 0): "SMART תקין"'s size
    assert abs(row_h - ref_h) <= 1, f"the 'נכשל' tag is {row_h}px tall, the SMART tag {ref_h}px"
    assert abs(row_w - ref_w) <= 1, f"the 'נכשל' tag is {row_w}px wide, the same words at the SMART size {ref_w}px"


# --- 2 + 3. the hero keeps its place, and its end ---------------------------------------

def _state(rate: int, eta: int, clock: int, stalled: bool) -> str:
    now = int(time.time())
    moved = now - 60 if stalled else now
    return ("cloner=Office 2024\nmachine_name=2\nscreen=cloner\n"
            f"drawer=2|sdb|writing|{int(0.44 * 19.5 * G)}|{int(19.5 * G)}||{rate}|{eta}|2|SATA 1\n"
            f"drawer_moved=sdb|{moved}\n"
            f"forecast={clock}||\n"
            f"updated={now}\n")


def _rows(driver: Path, tmp_path: Path, name: str, state: str) -> tuple[list[tuple[float, ...]], list[str]]:
    src = tmp_path / f"{name}.txt"
    src.write_text(state, encoding="utf-8", newline="\n")
    r = _run(driver, posix(src), posix(tmp_path / f"{name}.png"))
    rows = [tuple(float(v) for v in l.split()[2:]) for l in r.stdout.splitlines() if l.startswith("@ row ")]
    texts = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    assert rows, r.stdout                      # the writing drawer was drawn, with its "בטל…"
    assert any("מעתיק" in t for t in texts), texts
    return rows, texts


@pytest.mark.parametrize("clock", [1, 0])
def test_the_stream_stopping_and_starting_moves_nothing(driver, tmp_path, clock):
    """Writing at 15 MB/s, standing with the end the agent still has, standing without one, and
    not measured yet: the drawer's row (its "בטל…" target) is in the same place in all four."""
    seen = {
        "moving": _rows(driver, tmp_path, f"moving{clock}", _state(15_000_000, 840, clock, False))[0],
        "stalled": _rows(driver, tmp_path, f"stalled{clock}", _state(0, 840, clock, True))[0],
        "stalled-no-end": _rows(driver, tmp_path, f"stallednoend{clock}", _state(0, -1, clock, True))[0],
        "unmeasured": _rows(driver, tmp_path, f"unmeasured{clock}", _state(-1, -1, clock, False))[0],
    }
    assert len({tuple(v) for v in seen.values()}) == 1, seen


def test_a_standing_stream_keeps_its_end_and_says_it_waits(driver, tmp_path):
    _, texts = _rows(driver, tmp_path, "stallclock", _state(0, 840, 1, True))
    assert "לפי הקצב הממוצע (הזרם ממתין)" in texts, texts     # beside the hour, not instead of it
    assert "כ-14 דק' נותרו" in texts, texts
    assert texts.count("ממתין לזרם מהשרת") == 1, texts        # the speed card's caption only -- the hero's end is not replaced
    _, texts = _rows(driver, tmp_path, "stallnoclock", _state(0, 840, 0, True))
    assert "כ-14 דק' נותרו (הזרם ממתין)" in texts, texts      # no proven clock: no hour, the minutes stay


def test_a_moving_stream_reads_as_before(driver, tmp_path):
    """The guard: nothing standing -- "לפי הקצב בפועל", no "(הזרם ממתין)"."""
    _, texts = _rows(driver, tmp_path, "moveclock", _state(15_000_000, 840, 1, False))
    assert "לפי הקצב בפועל" in texts and "כ-14 דק' נותרו" in texts, texts
    assert not [t for t in texts if "הזרם ממתין" in t], texts


def test_a_standing_stream_without_an_end_still_says_it_waits(driver, tmp_path):
    """The agent has no anchor yet (eta -1): "ממתין לזרם מהשרת", never an invented end."""
    _, texts = _rows(driver, tmp_path, "noend", _state(0, -1, 1, True))
    assert texts.count("ממתין לזרם מהשרת") == 2, texts        # the hero's end and the speed card's caption
    assert "לפי הקצב הממוצע (הזרם ממתין)" not in texts, texts


# --- 4. #3274: "0 MB/s" in the waiting line --------------------------------------------

def test_the_waiting_line_isolates_zero_mb_s(driver, tmp_path):
    _, texts = _rows(driver, tmp_path, "bidi", _state(0, -1, 0, True))
    assert "ממתין לזרם מהשרת · \u20680 MB/s\u2069" in texts, [t for t in texts if "MB/s" in t]
