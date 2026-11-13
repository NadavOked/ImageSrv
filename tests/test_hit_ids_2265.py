"""#2265 — מזהה לחיצה פשוט שנופל בטווח של `*_BASE` בולע את הלחיצה.

המזהים הפשוטים ב-enum של `native-gui/src/ui.h` הגיעו ל-100, שהוא
`HIT_DISK_BASE`. `HIT_CDLG_OPEN` ("בטל דיסק…  X" במסך ההפצה של מחשב הבנייה)
קיבל 101, ובדיקת הטווח של הכוננים ב-`handle()` רצה לפני ה-`switch`: הלחיצה
נקראה כ"כונן 2" של מסך הקליטה (`chosen_disk=1`, `focus=HIT_NAME`), והחלון לא
נפתח. המקש X עבד.

שתי בדיקות, ושתיהן על מיפוי ה-hit ולא על צילום:

1. סטטית, על `ui.h` (ועל הבסיסים הקבועים של `install.h`): אף מזהה פשוט ב-enum
   אינו בתוך הבלוק של בסיס כלשהו — מהבסיס עד הבסיס הבא, ולכל היותר 100.
2. דרייבר שכולל את `main.c` האמיתי: מצייר את מסך ההפצה בזמן כתיבה, לוחץ במרכז
   המלבן שנרשם ל-`HIT_CDLG_OPEN`, ומדפיס את מצב ה-App אחרי `handle()`.

ועוד שומר אחד שעובר בשני המצבים: כשרשימה נפתחת פתוחה, לחיצה על הכפתור סוגרת
אותה (כך היה כשהמזהה היה מתחת ל-`HIT_OPTION_BASE`), ואינה פותחת את החלון מתחתיה.
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

SRC = GUI / "src"

native_gui = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)


# --- 1. הסטטית: המספור ------------------------------------------------------------


def _hit_enum() -> list[tuple[str, int]]:
    """The HIT_* enum of ui.h in declaration order, with the value C gives each."""
    src = (SRC / "ui.h").read_text(encoding="utf-8")
    start = src.index("HIT_NONE = 0")
    body = src[src.rindex("{", 0, start) + 1:src.index("};", start)]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    values: dict[str, int] = {}
    out: list[tuple[str, int]] = []
    v = -1
    for tok in (t.strip() for t in body.split(",")):
        if not tok:
            continue
        if "=" in tok:
            name, expr = (s.strip() for s in tok.split("=", 1))
            v = int(expr) if expr.isdigit() else values[expr]
        else:
            name, v = tok, v + 1
        assert re.fullmatch(r"HIT_[A-Z0-9_]+", name), tok
        values[name] = v
        out.append((name, v))
    return out


def _install_defines() -> dict[str, int]:
    src = (SRC / "install.h").read_text(encoding="utf-8")
    return {m[1]: int(m[2]) for m in re.finditer(r"#define\s+(HIT_\w+)\s+(\d+)", src)}


def test_the_enum_parser_reads_the_values_c_gives():
    """The parser is the evidence for the test below -- pin it to known values."""
    enum = dict(_hit_enum())
    assert enum["HIT_NONE"] == 0 and enum["HIT_THEME"] == 1
    assert enum["HIT_DISK_BASE"] == 100 and enum["HIT_IDENTIFY_BASE"] == 3700
    assert len(enum) > 120, len(enum)


def test_no_plain_hit_id_lands_in_a_base_range():
    enum = _hit_enum()
    bases = sorted({v for n, v in enum if n.endswith("_BASE")}
                   | {v for n, v in _install_defines().items() if n.endswith("_BASE")})
    # a base owns the ids up to the next base, at most 100 of them
    blocks = [(b, min(nxt, b + 100)) for b, nxt in zip(bases, bases[1:] + [bases[-1] + 100])]
    plain = [(n, v) for n, v in enum if not n.endswith("_BASE") and n != "HIT_NONE"]
    inside = [f"{n}={v} in [{lo},{hi})" for n, v in plain for lo, hi in blocks if lo <= v < hi]
    assert not inside, inside


def test_no_two_hit_ids_share_a_value():
    enum = _hit_enum()
    taken: dict[int, str] = {}
    clashes = []
    for n, v in enum + sorted(_install_defines().items(), key=lambda kv: kv[1]):
        if v in taken:
            clashes.append(f"{n}={v} == {taken[v]}")
        taken.setdefault(v, n)
    assert not clashes, clashes


# --- 2. הדרייבר: לחיצה אמיתית דרך handle() ----------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define main station_gui_main
#include "main.c"
#undef main
static void drv_draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1920, 1080);
    cairo_t *cr = cairo_create(s); app_route(a); app_draw(a, cr, 1920, 1080); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void drv_report(const char *tag, App *a) {
    printf("%s screen=%d cdlg_open=%d picker=%d chosen_disk=%d focus=%d dd_open=%d\n", tag,
           a->screen, a->cdlg.open, a->cdlg.picker, a->chosen_disk, a->focus, a->dd_open);
    fflush(stdout);
}
/* argv: STATE [dd] -- the distribution screen of STATE; "dd" opens the image
 * list first. Then one click in the middle of HIT_CDLG_OPEN's rectangle. */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    static App a; memset(&a, 0, sizeof a);
    a.theme = &THEME_LIGHT; a.default_theme = a.theme; a.focus = HIT_USER; a.menu_focus = -1; a.chosen_disk = -1;
    a.signed_in = 1; a.admin = 1; snprintf(a.signed_user, sizeof a.signed_user, "admin");
    snprintf(a.mac, sizeof a.mac, "3C:52:82:A1:00:1F");
    a.mode = MODE_ROOM;
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); after_reload(&a);
    drv_draw(&a);
    if (argc > 2 && !strcmp(argv[2], "dd")) a.dd_open = HIT_ROOM_IMAGE;
    drv_report("@ before", &a);
    Event e; memset(&e, 0, sizeof e);
    int found = 0;
    double s = a.scale > 0 ? a.scale : 1;   /* hits are logical, a click is in screen pixels */
    for (int h = a.nhits - 1; h >= 0 && !found; h--) if (a.hits[h].id == HIT_CDLG_OPEN) {
        e.type = UIEV_CLICK; e.x = (a.hits[h].r.x + a.hits[h].r.w / 2) * s; e.y = (a.hits[h].r.y + a.hits[h].r.h / 2) * s; found = 1; }
    if (!found) { printf("@ nohit\n"); return 0; }
    printf("@ click on_button=%d\n", app_hit(&a, e.x, e.y) == HIT_CDLG_OPEN);
    app_route(&a); handle(&a, &e, NULL, 1); drv_draw(&a);
    drv_report("@ after", &a);
    return 0;
}
'''

ROOM_WRITING = (
    "room_label=מחשבי שיכפול\n"
    "machine=1|3c:52:82:a1:00:21|1|1|0|writing|37|1|\nmachine_drawers=0|2\n"
    "room_drawer=0|1|S1|Samsung SSD 870 EVO|500107862016|passed|0|1||||ok||||37|writing|sda||0\n"
    "room_drawer=0|2|S2|WDC WD5000AAKX|500107862016|passed|0|1||||ok||||41|writing|sdb||0\n"
    "round=office365|1|0|0|4|0|4|192|68000000|300|running\n")


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("hit2265")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    assert srcs, "make print-gui-src returned nothing"
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _click(driver: Path, tmp_path: Path, *extra: str) -> dict[str, dict[str, int]]:
    state = tmp_path / "state.txt"
    state.write_text(ROOM_WRITING, encoding="utf-8", newline="\n")
    run = subprocess.run([str(driver), posix(state), *extra], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")),
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert "@ nohit" not in lines, f"no 'בטל דיסק…  X' on the distribution screen: {lines}"
    out = {}
    for parts in (line.split() for line in lines):   # "@ before k=v ...", "@ after k=v ..."
        if len(parts) > 2 and parts[0] == "@":
            out[parts[1]] = {k: int(v) for k, v in (p.split("=") for p in parts[2:])}
    assert set(out) == {"before", "click", "after"}, lines
    assert out["click"]["on_button"] == 1, lines   # the point clicked is the button's, not a neighbour's
    return out


@native_gui
def test_clicking_cancel_disk_x_opens_the_writers_window(driver, tmp_path):
    got = _click(driver, tmp_path)
    assert got["before"]["cdlg_open"] == 0
    # the click reaches HIT_CDLG_OPEN's case: the window with the choice among the writers
    assert (got["after"]["cdlg_open"], got["after"]["picker"]) == (1, 1), got
    # and it is not read as the capture screen's "disk 2"
    assert got["after"]["chosen_disk"] == -1, got
    assert got["after"]["focus"] == got["before"]["focus"], got


@native_gui
def test_with_a_list_open_the_click_only_closes_the_list(driver, tmp_path):
    """Passes before and after the fix: renumbering must not change this."""
    got = _click(driver, tmp_path, "dd")
    assert got["before"]["dd_open"] != 0
    assert got["after"]["dd_open"] == 0, got
    assert got["after"]["cdlg_open"] == 0, got
