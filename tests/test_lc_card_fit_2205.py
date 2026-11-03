"""#2205: the local clone's selection cards at 960x540 -- the words under the
drawer's name (the SMART reason, the cable note, "עליו עכשיו", the verdict)
ran past the card's top and bottom edges (found while verifying #2203, 26/10;
the same on main). The real screen, rendered by cairo through the production
sources: every line of each card's name block is read back with its pixel
extents (``text_draw_r`` wrapped, as ``test_lc_selection_emphasis_2203`` does)
and must sit inside its card, clear of the 3px frame -- at 960x540 and at
1024x768, the issue's two sizes. A roomy card (1280x800) keeps every line
whole: the fix may only shorten what does not fit.

The state is the issue's photo (C:\\ImageCtl-Lab\\mockups\\2203-real-*-960x540):
drawer 1 red (skipped), drawer 2 a target and drawer 3 the source, both with
the CRC cable note.
"""

import os
import subprocess

import pytest

from test_cloner_gui import BASH, GUI, posix
from test_native_local_clone import pytestmark  # noqa: F401


DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
void __real_hit_add(App *, Rect, int);
void __wrap_hit_add(App *a, Rect r, int id) {
    printf("H %d %.1f %.1f %.1f %.1f\n", id, r.x, r.y, r.w, r.h);
    __real_hit_add(a, r, id);
}
void __real_text_draw_r(cairo_t *, Text *, double, double, Rgb);
void __wrap_text_draw_r(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    PangoRectangle lg; pango_layout_get_pixel_extents(t->layout, NULL, &lg);   /* the drawn box, not t->h */
    printf("T %.1f %.1f %d %d %s\n", x, y + lg.y, lg.height, pango_layout_is_ellipsized(t->layout) ? 1 : 0,
           pango_layout_get_text(t->layout));
    __real_text_draw_r(cr, t, x, y, c);
}
int main(int argc, char **argv) {
    if (argc != 6) return 2;
    static App a; a.theme = &THEME_LIGHT;
    a.force_cloner = 1; a.fixed_clock = 1;
    int W = atoi(argv[2]), H = atoi(argv[3]), src = atoi(argv[5]);
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    int n = a.st.lc.ndisks; if (n < 2) return 4;
    a.lcu.nroles = n;   /* explicit roles: a red disk is skipped, never a target (#1732) */
    for (int i = 0; i < n; i++) {
        snprintf(a.lcu.role_dev[i], sizeof a.lcu.role_dev[i], "%s", a.st.lc.disks[i].dev);
        a.lcu.role[i] = i == src ? LCR_SOURCE : !strcmp(a.st.lc.disks[i].smart, "fail") ? LCR_SKIP : LCR_TARGET;
    }
    a.lcu.focus = LCF_NEXT;
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, W, H);
    cairo_status_t status = cairo_status(cr);
    cairo_status_t png = cairo_surface_write_to_png(s, argv[4]);
    cairo_destroy(cr); cairo_surface_destroy(s);
    return status == CAIRO_STATUS_SUCCESS && png == CAIRO_STATUS_SUCCESS ? 0 : 5;
}
'''

#: screens_local.c: the name block's right edge is the card's right edge less the
#: number column (LS_NUM_W 96), its 1px line and the 22px gap
NAME_RIGHT = 96 + 1 + 22
BAY_BASE, ROLE_BASE, ROLE_END = 3000, 3100, 3200   # ui.h HIT_LC_BAY_BASE / HIT_LC_ROLE_BASE / HIT_LC_DROP_BASE
FRAME = 3   # the emphasized card's frame (#2203)

CABLE = "שגיאות CRC רבות בקישור — כבל או מגירה חשודים, לא הדיסק"
G = 500000000000
#: the photo: 1 red and skipped, 2 a target, 3 the source -- 2 and 3 with the cable note
PHOTO = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb=0\n"
         f"lc_disk=1|sda|SAMSUNG MZ7TY256|S1|256000000000|fail|health_failed|Windows|3|115000000000|115000000000|1||1|SATA||{CABLE}\n"
         f"lc_disk=2|sdb|SAMSUNG MZ7TE256|S2|256000000000|ok|ok|Windows|3|115000000000|115000000000|1||2|SATA||{CABLE}\n"
         f"lc_disk=3|sdc|SAMSUNG MZ7TY256|S3|256000000000|ok|ok|Windows|3|115000000000|115000000000|1||3|SATA||{CABLE}\n"
         "lc_src=sdc|done|115000000000|\n"
         "lc_fit=sda|fits|115000000000|\nlc_fit=sdb|fits|115000000000|\n")
#: roomy: two healthy disks, no notes -- nothing to shorten
ROOMY = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb=0\n"
         f"lc_disk=1|sda|Samsung|S1|{G}|ok|ok|Windows|3|100000000000|{G}|1||1|SATA||\n"
         f"lc_disk=2|sdb|Crucial|S2|{G}|ok|ok|Windows|3|100000000000|{G}|1||2|SATA||\n"
         "lc_src=sda|done|100000000000|\nlc_fit=sdb|fits|100000000000|\n")


@pytest.fixture(scope="module")
def fit_driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("cardfit2205")
    driver, binary = d / "driver.c", d / "driver"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    sources = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert sources.returncode == 0, sources.stderr
    srcs = [posix(GUI / s) for s in sources.stdout.split() if not s.endswith("main.c")]
    assert srcs, "print-gui-src returned no production sources"
    build = subprocess.run([BASH, "-c",
        'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" '
        '$(pkg-config --cflags pangocairo cairo libdrm) '
        '-Wl,--wrap=hit_add,--wrap=text_draw_r '
        '-o "$2" "$3" "${@:4}" $(pkg-config --libs pangocairo cairo libdrm) -lm',
        "_", posix(GUI), posix(binary), posix(driver), *srcs],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _cards(binary, tmp_path, state, size, src):
    """{drawer index: (card rect, [(top, height, ellipsized, text), ...])} -- the
    name block's lines, by the card whose hit was added last before them."""
    w, h = size.split("x")
    path = tmp_path / "state.txt"
    path.write_text(state, encoding="utf-8", newline="\n")
    run = subprocess.run([str(binary), posix(path), w, h, posix(tmp_path / "card.png"), str(src)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr + run.stdout
    cards, cur = {}, None
    for line in run.stdout.splitlines():
        f = line.split(" ", 5)
        if f[0] == "H":
            hid = int(f[1])
            if BAY_BASE <= hid < ROLE_BASE:
                cur = hid - BAY_BASE
                cards[cur] = (tuple(map(float, f[2:6])), [])
            elif not ROLE_BASE <= hid < ROLE_END:
                cur = None   # past the cage: the foot's button, the bar
        elif f[0] == "T" and cur is not None:
            (x, y, cw, ch), lines = cards[cur]
            if abs(float(f[1]) - (x + cw - NAME_RIGHT)) < 0.6:
                lines.append((float(f[2]), int(f[3]), f[4] == "1", f[5] if len(f) > 5 else ""))
    return cards


@pytest.mark.parametrize("size", ["960x540", "1024x768"])
def test_every_line_of_a_card_stays_inside_it(fit_driver, tmp_path, size):
    cards = _cards(fit_driver, tmp_path, PHOTO, size, src=2)
    assert sorted(cards) == [0, 1, 2], cards
    for i, ((x, y, w, h), lines) in cards.items():
        # the name and at least one line under it: shortening is not hiding the card
        assert len(lines) >= 2, (i, lines)
        for top, height, _, text in lines:
            assert y + FRAME <= top and top + height <= y + h - FRAME, (size, i, (y, h), top, height, text)


def test_a_roomy_card_keeps_every_line_whole(fit_driver, tmp_path):
    """The guard against a fix that shortens everything: 1280x800, two plain
    disks -- the target says what it has, that it is erased, and that it fits,
    and no line is ellipsized."""
    cards = _cards(fit_driver, tmp_path, ROOMY, "1280x800", src=0)
    assert sorted(cards) == [0, 1], cards
    _, lines = cards[1]
    texts = " ".join(t for _, _, _, t in lines)
    assert not [t for _, _, e, t in lines if e], lines
    assert "עליו עכשיו" in texts and "יימחק" in texts and "נכנס" in texts, lines
