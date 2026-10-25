"""#2017 -- an orange source is told, not asked (Nadav 22/10: "F1 אדום. באמצע סוף חייו, לפני אדום — כתום").

On the local clone's choice (cloner and USB stick), "המשך לאישור" opened the SMART
window "להעתיק מ... למרות אזהרת ה-SMART?" ("דלג" / "העתק ממנו בכל זאת") for any
warned source (lc_state.c open_confirm, 16/10). Now:

* orange -- ``warn`` (signs of wear before F1, #1859) and ``failed_last`` (failed the
  previous clone with a working SMART: smart_level 1, the orange "נכשל בשיכפול הקודם")
  -- a notice with ONE button, "המשך": Enter, Esc or a click all go on to the confirm,
  and the copy starts from there as from any source;
* red -- ``fail`` (SMART failed, F1) -- the question stays as it was: "דלג" (Esc) or
  "העתק ממנו בכל זאת" (a red source only with an explicit yes, 18/10; never a target);
* green -- no window.

#2008's Enter path (Nadav 22/10): the notice opens on "המשך", so an orange source on the
stick is Enter-Enter-Enter -- "המשך לאישור", "המשך", and the confirmation, which on the
stick now opens on the red "מחק והעתק" (test_usb_enter_focus_2008.py). On the cloner the
confirmation still opens on "חזרה".

Driven through the real lc_handle (lc_state.c) and sdlg_handle (smartdlg.c).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_cloner_gui import BASH, posix
from test_native_local_clone import FITS, GUI, pytestmark  # noqa: F401  (cc + pango/cairo: the Testrunner)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "smartdlg.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
/* weak: a GUI without the notice links too (the negative control is behaviour) */
extern int sdlg_notice(const SmartDlg *d) __attribute__((weak));
static void draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
}
static int hit_of(const char *n) {
    return !strcmp(n, "NEXT") ? HIT_LC_NEXT : !strcmp(n, "GO") ? HIT_LC_GO : !strcmp(n, "SDLG_GO") ? HIT_SDLG_GO : HIT_SDLG_SKIP;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a); draw(&a);
    for (int i = 2; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "key:", 4)) { e.type = UIEV_KEY; e.key = !strcmp(v + 4, "ENTER") ? KEYSYM_ENTER : KEYSYM_ESC; }
        else {
            int id = hit_of(v), found = 0;
            for (int h = a.nhits - 1; h >= 0 && !found; h--) if (a.hits[h].id == id) {
                e.type = UIEV_CLICK; e.x = a.hits[h].r.x + a.hits[h].r.w / 2; e.y = a.hits[h].r.y + a.hits[h].r.h / 2; found = 1; }
            if (!found) { printf("@ nohit %s\n", v); continue; }
        }
        app_route(&a); lc_handle(&a, &e); app_route(&a); draw(&a);
        int buttons = 0;
        for (int h = 0; h < a.nhits; h++) buttons += a.hits[h].id == HIT_SDLG_GO || a.hits[h].id == HIT_SDLG_SKIP;
        printf("@ %s smartwin=%d notice=%d buttons=%d confirm=%d\n", v, a.sdlg.kind == SDLG_LC_SOURCE,
               sdlg_notice && sdlg_notice(&a.sdlg), buttons, a.lcu.confirm);
        printf("@ focus=%d\n", a.lcu.focus); fflush(stdout);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("orangedrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _choose(driver: Path, tmp_path: Path, smart: str, reason: str, *events: str, usb: bool = False) -> list[str]:
    """Drawer 3 (sdc) is the source by default; sda and sdb (green) the targets."""
    state = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
             "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
             "lc_disk=2|sdb|WDC|S2|500107862016|ok|ok|Ubuntu|2|38000000000|500107862016|1|\n"
             f"lc_disk=3|sdc|Crucial|S3|480103981056|{smart}|{reason}|Windows|4|148600000000|480103981056|1|\n" + FITS
             + ("lc_usb=1\n" if usb else ""))
    path = tmp_path / "state.txt"; path.write_text(state, encoding="utf-8", newline="\n")
    run = subprocess.run([str(driver), posix(path), *events], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    return [l for l in run.stdout.splitlines() if l]


START = "lc-start|sdc|sda,sdb|0"


@pytest.mark.parametrize("smart,reason", [("warn", "reported_uncorrectable"),   # #1859: 187 > 0, before F1
                                          ("failed_last", "disk")])            # failed the previous clone, SMART working
@pytest.mark.parametrize("answer", ["key:ENTER", "key:ESC", "SDLG_GO"])       # every answer goes on: nothing to cancel
def test_an_orange_source_is_a_notice_with_one_button_and_the_copy_starts(driver, tmp_path, smart, reason, answer):
    out = _choose(driver, tmp_path, smart, reason, "NEXT", answer, "GO")
    assert "@ NEXT smartwin=1 notice=1 buttons=1 confirm=0" in out, out      # the notice, "המשך" alone
    assert f"@ {answer} smartwin=0 notice=0 buttons=0 confirm=1" in out, out  # on to the confirm
    assert START in out, out                                                 # "מחק והעתק" starts the copy


def test_a_red_source_still_asks_and_esc_does_not_copy(driver, tmp_path):
    out = _choose(driver, tmp_path, "fail", "health_failed", "NEXT", "key:ESC", "GO")
    assert "@ NEXT smartwin=1 notice=0 buttons=2 confirm=0" in out, out      # "דלג" and "העתק ממנו בכל זאת"
    assert "@ key:ESC smartwin=0 notice=0 buttons=0 confirm=0" in out, out    # Esc = "דלג": no confirm
    assert not any(l.startswith("lc-start") for l in out), out


def test_a_red_source_copies_only_on_its_explicit_yes(driver, tmp_path):
    out = _choose(driver, tmp_path, "fail", "health_failed", "NEXT", "SDLG_GO", "GO")
    assert "@ SDLG_GO smartwin=0 notice=0 buttons=0 confirm=1" in out, out
    assert START in out, out


def test_a_green_source_opens_no_window(driver, tmp_path):
    out = _choose(driver, tmp_path, "ok", "ok", "NEXT", "GO")
    assert "@ NEXT smartwin=0 notice=0 buttons=0 confirm=1" in out, out
    assert START in out, out


def test_the_notice_card_is_drawn(tmp_path):
    """--png draws the notice (lcsource-orange), both themes -- the photo for the owner."""
    from test_cloner_gui import _build_gui
    binary = _build_gui(tmp_path)
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "c"), "--size", "1280x800"], capture_output=True, text=True,
                         timeout=300, stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    for card in ("lcsource-orange", "lcconfirm-usb"):   # the notice; the stick's confirmation (focus on "מחק והעתק")
        for theme in ("light", "dark"):
            assert (tmp_path / f"c-{card}-{theme}.png").stat().st_size > 10000, (card, theme)


FOCUS_GO, FOCUS_CANCEL = "@ focus=3", "@ focus=4"   # LCF_GO ("מחק והעתק"), LCF_CANCEL ("חזרה")


@pytest.mark.parametrize("smart,reason", [("warn", "reported_uncorrectable"), ("failed_last", "disk")])
def test_an_orange_source_on_the_stick_is_enter_enter_enter(driver, tmp_path, smart, reason):
    out = _choose(driver, tmp_path, smart, reason, "key:ENTER", "key:ENTER", "key:ENTER", usb=True)
    steps = [l for l in out if l.startswith("@ key:")]
    assert steps[0] == "@ key:ENTER smartwin=1 notice=1 buttons=1 confirm=0", out   # "המשך לאישור" -> the notice
    assert steps[1] == "@ key:ENTER smartwin=0 notice=0 buttons=0 confirm=1", out   # "המשך" -> the confirmation
    assert out[out.index(steps[1]) + 1] == FOCUS_GO, out                            # ... on "מחק והעתק" (the stick)
    assert START in out[out.index(steps[1]):], out                                  # the third Enter starts


def test_an_orange_source_on_the_cloner_reaches_the_confirmation_on_back(driver, tmp_path):
    out = _choose(driver, tmp_path, "warn", "reported_uncorrectable", "key:ENTER", "key:ENTER", "key:ENTER")
    steps = [l for l in out if l.startswith("@ key:")]
    assert steps[1] == "@ key:ENTER smartwin=1 notice=1 buttons=1 confirm=0", out   # drawers -> "המשך לאישור" -> the notice
    assert steps[2] == "@ key:ENTER smartwin=0 notice=0 buttons=0 confirm=1", out
    assert out[-1] == FOCUS_CANCEL, out                                             # the cloner: "חזרה"
    assert not any(l.startswith("lc-start") for l in out), out


def test_a_green_source_on_the_stick_is_enter_enter(driver, tmp_path):
    out = _choose(driver, tmp_path, "ok", "ok", "key:ENTER", "key:ENTER", usb=True)
    assert START in out, out


def test_a_green_source_on_the_cloner_enter_enter_starts_nothing(driver, tmp_path):
    out = _choose(driver, tmp_path, "ok", "ok", "key:ENTER", "key:ENTER")
    assert out[-1] == FOCUS_CANCEL and not any(l.startswith("lc-start") for l in out), out


# ---- the SMART chip on the source's row (Nadav 23/10, found on metal: screens_local.c drew it only for non-sources) ----

CHIP_DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static int near(const unsigned char *p, Rgb c) {   /* RGB24 little-endian: B G R x */
    return abs(p[2] - (int)(c.r * 255 + .5)) <= 1 && abs(p[1] - (int)(c.g * 255 + .5)) <= 1 && abs(p[0] - (int)(c.b * 255 + .5)) <= 1;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_surface_flush(s);
    const unsigned char *px = cairo_image_surface_get_data(s); int stride = cairo_image_surface_get_stride(s);
    for (int h = 0; h < a.nhits; h++) {
        int id = a.hits[h].id; if (id < HIT_LC_BAY_BASE || id >= HIT_LC_BAY_BASE + 8) continue;
        Rect r = a.hits[h].r; long ok = 0, warn = 0, bad = 0;
        for (int y = (int)r.y; y < (int)(r.y + r.h); y++) for (int x = (int)r.x; x < (int)(r.x + r.w); x++) {
            const unsigned char *p = px + (long)y * stride + 4L * x;
            ok += near(p, THEME_LIGHT.success_soft); warn += near(p, THEME_LIGHT.warning_soft); bad += near(p, THEME_LIGHT.danger_soft);
        }
        printf("bay%d ok=%ld warn=%ld red=%ld\n", id - HIT_LC_BAY_BASE, ok, warn, bad);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def chip_driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("chipdrv")
    (d / "driver.c").write_text(CHIP_DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _soft_pixels(driver: Path, tmp_path: Path, smart: str, reason: str) -> dict[str, tuple[int, int, int]]:
    """Per bay: (green, orange, red) chip-background pixels. Drawer 3 (sdc) is the source; sda/sdb are green."""
    state = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
             "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
             "lc_disk=2|sdb|WDC|S2|500107862016|ok|ok|Ubuntu|2|38000000000|500107862016|1|\n"
             f"lc_disk=3|sdc|Crucial|S3|480103981056|{smart}|{reason}|Windows|4|148600000000|480103981056|1|\n" + FITS)
    path = tmp_path / "state.txt"; path.write_text(state, encoding="utf-8", newline="\n")
    run = subprocess.run([str(driver), posix(path)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    out = {}
    for l in filter(lambda l: l.startswith("bay"), run.stdout.splitlines()):
        name, *kv = l.split(); out[name] = tuple(int(x.split("=")[1]) for x in kv)
    return out


MIN_CHIP_PX = 300   # a pill is ~100 x 22 px; the row's own background is indigo_soft, never one of the three


@pytest.mark.parametrize("smart,reason,slot", [("ok", "ok", 0), ("warn", "reported_uncorrectable", 1),
                                               ("failed_last", "disk", 1), ("fail", "health_failed", 2)])
def test_the_source_row_carries_the_smart_chip_in_its_colour(chip_driver, tmp_path, smart, reason, slot):
    src = max((v for k, v in _soft_pixels(chip_driver, tmp_path, smart, reason).items() if k == "bay2"), default=None)
    assert src is not None, "no source bay"
    assert src[slot] >= MIN_CHIP_PX, src
    assert all(src[i] < MIN_CHIP_PX for i in range(3) if i != slot), src
