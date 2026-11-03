"""חיצי הגלילה ▲▼ במתקין מצוירים כריבוע (#2332).

ב-ISO החי נארזת משפחת גופנים אחת — IBM Plex (‏`native-gui/fonts.conf`,
‏`fonts-ibm-plex`) — ובצילומי rc1 של 24/10 (‏esxi-12, ‏hv-11b) החצים בתיבת
"פרטים טכניים" של מסך הכשל צוירו כריבוע ריק. אותו תו היה גם בכפתור
"פרטים טכניים ▲/▼" של מסך ההתקדמות.

התיקון מצייר את החץ כמשולש cairo, ולא כתו. שני הטסטים כאן בודקים את זה
דרך הקוד האמיתי של `screens_install_final.c`:

* אף מחרוזת שהמסך פורש (‏`text_make`, עטוף בזמן הקישור) אינה מכילה ▲ או ▼ —
  כלומר שום דבר אינו תלוי בכך שהגליף קיים בגופן;
* ובמקום החץ יש צורה של חץ: שורות הדיו מתרחבות כלפי מטה בחץ למעלה,
  ומצטמצמות בחץ למטה. ריבוע (גליף חסר) רחב באותה מידה למעלה ולמטה.

בונים את הגואי (C, ‏pango/cairo) — לינוקס בלבד.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui"
ARROWS = ("▲", "▼")   # ▲ ▼


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("make", shutil.which("make")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native installer is built and rendered on Linux",
)

# The driver includes the screen's file (output_box / details_toggle are static)
# and links the rest of the GUI. text_make is wrapped at link time, so every
# string the screen lays out is reported; the drawn pixels are reported as an
# ink mask ('#' = differs from the background, '.' = background).
DRIVER = r'''
#include "%(final_c)s"
#include <stdint.h>
#include <stdlib.h>
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
static int log_makes;
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (log_makes) printf("make\t%%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
static void mask(cairo_surface_t *sf, int x0, int y0, int w, int h, int bx, int by) {
    cairo_surface_flush(sf);
    unsigned char *d = cairo_image_surface_get_data(sf); int st = cairo_image_surface_get_stride(sf);
    uint32_t bg = *(uint32_t *)(d + by * st + bx * 4);
    for (int y = y0; y < y0 + h; y++) {
        char line[512]; int n = 0;
        for (int x = x0; x < x0 + w && n < 510; x++) {
            uint32_t p = *(uint32_t *)(d + y * st + x * 4); int diff = 0;
            for (int k = 0; k < 24; k += 8) { int e = abs((int)((p >> k) & 255) - (int)((bg >> k) & 255)); if (e > diff) diff = e; }
            line[n++] = diff > 48 ? '#' : '.';
        }
        line[n] = 0; printf("mask\t%%s\n", line);
    }
}
static App app;
int main(int argc, char **argv) {
    InstallState *s = &app.install; app.theme = &THEME_LIGHT;
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 800, 300);
    cairo_t *cr = cairo_create(sf); cairo_set_source_rgb(cr, 1, 1, 1); cairo_paint(cr);
    if (argc == 2 && !strcmp(argv[1], "box")) {
        snprintf(s->output, sizeof s->output, "first line\nsecond line\nthird line");
        log_makes = 1; output_box(&app, cr, (Rect){ 0, 0, 760, 250 }); log_makes = 0;
        mask(sf, 726, 6, 27, 238, 740, 125);   /* the arrow column, inside the border */
    } else if (argc == 3 && !strcmp(argv[1], "toggle")) {
        s->technical_open = atoi(argv[2]);
        log_makes = 1; details_toggle(&app, cr, (Rect){ 40, 10, 210, 32 }); log_makes = 0;
        mask(sf, 0, 0, 300, 60, 0, 0);
    } else return 2;
    cairo_destroy(cr); cairo_surface_destroy(sf); return 0;
}
'''


def _driver(tmp_path: Path) -> Path:
    source = tmp_path / "arrows_driver.c"
    source.write_text(DRIVER % {"final_c": (GUI / "src" / "screens_install_final.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "arrows-driver"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_final.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -Wl,--wrap=text_make -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(source)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _run(binary: Path, *args: str) -> tuple[list[str], list[str]]:
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")   # the ISO's fonts, as on the live installer
    run = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, (run.returncode, run.stderr)
    lines = run.stdout.splitlines()
    makes = [line.split("\t", 1)[1] for line in lines if line.startswith("make\t")]
    rows = [line.split("\t", 1)[1] for line in lines if line.startswith("mask\t")]
    assert rows, "the driver drew no mask -- the test proves nothing"
    return makes, rows


def _pointing(rows: list[str]) -> str:
    """"up" / "down" by the width of the first and last ink rows; "box" when
    they are the same width (an empty-box glyph), "none" when there is no ink."""
    widths = [row.count("#") for row in rows if "#" in row]
    if not widths:
        return "none"
    if widths[0] + 3 <= widths[-1]:
        return "up"
    if widths[-1] + 3 <= widths[0]:
        return "down"
    return f"box {widths}"


def _leftmost_cluster(rows: list[str]) -> list[str]:
    """The columns of the leftmost run of ink (a gap of 4 empty columns ends
    it) -- in RTL that is what follows the words, where the arrow is."""
    inked = [x for x in range(len(rows[0])) if any(row[x] == "#" for row in rows)]
    assert inked, "nothing drawn"
    end = inked[0]
    for x in inked[1:]:
        if x - end > 4:
            break
        end = x
    return [row[inked[0]:end + 1] for row in rows]


@NATIVE
def test_the_output_box_scroll_arrows_are_drawn_not_typed(tmp_path) -> None:
    binary = _driver(tmp_path)
    makes, rows = _run(binary, "box")
    assert "first line" in makes, f"the box laid out no output -- the wrap is not in place: {makes}"
    typed = [m for m in makes if any(a in m for a in ARROWS)]
    assert typed == [], f"an arrow is still a character, and the ISO's font draws it as a box: {typed}"
    half = len(rows) // 2
    assert _pointing(rows[:half]) == "up", "\n".join(rows[:half])
    assert _pointing(rows[half:]) == "down", "\n".join(rows[half:])


@NATIVE
def test_the_technical_details_toggle_arrow_is_drawn_not_typed(tmp_path) -> None:
    binary = _driver(tmp_path)
    for is_open, way in (("1", "up"), ("0", "down")):
        makes, rows = _run(binary, "toggle", is_open)
        assert "פרטים טכניים" in makes, f"open={is_open}: the label was not laid out alone: {makes}"
        typed = [m for m in makes if any(a in m for a in ARROWS)]
        assert typed == [], f"open={is_open}: the arrow is still a character: {typed}"
        arrow = _leftmost_cluster(rows)
        assert len(arrow[0]) <= 12, f"open={is_open}: the leftmost ink is {len(arrow[0])}px wide, not an arrow"
        assert _pointing(arrow) == way, f"open={is_open}\n" + "\n".join(r for r in arrow if "#" in r)
