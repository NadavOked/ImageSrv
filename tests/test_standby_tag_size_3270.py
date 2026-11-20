"""#3270 -- the tags of the cloner's wait screen are all "SMART תקין"'s size (Nadav, 14/11, 15/11).

The rule (Nadav 14/11, on metal: "נכשל" and "אפשר להוציא" were too big): every disk-state tag is the
size of "SMART תקין" -- 24px, 12/600 -- on every screen. The round screens were fixed (#3270, #3278,
#3304); the wait screen still drew every drawer that carries a colour (orange, red, yellow) with
``sb_bay_pill(..., emphasis=1)``: 28px, 13/600. The colour is the frame's and the tag's own; the
size is not. Nothing but the size changes.

Measured on the real sources: ``screen_standby`` through ``app_draw`` with the real state parser;
what the screen asked cairo for -- the label's font size and the rounded rectangle it fills for it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from disk_rules_state import with_rules   # #3666
from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "state.h"
#include "screens_rounds.h"
#include "text.h"
#include "draw.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text px=%g w=%d %s\n", px, weight, utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
void __real_draw_fill_rrect(cairo_t *cr, Rect r, double radius, Rgb c);
void __wrap_draw_fill_rrect(cairo_t *cr, Rect r, double radius, Rgb c) {
    printf("rrect h=%.1f r=%.1f\n", r.h, radius);
    __real_draw_fill_rrect(cr, r, radius, c);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    if (argc < 2) return 2;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    a.force_cloner = 1; a.force_standby = 1; app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_flush(s);
    if (argc > 2) cairo_surface_write_to_png(s, argv[2]);
    printf("@ screen %d\n", a.screen);
    cairo_surface_destroy(s);
    return 0;
}
'''

HEAD = "hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
MODEL = "Samsung SSD 870 EVO|500107862016"

#: (label the drawer's tag says, the cdisk record). The first is the reference: green, no colour.
GREEN = ("SMART תקין", f"cdisk=1|sda|{MODEL}|passed||6.0|6.0|\n")
COLOURED = [
    ("SMART חצי תקין", f"cdisk=2|sdb|{MODEL}|warn|pending_sectors|6.0|6.0|\n"),            # orange
    ("SMART תקול", f"cdisk=3|sdc|{MODEL}|fail|health_failed|6.0|6.0|\n"),                  # red
    ("נכשל בשיכפול הקודם", f"cdisk=4|sdd|{MODEL}|failed_last|disk|6.0|6.0|\n"),            # yellow
    ("בעיית SATA", f"cdisk=8|sdh|{MODEL}|passed||6.0|6.0|||||||||link_reset\n"),   # red: the link was reset
    ("כתיבה לא הושלמה", f"cdisk=6|sdf|{MODEL}|passed||6.0|6.0||||incomplete\n"),            # yellow, a cut write
]
PLAIN = ("SMART לא נבדק", f"cdisk=7|sdg|{MODEL}|||6.0|6.0|\n")


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("standby3270")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    assert "src/screens_standby.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -Wl,--wrap=draw_fill_rrect -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _tags(driver: Path, tmp_path: Path, name: str, records: list[str]) -> dict[str, tuple[float, float, float]]:
    """label -> (font px, the tag's height, its corner radius): the label's own text_make and the
    rounded rectangle sb_bay_pill fills right after it."""
    src = tmp_path / f"{name}.txt"
    src.write_text(with_rules(HEAD + "".join(records) + "message=x|y\n"), encoding="utf-8", newline="\n")   # #3666: the verdicts the agent adds
    r = subprocess.run([str(driver), posix(src), posix(tmp_path / f"{name}.png")], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")),
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert any(l.startswith("@ screen ") for l in lines), r.stdout
    out: dict[str, tuple[float, float, float]] = {}
    for i, l in enumerate(lines):
        if not l.startswith("text px="):
            continue
        _text, px_f, _weight, label = l.split(" ", 3)   # "text px=12 w=600 <label>"
        px = float(px_f[3:])
        label = label.strip("\u200f\u200e")
        nxt = next((x for x in lines[i + 1:i + 3] if x.startswith("rrect ")), None)
        if nxt is None or label in out:
            continue
        h = float(nxt.split()[1][2:]); rad = float(nxt.split()[2][2:])
        out[label] = (px, h, rad)
    return out


def test_every_tag_of_a_coloured_drawer_is_the_smart_ok_size(driver, tmp_path):
    seen = _tags(driver, tmp_path, "all", [GREEN[1]] + [r for _l, r in COLOURED] + [PLAIN[1]])
    px, h, _r = seen[GREEN[0]]
    assert (px, h) == (12.0, 24.0), seen[GREEN[0]]   # the reference is what the rule says it is
    for label in [l for l, _r in COLOURED] + [PLAIN[0]]:
        assert label in seen, (label, sorted(seen))
        assert seen[label][:2] == (px, h), f"the tag '{label}' is {seen[label][1]}px / font {seen[label][0]}, the SMART tag {h}px / {px}"
        assert seen[label][2] == h / 2, (label, seen[label])   # still fully round, as the SMART tag
