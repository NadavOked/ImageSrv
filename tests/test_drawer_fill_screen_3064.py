"""#3064 (R5, המוקאפ המאושר `production-line-mockup-1114`): מה המשכפל והקונסולה אומרים על מגירה שחזרה ועוד משלימה.

אין רכיב חדש: התג "מצטרף ומשלים" ושורת המצב "משלים מחיצה P · חסרות N חתיכות (Y% מהמחיצה)". שלוש שכבות:

* **הסוכן** (`clonergui.sh`, `drawer_fill_record`): הרשומה `drawer_fill=<dev>|<part>|<missing>|<chunks>` מתוך `targets/<dev>/rejoin`
  של `drawerfill.sh`. בלי ספירה אין רשומה, ושדה שאינו ספירה ריק ("לא נאמר", לעולם לא 0);
* **המסך** (`state.c`, `screens_cloner.c`): המנתח האמיתי והמסך האמיתי של המשכפל, כמו `test_drawer_pulled_screen_3270.py`;
* **הקונסולה** (`console-prodline.js`): `tests/prodline_console_3181.test.cjs`, הטסט האחרון בו.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_cloner_gui import BASH, GUI, PORTS, PRELUDE, _pkgconfig, env, posix, sh, target

G = 1_000_000_000


# --- הסוכן: הרשומה ---------------------------------------------------------------------------------------


def _record(tmp_path: Path, rejoin: str | None, state: str = "writing") -> list[str]:
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(parents=True, exist_ok=True)
    gui.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", state, 20 * G, 0, str(8 * G))
    if rejoin is not None:
        (run / "targets" / "sda" / "rejoin").write_text(rejoin + "\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + agent_sources("stall") + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


def _fill(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("drawer_fill=")]


@requires_native("jq", ("bash", BASH), why="json_get reads the rejoin file with jq")
@pytest.mark.parametrize("rejoin, want", [
    ('{"part":3,"missing":412,"chunks":18254,"away_s":21.5,"step":"neighbor"}', "drawer_fill=sda|3|412|18254"),
    ('{"part":3,"missing":0,"chunks":100,"away_s":null,"step":"http"}', "drawer_fill=sda|3|0|100"),
    ('{"missing":9}', "drawer_fill=sda||9|"),                      # part and chunks not said: empty, never 0
    ('{"part":"x","missing":9,"chunks":-4}', "drawer_fill=sda||9|"),   # not counts: the same
])
def test_the_record_carries_what_the_drawer_said_and_leaves_the_rest_empty(tmp_path, rejoin, want):
    got = _fill(_record(tmp_path, rejoin))
    assert got == [want], got


@requires_native("jq", ("bash", BASH), why="json_get reads the rejoin file with jq")
@pytest.mark.parametrize("rejoin", ['{"part":3,"chunks":100}', '{"missing":"7"}', '{"missing":-1}', "not json", ""])
def test_no_count_is_no_record(tmp_path, rejoin):
    assert _fill(_record(tmp_path, rejoin)) == [], "ספירה שלא נאמרה אינה אפס"


@requires_native("jq", ("bash", BASH), why="json_get reads the rejoin file with jq")
def test_no_file_and_a_drawer_that_is_not_writing_have_no_record(tmp_path):
    assert _fill(_record(tmp_path, None)) == []
    assert _fill(_record(tmp_path / "again", '{"part":3,"missing":5,"chunks":9}', state="done")) == []


# --- המסך: המנתח האמיתי והציור האמיתי ------------------------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
#include "theme.h"
#include "screens_rounds.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
void __real_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g);
void __wrap_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g) {
    printf("bay=%d|%s|%s|%.2f\n", g->kind, g->pill ? g->pill : "", g->line, g->share);
    __real_lc_bay_draw(a, cr, b, g);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f);
    for (int i = 0; i < a.st.ndrawers; i++)
        printf("drawer=%s|%d|%lld|%lld|%lld\n", a.st.drawers[i].dev, a.st.drawers[i].fill, a.st.drawers[i].fill_part,
               a.st.drawers[i].fill_missing, a.st.drawers[i].fill_chunks);
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_destroy(s);
    return 0;
}
'''

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("fill3064")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -Werror -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=lc_bay_draw -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def run_driver(driver: Path, tmp_path: Path, state: str) -> list[str]:
    p = tmp_path / "state.txt"
    p.write_text(state, encoding="utf-8", newline="\n")
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def state(first_extra: str = "") -> str:
    """Two drawers, 8 of 20 GB written; <first_extra> = the records after drawer 1's."""
    return ("cloner=classroom-win11 v7\n"
            f"drawer=1|sda|writing|{8 * G}|{20 * G}||55000000|840|1|SATA 0\n{first_extra}"
            f"drawer=2|sdb|writing|{8 * G}|{20 * G}||55000000|840|2|SATA 1\n"
            f"forecast=1|4102483920|\nupdated={int(time.time())}\n")


def bays(lines: list[str]) -> list[list[str]]:
    return [l[4:].split("|") for l in lines if l.startswith("bay=")]


@_GUI
def test_the_record_is_read_and_a_completing_drawer_says_so_in_the_mockups_words(driver, tmp_path):
    got = run_driver(driver, tmp_path, state("drawer_fill=sda|3|412|1000\n"))
    assert "drawer=sda|1|3|412|1000" in got and "drawer=sdb|0|0|0|0" in got, "הרשומה נקראת, ומגירה אחרת אינה משלימה"
    first, second = bays(got)[:2]
    assert first[1:3] == ["מצטרף ומשלים", "משלים מחיצה 3 · חסרות 412 חתיכות (41% מהמחיצה)"], first
    assert second[1] == "" and second[2].startswith("כותב"), "המגירה השנייה כותבת כרגיל"


@_GUI
@pytest.mark.parametrize("record, line", [
    ("drawer_fill=sda||412|\n", "משלים · חסרות 412 חתיכות"),                      # not said: no part, no share -- never "0"
    ("drawer_fill=sda|3|412|100\n", "משלים מחיצה 3 · חסרות 412 חתיכות"),          # more missing than the whole: no share
    ("drawer_fill=sda|3|0|100\n", "משלים מחיצה 3 · חסרות 0 חתיכות (0% מהמחיצה)"),
])
def test_what_is_not_said_is_not_drawn(driver, tmp_path, record, line):
    first = bays(run_driver(driver, tmp_path, state(record)))[0]
    assert first[1:3] == ["מצטרף ומשלים", line], first


@_GUI
@pytest.mark.parametrize("record", ["drawer_fill=sda|3||1000\n", "drawer_fill=sda|3|x|1000\n", "drawer_fill=sda|3|-1|1000\n",
                                    "drawer_fill=sdz|3|5|10\n"])
def test_a_record_without_a_count_or_for_no_drawer_changes_nothing(driver, tmp_path, record):
    first = bays(run_driver(driver, tmp_path, state(record)))[0]
    assert first[1] == "" and first[2].startswith("כותב"), first
