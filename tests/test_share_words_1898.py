"""#1898 -- the split question says "הדיסק" for one disk and "הדיסקים" for more (Nadav 22/10).

The question (ask kind "share", screens_ask.c) said "הדיסק גדול מהמקור" and "אל תכתוב את
הדיסקים" in every case. The words now follow the disks the rows are about: one drawer -- or a
row with no drawer, the one disk of a single restore -- is "הדיסק"; two drawers or more are
"הדיסקים". The real state parser reads the question and the real draw_ask_dialog draws it;
the test reads every string it draws (text_make, wrapped at link time) -- so it reads the
code before the fix the same way.
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
    ("bash", BASH), ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "ui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
/* every string the dialog draws (-Wl,--wrap=text_make) */
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *s, int w, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *s, int w, Dir dir) {
    printf("text %s\n", s);
    return __real_text_make(cr, font, px, weight, s, w, dir);
}
int main(int argc, char **argv) {
    (void)argc;
    static App a; a.theme = &THEME_LIGHT;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2;
    state_parse(&a.st, f); fclose(f);
    printf("pending=%d\n", a.st.ask_pending);
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(sf); draw_ask_dialog(&a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(sf);
    return 0;
}
'''

BASE = "task=running\ntask_name=Win11\ntask_disk=/dev/sda\npct=-1\nmessage=x|y\n"


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("sharewords")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" '
                            '$(pkg-config --cflags pangocairo cairo libdrm) -o "$2" "$3" ${@:4} '
                            '-Wl,--wrap=text_make $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def words(driver: Path, tmp_path: Path, rows: str, n: int) -> list[str]:
    state = tmp_path / "state"
    state.write_text(BASE + f"ask_prompt=7-0|share|{n}|sda\n" + rows, encoding="utf-8", newline="\n")
    out = subprocess.run([str(driver), posix(state)], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def drawn(out: list[str], words: str) -> bool:
    """Some string the dialog drew holds <words> as whole words: "הדיסק" is not in "הדיסקים"."""
    def norm(s: str) -> str:
        return " " + " ".join("".join(" " if c in ",:.—" else c for c in s).split()) + " "
    return any(norm(words) in norm(line[5:]) for line in out if line.startswith("text "))


def test_one_drawer_is_the_disk(driver, tmp_path):
    out = words(driver, tmp_path, "ask_row=1|2|21474836480|166429982720\nask_row=1|3|32212254720|104152760320\n", 2)
    assert out[0] == "pending=1", out                                  # two partitions, one disk
    assert drawn(out, "הדיסק גדול מהמקור") and not drawn(out, "הדיסקים"), out
    assert drawn(out, "דלג אל תכתוב את הדיסק") and drawn(out, "בלי תשובה הדיסק לא ייכתב"), out


def test_two_drawers_are_the_disks(driver, tmp_path):
    out = words(driver, tmp_path, "ask_row=1|2|21474836480|166429982720\nask_row=2|2|21474836480|166429982720\n", 2)
    assert out[0] == "pending=1", out
    assert drawn(out, "הדיסקים גדולים מהמקור") and not drawn(out, "הדיסק גדול"), out
    assert drawn(out, "דלג אל תכתוב את הדיסקים") and drawn(out, "בלי תשובה הדיסקים לא ייכתבו"), out


def test_a_single_restore_without_a_drawer_is_the_disk(driver, tmp_path):
    out = words(driver, tmp_path, "ask_row=0|2|21474836480|166429982720\n", 1)
    assert out[0] == "pending=1" and not drawn(out, "הדיסקים"), out
    assert drawn(out, "דלג אל תכתוב את הדיסק") and drawn(out, "בלי תשובה הדיסק לא ייכתב"), out
