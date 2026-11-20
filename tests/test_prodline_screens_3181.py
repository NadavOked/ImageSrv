"""#3181 -- the production line on the cloner's own screen (the native GUI), Nadav 14/11.

A production line is a regular round that asks for more disks than the drawers. On the cloner's
screen (template 1, "בזמן שיכפול") three things are its own, from the approved mockup
(production-line-mockup-1114, c1):

1. "אפשר להוציא" on a drawer the server counted removable -- after a safe detach. The row is the
   green of a done row (full colour: only "אפשר להוציא" and "הושלם"), the tag the "SMART תקין" size;
2. "הכנס דיסק" on an empty drawer, with the name the next disk takes -- no colour, a grey LED;
3. the counter, "נכתבו X מתוך Y".

The server says them (``production_line`` in the progress reply and the hello, PR #3282). A regular
round has no ``production_line``: its screen does not change at all.

Measured on the real sources: the agent's ``gui_prodline_records`` (prodlinegui.sh) through
``cloner_gui_state``, and the real parser, ``screen_cloner`` through ``app_draw`` and ``lc_bay_draw``
-- the rows it drew, the texts it made and the pixels.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_cloner_gui import AGENT, BASH, GUI, PORTS, PRELUDE, _pkgconfig, _png_rgb, env, posix, sh, target

G = 1_000_000_000
LINE = {"written": 14, "target": 30, "remaining": 16, "next_name": "LAB-20",
        "names": {"S3TWNE0JB04745": "LAB-14"}, "insert": {"3": "LAB-20"}, "removable": [2], "refused": []}


# --- the agent: production_line becomes three kinds of records -----------------------------

def _records(tmp_path: Path, hello_line, reply: dict | None | str) -> list[str]:
    run = tmp_path / "run"; run.mkdir(parents=True); gui = tmp_path / "gui"; gui.mkdir()   # tmp_path / "b": a second room in the same test
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text(json.dumps({"schema": 1, "session": {"production_line": hello_line}}), newline="\n")
    if reply is not None:
        (run / "progress.reply").write_text(reply if isinstance(reply, str) else json.dumps(reply), newline="\n")
    target(run, "sda", "writing", 100, 40, "5")
    target(run, "sdb", "done", 100, 100, "")
    # the kiosk's room (guibridge.sh): cloner_gui_state also calls fw_warn_record (fwimage.sh, #2080) and
    # stream_beep_tick/stream_net_record (roundbeep.sh, #2067). PRELUDE does not load them, and the check
    # below ("not found") is the first in the file to read stderr -- so the room loads what the kiosk loads
    out = sh(env(run, gui) + PRELUDE + agent_sources("fwimage", "roundbeep", "prodlinegui") + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    return [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("pline")]


@requires_native("jq", why="cloner_gui_state reads the server's answers with jq")
def test_the_progress_reply_gives_the_three_records(tmp_path):
    reply = {"ok": True, "production_line": LINE}
    got = _records(tmp_path, dict(LINE, written=9, insert={}, removable=[]), reply)
    # the reply is the newer word: 14 written, not the hello's 9; drawer 3 by the convention (no mapping file)
    assert got == ["pline=14|30|16|1", "pline_ready=2", "pline_insert=3|3|LAB-20"], got


@requires_native("jq", why="cloner_gui_state reads the server's answers with jq")
def test_before_the_first_reply_the_hello_speaks(tmp_path):
    assert _records(tmp_path, LINE, None) == ["pline=14|30|16|1", "pline_ready=2", "pline_insert=3|3|LAB-20"]
    # a reply without the field (the server's production line never fails a report) -- the hello's word again
    assert _records(tmp_path / "b", LINE, {"ok": True}) == ["pline=14|30|16|1", "pline_ready=2", "pline_insert=3|3|LAB-20"]


@requires_native("jq", why="cloner_gui_state reads the server's answers with jq")
@pytest.mark.parametrize("hello_line", [None, "not-an-object", 7])
def test_a_regular_round_writes_nothing_whatever_a_reply_says(tmp_path, hello_line):
    """The hello decides: a round that is not a production line has no record at all -- even
    with an older reply that still carries one."""
    assert _records(tmp_path, hello_line, {"ok": True, "production_line": LINE}) == []


@requires_native("jq", why="cloner_gui_state reads the server's answers with jq")
def test_what_is_not_in_shape_is_not_said(tmp_path):
    bad = {"written": -1, "target": 30, "remaining": 16, "next_name": None, "names": {},
           "insert": {"x": "A", "0": "B", "4": "", "5": 7, "2": "LAB|7\nX"}, "removable": ["2", 0, 1.5, 3]}
    got = _records(tmp_path, bad, None)
    assert got == ["pline_ready=3", "pline_insert=2|2|LAB 7 X"], got   # no counter; a '|' or a break in a name is a space
    assert _records(tmp_path / "b", dict(LINE, names={}, next_name=None), "{not json")[0] == "pline=14|30|16|0"   # no names


def test_the_kiosk_and_the_agent_load_it_and_the_state_calls_it():
    bridge = (AGENT / "lib/gui/guibridge.sh").read_text(encoding="utf-8")
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    cloner = (AGENT / "lib/gui/clonergui.sh").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/prodlinegui.sh"' in bridge and '. "$LIB_DIR/prodlinegui.sh"' in main
    assert 'gui_prodline_records >> "$_cg_next"' in cloner


# --- the screen ----------------------------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lcui.h"
#include "state.h"
#include "screens_rounds.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
void __real_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g);
void __wrap_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g) {
    printf("@ bay %d|%.0f|%.0f|%.0f|%.0f|%s|%s|%s|%s\n", g->drawer, b.x, b.y, b.w, b.h, g->name, g->pill ? g->pill : "", g->pct, g->line);
    __real_lc_bay_draw(a, cr, b, g);
}
/* #3870: the screen's clock is the state's `updated`, as the --png clock is in screens_brain_ask.c. On the real clock, a draw
 * that began in the second after its state was written said "updated 1 second ago" where the one before said 0, and two
 * draws of one state differed by that word alone (1120-052838-argus-r44d2-bt1, testrunner2). */
static long long fixed_now;
time_t __real_time(time_t *t);
time_t __wrap_time(time_t *t) {
    if (!fixed_now) return __real_time(t);
    if (t) *t = (time_t)fixed_now;
    return (time_t)fixed_now;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    a.fixed_clock = 1;   /* the header clock is a constant, as in --png (main.c): this file diffs several draws of one state */
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    fixed_now = a.st.updated;
    a.force_cloner = 1; app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800);
    const Theme *t = a.theme;   /* the theme's colours, as cairo writes them: the bottom row, 10px each */
    Rgb sw[] = { t->led_ok, t->led_idle, t->surface, t->hair, t->success_ink, t->ink };
    for (int i = 0; i < 6; i++) { draw_set(cr, sw[i]); cairo_rectangle(cr, i * 10, 795, 10, 5); cairo_fill(cr); }
    cairo_destroy(cr); cairo_surface_flush(s);
    int rc = cairo_surface_write_to_png(s, argv[2]) == CAIRO_STATUS_SUCCESS ? 0 : 4;
    cairo_surface_destroy(s);
    return rc;
}
'''

native = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("prodline3181")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    assert "src/screens_cloner.c" in srcs and "src/lcbay.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -Wl,--wrap=lc_bay_draw -Wl,--wrap=time -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _state(extra: str = "", third: str = "") -> str:
    """Cloner 2, three drawers: 1 writing LAB-15 at 62%, 2 done (its disk detached), and what `third` says of 3."""
    now = int(time.time())
    return ("cloner=Office 2024\nmachine_name=2\nscreen=cloner\n"
            f"drawer=1|sda|writing|{int(0.62 * 22.2 * G)}|{int(22.2 * G)}||67000000|420|1|SATA 0\n"
            f"drawer=2|sdb|done|{int(22.2 * G)}|{int(22.2 * G)}||-1|-1|2|SATA 1\n"
            f"{third}drawer_moved=sda|{now}\n{extra}updated={now}\n")


def _draw(driver: Path, tmp_path: Path, name: str, state: str):
    src = tmp_path / f"{name}.txt"
    src.write_text(state, encoding="utf-8", newline="\n")
    png = tmp_path / f"{name}.png"
    r = subprocess.run([str(driver), posix(src), posix(png)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")), stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    bays = {}
    for l in r.stdout.splitlines():
        if l.startswith("@ bay "):
            f = l[len("@ bay "):].split("|", 8)
            bays[int(f[0])] = {"rect": tuple(float(v) for v in f[1:5]), "name": f[5], "pill": f[6], "pct": f[7], "line": f[8]}
    texts = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    return bays, texts, png


PL = "pline=14|30|16|1\npline_ready=2\npline_insert=3|3|LAB-20\n"


def _px(image, x, y):
    width, _h, ch, px = image
    p = (int(y) * width + int(x)) * ch
    return tuple(px[p:p + 3])


@native
def test_a_production_line_says_its_three_things(driver, tmp_path):
    bays, texts, png = _draw(driver, tmp_path, "line", _state(PL))
    assert "נכתבו 14 מתוך 30 שמות · נשארו 16" in texts, texts     # 3. the counter, under the title
    assert "הכנס דיסק, חכה, הוצא" in texts, texts                   # the line's title (mockup c1)
    assert bays[2]["pill"] == "אפשר להוציא", bays[2]                # 1. the removable drawer
    assert bays[2]["line"].endswith(" · נותק בבטחה"), bays[2]
    assert bays[3]["name"] == "הכנס דיסק" and bays[3]["pct"] == "LAB-20", bays[3]   # 2. the empty drawer, its next name
    assert bays[3]["line"] == "ממתין לדיסק", bays[3]
    assert "הדיסק הבא יקבל את השם" in texts and "LAB-20" in texts, texts
    assert bays[1]["pill"] == "" and "כותב" in bays[1]["line"], bays[1]   # the writing drawer is as it was
    # the rows are in the drawers' order, one under the other
    assert bays[1]["rect"][1] < bays[2]["rect"][1] < bays[3]["rect"][1], bays


@native
def test_green_only_for_removable_and_no_colour_on_the_empty_drawer(driver, tmp_path):
    bays, _texts, png = _draw(driver, tmp_path, "colours", _state(PL))
    image = _png_rgb(png)
    ok, idle, surface, hair, success_ink, ink = (_px(image, i * 10 + 5, 797) for i in range(6))
    x, y, w, h = bays[2]["rect"]
    assert _px(image, x + 1, y + h / 2) == ok, "the removable row's frame is the done green"
    x, y, w, h = bays[3]["rect"]
    assert _px(image, x + 1, y + h / 2) not in (ok, idle), "the empty drawer is not framed in a colour"
    assert _px(image, x + w / 2, y + 4) == surface, "the empty drawer is not filled"
    # its LED, the mockup's grey (.lv.idle): the LED column is 48px wide at the row's start; the dot is 15px over the centre
    led = [_px(image, x + 24 + dx, y + h / 2 - 15 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    assert idle in led, led
    # the removable drawer's name in the row's green (.rdy), the writing drawer's in ink
    def has(colour, rect):
        x, y, w, h = rect
        return any(_px(image, xx, yy) == colour for xx in range(int(x + w - 96 - 230), int(x + w - 96)) for yy in range(int(y + 8), int(y + h / 2)))
    assert has(success_ink, bays[2]["rect"]), "the 'אפשר להוציא' row's name is green"
    assert not has(success_ink, bays[1]["rect"]), "the writing row has no green name"


@native
def test_a_slot_the_agent_writes_in_keeps_its_row(driver, tmp_path):
    """The server's word is two seconds old: a new disk in drawer 3 is the agent's, no "הכנס דיסק" over it."""
    third = f"drawer=3|sdc|writing|{int(0.05 * 22.2 * G)}|{int(22.2 * G)}||67000000|900|3|SATA 2\n"
    bays, texts, _png = _draw(driver, tmp_path, "busy", _state(PL, third))
    assert bays[3]["name"] != "הכנס דיסק" and "כותב" in bays[3]["line"], bays[3]
    assert "ממתין לדיסק" not in texts, texts
    # the disk that left drawer 3 (done) is the row the insert takes -- one row, not two
    third = f"drawer=3|sdc|done|{int(22.2 * G)}|{int(22.2 * G)}||-1|-1|3|SATA 2\n"
    bays, _texts, _png = _draw(driver, tmp_path, "left", _state(PL, third))
    assert bays[3]["name"] == "הכנס דיסק" and bays[3]["pct"] == "LAB-20", bays[3]


@native
def test_a_done_row_with_a_warning_keeps_its_tag(driver, tmp_path):
    """Removable, but its check did not come down: the line says it was detached, the tag stays its own."""
    bays, _texts, _png = _draw(driver, tmp_path, "warn", _state(PL + "drawer_rb=sdb|list_not_fetched\n"))
    assert bays[2]["pill"] != "אפשר להוציא" and bays[2]["line"].endswith(" · נותק בבטחה"), bays[2]


@native
def test_a_regular_round_is_the_screen_as_it_was(driver, tmp_path):
    """No pline= (a regular round, or a counter that is not in shape): the slots the records name change
    nothing -- not a pixel, not a word."""
    base_bays, base_texts, base_png = _draw(driver, tmp_path, "regular", _state())
    assert not [t for t in base_texts if t.endswith(" UTC")], base_texts   # no wall clock: a minute turning between draws is not a diff
    assert "הכנס דיסק" not in base_texts and "אפשר להוציא" not in base_texts and not [t for t in base_texts if "נכתבו" in t]
    for i, junk in enumerate(["pline_ready=2\npline_insert=3|3|LAB-20\n",
                              "pline=31|30|0|1\npline_ready=2\npline_insert=3|3|LAB-20\n",
                              "pline=14|30|16\npline_ready=2\n"]):
        bays, texts, png = _draw(driver, tmp_path, f"junk{i}", _state(junk))
        assert texts == base_texts and bays == base_bays, junk
        assert _png_rgb(png) == _png_rgb(base_png), junk
