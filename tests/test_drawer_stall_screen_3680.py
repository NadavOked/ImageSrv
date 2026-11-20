"""#3680 (metal 18/11, cloner 2): the cloner's own screen did not know `drawer_stall=`.

The agent writes `drawer_stall=<dev>|<waiting_disk|waiting_net>|<seconds>|<limit>` beside a drawer that stands (#2988,
Nadav 12/11: "30 seconds and no sign" -- the drawer must say at once that it waits, and for what). The native GUI had no
branch for the key: `state.c` printed `unknown key 'drawer_stall' ignored` on every state write (497 lines on 18/11, 7,611
on 17/11) and the drawer kept saying "כותב" with its last speed. The console did show it (`stallText`).

The words and the colours are the approved mockup 06-bay-states (the "stall" and "net" frames, 12/11): an orange row, the
tag "ממתין" (or "ממתין לרשת"), the sentence, and the speed as "—". What is measured here is the real `state_parse`,
`screen_cloner` through `app_draw`, `lc_bay_draw` and `sb_bay_pill`: the texts the screen made, and its pixels.

The contract (interfaces.md, section 4): no record = the drawer moves, and nothing here is drawn; a word the agent does not
say is no record; the limit is on the wire and is never shown (Nadav 12/11): only the time that has passed.
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
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    a.force_cloner = 1; app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800);
    draw_set(cr, a.theme->warning_soft); cairo_rectangle(cr, 0, 790, 10, 10); cairo_fill(cr);   /* the colour, as cairo writes it */
    cairo_destroy(cr);
    cairo_surface_flush(s);
    if (cairo_surface_write_to_png(s, argv[2]) != CAIRO_STATUS_SUCCESS) return 4;
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
    d = tmp_path_factory.mktemp("stall3680")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    assert "src/screens_cloner.c" in srcs and "src/state.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _state(*records: str, rate: int = 0, eta: int = -1) -> str:
    """One writing drawer (sdb, drawer 2, "SATA 1"). The agent writes rate 0 and eta -1 at once while a drawer stands
    (interfaces.md: fields 7-8 of `drawer=`), and the last speed while it moves."""
    now = int(time.time())
    return ("cloner=Office 2024\nmachine_name=2\nscreen=cloner\n"
            f"drawer=2|sdb|writing|{int(0.44 * 19.5 * G)}|{int(19.5 * G)}||{rate}|{eta}|2|SATA 1\n"
            + "".join(r + "\n" for r in records) + f"updated={now}\n")


def _screen(driver: Path, tmp_path: Path, name: str, state: str):
    src = tmp_path / f"{name}.txt"
    src.write_text(state, encoding="utf-8", newline="\n")
    png = tmp_path / f"{name}.png"
    r = subprocess.run([str(driver), posix(src), posix(png)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    texts = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    rows = [tuple(float(v) for v in l.split()[2:]) for l in r.stdout.splitlines() if l.startswith("@ row ")]
    assert rows, r.stdout                       # the drawer was drawn, with its "בטל…"
    return r, texts, rows[0], _png_rgb(png)


def _orange(image, row) -> int:
    """How many pixels of the drawer's row (a band round its "בטל…" button, the whole width) are the theme's `warning_soft`: the
    fill of an orange row, a solid area -- its 3px edge is anti-aliased and never one exact colour. Only an orange row has it."""
    width, height, ch, px = image
    p = (795 * width + 5) * ch
    soft = tuple(px[p:p + 3])
    y0, y1 = max(0, int(row[2]) - 60), min(height, int(row[2]) + 60)   # row = (index, x, y, w, h) of the button
    return sum(1 for y in range(y0, y1) for x in range(10, width)
               if tuple(px[(y * width + x) * ch:(y * width + x) * ch + 3]) == soft)


def test_a_drawer_that_waits_for_its_disk_says_so_in_orange(driver, tmp_path):
    r, texts, row, image = _screen(driver, tmp_path, "disk", _state("drawer_stall=sdb|waiting_disk|6|150"))
    assert "ממתין לדיסק… הכתיבה עומדת 6 שניות · מהירות —" in texts, texts
    assert "ממתין" in texts and not [t for t in texts if t.startswith("כותב ·")], texts   # the tag; not "writing, 0 MB/s"
    assert "ממתין לזרם מהשרת · \u20680 MB/s\u2069" not in texts, texts                    # the agent's own word wins over the guess
    assert _orange(image, row) > 5000, "the waiting row is not orange"


def test_a_drawer_that_waits_for_the_network_says_so_and_that_the_disk_is_not_erased(driver, tmp_path):
    _, texts, row, image = _screen(driver, tmp_path, "net", _state("drawer_stall=sdb|waiting_net|40|300"))
    assert "הרשת נותקה — ממתין 40 שניות · הדיסק לא נמחק" in texts, texts
    assert "ממתין לרשת" in texts, texts
    assert _orange(image, row) > 5000


def test_the_agents_limit_is_never_shown_only_the_time_that_has_passed(driver, tmp_path):
    """Nadav 12/11 (PR #2997, #3726): "only the time that has passed, no 'ייכשל אחרי X'" -- a number that mostly does not hold.
    The limit stays on the wire; with one, or without, the screen says the same."""
    for kind, said in (("waiting_disk", "ממתין לדיסק… הכתיבה עומדת 9 שניות · מהירות —"),
                       ("waiting_net", "הרשת נותקה — ממתין 9 שניות · הדיסק לא נמחק")):
        for limit in ("150", "300", ""):
            _, texts, _, _ = _screen(driver, tmp_path, f"limit{kind}{limit}", _state(f"drawer_stall=sdb|{kind}|9|{limit}"))
            assert said in texts, (kind, limit, texts)
            assert not [t for t in texts if "ייכשל" in t or "(עד" in t], texts


def test_a_drawer_that_moves_has_no_waiting_and_is_not_orange(driver, tmp_path):
    """The guard: no record = the drawer moves. Rate 15 MB/s, as before -- nothing of the above is drawn."""
    r, texts, row, image = _screen(driver, tmp_path, "moving", _state(rate=15_000_000, eta=840))
    assert "כותב · \u206815 MB/s\u2069" in texts, texts
    assert not [t for t in texts if "ממתין לדיסק…" in t or "הרשת נותקה" in t or t == "ממתין לרשת"], texts
    assert _orange(image, row) == 0


@pytest.mark.parametrize("record", [
    "drawer_stall=sdb|waiting_cpu|6|150",    # a word the agent does not say: no record, never a guess
    "drawer_stall=sdz|waiting_disk|6|150",   # another drawer's: this one moves
    "drawer_stall=sdb|waiting_disk|6",       # four fields are the contract, three are not a record
])
def test_a_record_that_is_not_one_draws_nothing(driver, tmp_path, record):
    _, texts, row, image = _screen(driver, tmp_path, "bad", _state(record, rate=15_000_000, eta=840))
    assert not [t for t in texts if "ממתין לדיסק…" in t or "הרשת נותקה" in t], texts
    assert "כותב · \u206815 MB/s\u2069" in texts, texts
    assert _orange(image, row) == 0


def test_the_key_is_known_so_the_log_is_not_filled(driver, tmp_path):
    """The agent writes the state every couple of seconds while a drawer stands: one stderr line each time it was unknown."""
    r, _, _, _ = _screen(driver, tmp_path, "quiet", _state("drawer_stall=sdb|waiting_disk|6|150"))
    assert "unknown key" not in r.stderr, r.stderr
