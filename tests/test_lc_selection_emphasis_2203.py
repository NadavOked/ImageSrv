"""#2203: approved selection cards, through real cairo PNGs and text/geometry logs.

Like #2050, observe the production drawing calls without replacing their rendering.
The old code fails the 3px/fill checks (including hardware-only warnings) and hides
the SMART reason on a warned source. Plain targets must retain their old appearance.
"""

import os
import subprocess

import pytest

from test_cloner_gui import BASH, GUI, _assert_emphasized_frame, _png_rgb, _token, posix
from test_native_local_clone import pytestmark  # noqa: F401


DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
void __real_draw_border_rrect(cairo_t *, Rect, double, Rgb, double);
void __wrap_draw_border_rrect(cairo_t *cr, Rect r, double rad, Rgb c, double lw) {
    if (r.w > 300 && r.h > 50)
        printf("B %.1f %.1f %.1f %.1f %.0f %.0f %.0f %.0f\n",
               r.x, r.y, r.w, r.h, lw, c.r*255, c.g*255, c.b*255);
    __real_draw_border_rrect(cr, r, rad, c, lw);
}
Text __real_text_make_markup(cairo_t *, Font, double, int, const char *, int, Dir);
Text __wrap_text_make_markup(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d) {
    printf("M %s\n", s);
    return __real_text_make_markup(cr, f, px, w, s, wp, d);
}
void __real_text_draw_r(cairo_t *, Text *, double, double, Rgb);
void __wrap_text_draw_r(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    if (!strcmp(pango_layout_get_text(t->layout), "1"))
        printf("N %.0f %.0f %.0f\n", c.r*255, c.g*255, c.b*255);
    printf("T %.0f %.0f %.0f %.0f %.0f %s\n", x, y, c.r*255, c.g*255, c.b*255, pango_layout_get_text(t->layout));
    __real_text_draw_r(cr, t, x, y, c);
}
int main(int argc, char **argv) {
    if (argc != 6) return 2;
    static App a; a.theme = !strcmp(argv[2], "dark") ? &THEME_DARK : &THEME_LIGHT;
    a.force_cloner = 1; a.fixed_clock = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    if (a.st.lc.ndisks != 2) return 4;
    /* Explicit operator roles: do not depend on the inventory's default proposal. */
    a.lcu.nroles = 2;
    for (int i = 0; i < 2; i++) {
        snprintf(a.lcu.role_dev[i], sizeof a.lcu.role_dev[i], "%s", a.st.lc.disks[i].dev);
        a.lcu.role[i] = i == atoi(argv[4]) ? LCR_SOURCE : LCR_TARGET;
    }
    a.lcu.focus = atoi(argv[5]) ? LCF_BAY : LCF_NEXT; a.lcu.bay = 0;
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800);
    int bays = 0;
    for (int h = 0; h < a.nhits; h++) {
        int i = a.hits[h].id - HIT_LC_BAY_BASE;
        if (i < 0 || i >= 2) continue;
        Rect r = a.hits[h].r;
        printf("R %d %.0f %.0f %.0f %.0f\n", i, r.x, r.y, r.w, r.h); bays++;
    }
    cairo_status_t status = cairo_status(cr);
    cairo_status_t png = cairo_surface_write_to_png(s, argv[3]);
    cairo_destroy(cr); cairo_surface_destroy(s);
    return status == CAIRO_STATUS_SUCCESS && png == CAIRO_STATUS_SUCCESS && bays == 2 ? 0 : 5;
}
'''


@pytest.fixture(scope="module")
def selection_driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("selection2203")
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
        '-Wl,--wrap=draw_border_rrect,--wrap=text_make_markup,--wrap=text_draw_r '
        '-o "$2" "$3" "${@:4}" $(pkg-config --libs pangocairo cairo libdrm) -lm',
        "_", posix(GUI), posix(binary), posix(driver), *srcs],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


CHIPS = {"SMART תקול", "קריאה בלבד", "אזהרת SMART", "נכשל בשיכפול הקודם", "SMART תקין", "SMART לא נבדק"}
CABLE ="שגיאות CRC רבות בקישור — כבל או מגירה חשודים, לא הדיסק"


def _render(binary, tmp_path, theme, usb, *, smart="ok", mark="", hw="", source=False,
            verdict="fits", focus=False):
    state = tmp_path / "state.txt"
    png = tmp_path / "selection.png"
    src = "sda" if source else "sdb"
    state.write_text(
        f"lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb={int(usb)}\n"
        f"lc_disk=1|sda|Samsung|S1|500000000000|{smart}|reported_uncorrectable|Windows|3|100000000000|500000000000|1||1|SATA|{mark}|{hw}\n"
        "lc_disk=2|sdb|Crucial|S2|500000000000|ok|ok|Windows|3|100000000000|500000000000|1||2|SATA||\n"
        f"lc_src={src}|done|100000000000|\nlc_fit=sda|{verdict}|100000000000|\n"
        "lc_fit=sdb|fits|100000000000|\n", encoding="utf-8", newline="\n")
    run = subprocess.run([str(binary), posix(state), theme, posix(png), "0" if source else "1", str(int(focus))],
                         capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL,
                         env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr + run.stdout
    lines = run.stdout.splitlines()
    rects = [tuple(map(int, line.split()[2:])) for line in lines if line.startswith("R 0 ")]
    assert len(rects) == 1, lines
    return lines, _png_rgb(png), rects[0]


def _pixel(image, x, y):
    w, h, ch, px = image
    assert 0 <= x < w and 0 <= y < h
    p = (y * w + x) * ch
    return tuple(px[p:p + 3])


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("usb", [False, True], ids=["cloner", "usb"])
@pytest.mark.parametrize("state,tone", [
    ({"source": True}, "action"),
    ({"smart": "fail"}, "danger"),
    ({"smart": "warn"}, "warning"),
    ({"smart": "failed_last"}, "warning"),
    ({"mark": "incomplete"}, "warning"),
    ({"hw": CABLE}, None),  # Nadav 26/10: a cable/CRC note is words, not the card colour -- orange is SMART only.
    ({"verdict": "shrink"}, None),  # Nadav 26/10: a shrink-to-fit is not orange.
    ({"smart": "fail", "hw": CABLE, "mark": "incomplete"}, "danger"),
    ({"source": True, "smart": "warn", "hw": CABLE}, "action"),
    ({}, None),  # A healthy target is NOT a green result card.
    ({"smart": "unknown", "verdict": "measuring"}, None),
], ids=["source", "red", "orange", "failed-last", "incomplete", "cable", "shrink",
        "red-wins", "source-wins", "plain", "unmeasured"])
def test_selection_card_frame_fill_number_and_dividers(selection_driver, tmp_path, theme, usb, state, tone):
    lines, image, rect = _render(selection_driver, tmp_path, theme, usb, **state)
    x, y, w, h = rect
    edge = _token("--clr-" + tone if tone else "--clr-border", theme)
    fill = _token("--clr-" + tone + "-soft" if tone else "--clr-surface", theme)
    width = 3 if tone else 1
    frames = [tuple(map(float, line.split()[1:])) for line in lines if line.startswith("B ")]
    assert (*rect, width, *edge) in frames, lines
    if tone:
        _assert_emphasized_frame(image, rect, edge, fill)
    else:
        for i in range(5):
            expected = edge if i == 0 else fill
            for point in [(x + w // 2, y + i), (x + w // 2, y + h - 1 - i),
                          (x + i, y + h // 2), (x + w - 1 - i, y + h // 2)]:
                assert _pixel(image, *point) == expected, (point, expected)
    # At the top of each divider there is no text/control to contaminate the pixel.
    for xx in (x + 48, x + w - 96):
        assert _pixel(image, xx, y + 8) == (fill if tone else edge)
    ink = _token("--clr-" + tone + "-fg" if tone else "--clr-text", theme)
    assert "N " + " ".join(map(str, ink)) in lines, lines
    # On a tinted card no line is drawn in a line colour (danger/warning/success sit
    # at ~4.1:1 on the light soft fills) -- only their -fg inks. The SMART chip is
    # left out: it sits on its own pill, the same as on a plain card.
    if tone and theme == "light":
        raw = {_token("--clr-" + k, theme) for k in ("danger", "warning", "success")}
        inside = [(tuple(map(int, f[3:6])), " ".join(f[6:])) for f in (line.split() for line in lines)
                  if f[0] == "T" and x <= float(f[1]) <= x + w and y <= float(f[2]) <= y + h
                  and " ".join(f[6:]).lstrip("‏") not in CHIPS]
        assert inside and not raw & {c for c, _ in inside}, inside
    if state.get("hw"):
        colour = "#%02X%02X%02X" % _token("--clr-warning-fg", theme)
        assert any(f"<span foreground='{colour}'>{CABLE}</span>" in line for line in lines), lines
    if state.get("source") and state.get("smart") == "warn":
        colour = "#%02X%02X%02X" % _token("--clr-warning-fg", theme)
        assert any(f"<span foreground='{colour}'>שגיאות שלא תוקנו (187)</span>" in line for line in lines), lines


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_emphasized_blocked_card_keeps_hatch_and_focus(selection_driver, tmp_path, theme):
    lines, image, (x, y, w, h) = _render(selection_driver, tmp_path, theme, True,
                                      smart="warn", verdict="short", focus=True)
    frames = [tuple(map(float, line.split()[1:])) for line in lines if line.startswith("B ")]
    assert (x, y, w, h, 3, *_token("--clr-warning", theme)) in frames, lines
    assert (x - 3.5, y - 3.5, w + 7, h + 7, 3, *_token("--clr-action", theme)) in frames, lines
    # The untouched hatch paints alternating tones over the soft fill; it is not flat.
    tones = {_pixel(image, xx, y + 8) for xx in range(x + 100, x + 200)}
    assert len(tones) >= 2, tones
    assert _token("--clr-warning-soft", theme) not in tones, tones
