"""#2081 — a held end-of-task screen draws ONE power-off button.

Nadav 24/10: "בתמונה ששלחת יש פעמיים כפתור כיבוי." The #2024 bar ("המחשב
נשאר דלוק כי יש דיסק עם בעיה") drew its own "כבה" over the local clone's
result, which already has "כבה את המחשב". The approved mockup
(docs/design/mockups-1024/03-one-poweroff-2081.html, 9fc05f14) for BOTH
screens -- the local clone's result and the cloner after a round from the
server: "כבה את המחשב" is a button inside the screen, and the bar is a
sentence only, naming that button.

What is checked (cc + pango, the Testrunner): a driver renders the real
screens with ``text_make`` wrapped (``ld --wrap``) and prints every string
drawn and every hit target, so the assertions are on the words and on the
clickable buttons -- not on pixels:

* the only power-off label drawn is "כבה את המחשב" (a label is measured and
  drawn, so the strings are a set -- the count is the hit table's);
* exactly one power-off hit target exists, and on the cloner it is the end's
  own (HIT_END_NOW -> end-poweroff), so the button really powers off;
* the bar says the new sentence, never the old "לחצו 'כבה'".

**Negative control** (in the commit): with the base's production files the
held local clone draws "כבה את המחשב" and "כבה" (two hit targets), and the
held cloner has no in-screen button.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_cloner_gui import BASH, GUI, posix
from test_lc_progress_1437_1440 import DISK, DISK2, needs_cc

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static int logging;
Text __real_text_make(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d);
Text __real_text_make_ltr(cairo_t *cr, Font f, double px, int w, const char *s, int wp);
Text __wrap_text_make(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d) {
    if (logging) printf("T %s\n", s);
    return __real_text_make(cr, f, px, w, s, wp, d);
}
Text __wrap_text_make_ltr(cairo_t *cr, Font f, double px, int w, const char *s, int wp) {
    if (logging) printf("T %s\n", s);
    return __real_text_make_ltr(cr, f, px, w, s, wp);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    snprintf(a.mac, sizeof a.mac, "3C:52:82:A1:00:1F");
    printf("K %d %d\n", HIT_END_NOW, HIT_LC_OFF);
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "r"); if (!f) { perror(argv[i]); return 2; }
        state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a);
        cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
        cairo_t *cr = cairo_create(s);
        printf("@ %d\n", i); logging = 1; app_draw(&a, cr, 1280, 800); logging = 0;
        for (int h = 0; h < a.nhits; h++) printf("H %d\n", a.hits[h].id);
        fflush(stdout);
        cairo_destroy(cr); cairo_surface_destroy(s);
    }
    return 0;
}
'''

POWER_LABELS = {"כבה", "כבה עכשיו", "כבה את המחשב"}
OWN_SENTENCE = 'המחשב נשאר דלוק כי יש דיסק עם בעיה. כשתסיימו: "כבה את המחשב".'
OLD_SENTENCE = "המחשב נשאר דלוק כי יש דיסק עם בעיה — לחצו 'כבה' כשסיימתם"

CLONER = ("cloner=Win11\ndrawer_count=3\n"
          "drawer=1|sda|done|100|100||-1|-1|1|SATA 0||\n"
          "drawer=2|sdb|failed|10|100|הזרם נקטע|-1|-1|2|SATA 1||\n"
          "drawer=3|sdc|done|100|100||-1|-1|3|SATA 2||\n")
HELD = "end_poweroff=0|partial|2|3|1\nend_problem=sdb|r|הזרם נקטע באמצע מחיצה 2\n"
LOCAL = ("lc_screen=local\nlc_inv=done\nlc_phase=done\n" + DISK + DISK2 +
         "lc_run=sdc|206000000000|206000000000|900|0|sent|2||\n"
         "lc_part=1|EFI|vfat|3030000000|3030000000|done|0\n"
         "lc_tgt=sdb|done|206000000000|2|2||||206000000000\n")
LOCAL_HELD = "end_poweroff=0|localclone|1|1|1\nend_problem=sdb|o|האימות אחרי הכתיבה לא נרשם\n"


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("off2081")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    assert srcs, "make print-gui-src printed no sources"
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -Wl,--wrap=text_make_ltr '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def frame(driver: Path, tmp_path: Path, state: str) -> tuple[list[str], list[int], int, int]:
    """One state -> (the strings drawn, the hit ids, HIT_END_NOW, HIT_LC_OFF)."""
    p = tmp_path / "s.txt"
    p.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    end_now, lc_off = (int(x) for x in lines[0].split()[1:])
    assert lines[1] == "@ 1", out.stdout
    texts = [ln[2:] for ln in lines if ln.startswith("T ")]
    hits = [int(ln[2:]) for ln in lines if ln.startswith("H ")]
    return texts, hits, end_now, lc_off


@needs_cc
def test_the_held_local_clone_result_has_one_power_off(driver, tmp_path):
    texts, hits, end_now, lc_off = frame(driver, tmp_path, LOCAL + LOCAL_HELD)
    assert {s for s in texts if s in POWER_LABELS} == {"כבה את המחשב"}, texts   # measured + drawn: a set
    assert [h for h in hits if h in (end_now, lc_off)] == [lc_off], hits
    assert OWN_SENTENCE in texts and OLD_SENTENCE not in texts, texts


@needs_cc
def test_the_held_cloner_end_has_its_own_power_off_and_only_it(driver, tmp_path):
    texts, hits, end_now, lc_off = frame(driver, tmp_path, CLONER + HELD)
    assert {s for s in texts if s in POWER_LABELS} == {"כבה את המחשב"}, texts   # measured + drawn: a set
    assert [h for h in hits if h in (end_now, lc_off)] == [end_now], hits   # end-poweroff (main.c end_modal)
    assert OWN_SENTENCE in texts and OLD_SENTENCE not in texts, texts
    assert "הזרם נקטע באמצע מחיצה 2" in texts                          # #2024's reason stays


@needs_cc
def test_a_cloner_that_is_not_held_draws_no_power_off_button(driver, tmp_path):
    """The guard on the other side: the in-screen button belongs to a held end only."""
    texts, hits, end_now, lc_off = frame(driver, tmp_path, CLONER)
    assert not [s for s in texts if s in POWER_LABELS], texts
    assert end_now not in hits and lc_off not in hits, hits
