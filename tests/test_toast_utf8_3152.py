"""#3152: the cloner GUI's toast shows the whole power-off refusal, not 160 bytes of it.

14/11, cloner MC2 in the VM lab, end of round C2: the toast "המחשב לא כובה: Wake-on-LAN לא נדרך
..." ended on "בכ" and a missing-glyph box. The sentence (agent/lib/core/idle.sh, power.fail ->
guistate.sh `toast=`) is 180 bytes of Hebrew; the `toast` buffer of the state was 160, so the
copy kept 159 bytes -- the last of them the first half of a two-byte letter.

The driver links the production sources of native-gui (everything but main.c), parses a state
file with the real `state_parse`, copies the toast the way main.c's `toast()` does, draws it with
the real `draw_toast`, and prints the text it handed to `text_make`. Runs on the VM lab (cc +
pango/cairo); on the dev workstation `requires_native` declares the skip.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix

REPO = Path(__file__).resolve().parent.parent
IDLE_SH = REPO / "agent" / "lib" / "core" / "idle.sh"

DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "widgets.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *, Font, double, int, const char *, int, Dir);
Text __wrap_text_make(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d) {
    printf("T %s\n", s);
    return __real_text_make(cr, f, px, w, s, wp, d);
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    static App a; a.theme = &THEME_LIGHT;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    printf("S %s\n", a.st.toast);
    /* main.c toast(): the state's toast goes into the app's own buffer */
    printf("SIZES %zu %zu\n", sizeof a.st.toast, sizeof a.toast);
    snprintf(a.toast, sizeof a.toast, "%s", a.st.toast);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s);
    draw_toast(&a, cr, 1280, 800);
    cairo_destroy(cr); cairo_surface_destroy(s);
    return 0;
}
'''


def _refusal_text() -> str:
    """The sentence idle.sh writes to power.fail when the Wake-on-LAN could not be armed."""
    text = IDLE_SH.read_text(encoding="utf-8")
    match = re.search(r'echo "(המחשב לא כובה: Wake-on-LAN[^"]+)" > "\$RUN_DIR/power\.fail"', text)
    assert match, "idle.sh: the Wake-on-LAN refusal sentence was not found"
    return match.group(1)


def test_the_refusal_is_longer_than_the_old_buffer():
    """Keeps the test honest: a sentence that fits 159 bytes proves nothing about the cut."""
    assert len(_refusal_text().encode("utf-8")) > 159


@requires_native(
    ("bash", BASH),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_the_toast_carries_and_draws_the_whole_wol_refusal(tmp_path):
    message = _refusal_text()
    driver = tmp_path / "driver.c"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    sources = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert sources.returncode == 0, sources.stderr
    srcs = [posix(GUI / s) for s in sources.stdout.split() if not s.endswith("main.c")]
    assert srcs
    binary = tmp_path / "driver"
    build = subprocess.run(
        [BASH, "-c",
         'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" '
         '$(pkg-config --cflags pangocairo cairo libdrm) '
         '-Wl,--wrap=text_make -o "$2" "$3" "${@:4}" '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary), posix(driver), *srcs],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr

    state = tmp_path / "state.txt"
    state.write_text(f"toast={message}\n", encoding="utf-8", newline="\n")
    run = subprocess.run([str(binary), posix(state)], capture_output=True, encoding="utf-8",
                         errors="replace", timeout=120, stdin=subprocess.DEVNULL,
                         env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    # the state keeps every byte, and a cut (if the sentence ever outgrows the buffer) is on a letter
    assert f"S {message}" in lines, [line for line in lines if line.startswith("S ")]
    # the app's buffer is not smaller than the state's: main.c toast() copies one into the other
    sizes = next(line for line in lines if line.startswith("SIZES ")).split()[1:]
    assert int(sizes[1]) >= int(sizes[0]), sizes
    # and what reaches the screen is the sentence, from its first word to its last
    assert f"T {message}" in lines, [line for line in lines if line.startswith("T ")]
