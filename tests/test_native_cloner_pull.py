"""16/10 — "שחזור אימג' מהשרת" on the cloner, the native screens.

* The boot choice has a third card (key 3, ``boot-pull``) under the two of the
  approved mockup, which keep their place; with one disk (``lc_local=0``) the
  local clone card is off -- key 2 and its click send nothing.
* ``lc_screen=pull`` routes to the build machine's restore screen
  (``SCREEN_RESTORE``), its target the drawers of ``cdisk=`` -- none ticked by
  itself (#1410), a disk with no slot never offered, drawer 1 on the right
  (RTL). The red button opens the #1270 dialog; only its OK sends
  ``lc-pull|<image>|<ports>``; Esc / "חזרה" sends ``lc-back``.
* Keyboard only works too (owner, 16/10): Tab / Down to the drawer row and
  back, Left / Right between drawers (RTL), Space ticks, A ticks the healthy
  ones; the digits stay with the image list; the focused drawer has the
  focus ring in both themes.

Driven through the real ``lc_handle`` (the restore screen's own keys and
clicks on the cloner are ``lc_pull.c``), like ``test_native_local_clone.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig, posix

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "widgets.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_rtl_block_make(cairo_t *, double, int, const char *, double);
Text __wrap_rtl_block_make(cairo_t *cr, double px, int weight, const char *s, double w) {
    if (getenv("TRACE_RESTORE_TEXT")) printf("@ text %s\n", s);
    return __real_rtl_block_make(cr, px, weight, s, w);
}
static void draw_to(App *a, const char *png) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, 1280, 800); cairo_destroy(cr);
    if (png) cairo_surface_write_to_png(s, png);
    cairo_surface_destroy(s);
}
static void draw(App *a) { draw_to(a, NULL); }
static void load(App *a, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) { perror(path); exit(2); }
    state_parse(&a->st, f); fclose(f); lc_after_reload(a);
}
static int hit_id(const char *n) {
    static const struct { const char *n; int id; } T[] = { {"BOOT_SERVER", HIT_LC_BOOT_SERVER}, {"BOOT_LOCAL", HIT_LC_BOOT_LOCAL},
        {"BOOT_PULL", HIT_LC_BOOT_PULL}, {"START", HIT_RESTORE_START}, {"OK", HIT_CONFIRM_OK},
        {"CANCEL", HIT_CONFIRM_CANCEL}, {"BACK", HIT_BACK} };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) if (!strcmp(T[i].n, n)) return T[i].id;
    int p; if (sscanf(n, "PULL%d", &p) == 1) return HIT_PULL_DRAWER_BASE + p;
    return -1;
}
static void hits(App *a) {   /* the cloner's own targets, as drawn: id x y w h */
    for (int h = 0; h < a->nhits; h++)
        if ((a->hits[h].id >= HIT_PULL_DRAWER_BASE && a->hits[h].id <= HIT_PULL_DRAWER_BASE + MAX_DRAWERS) ||
            a->hits[h].id == HIT_LC_BOOT_SERVER || a->hits[h].id == HIT_LC_BOOT_LOCAL || a->hits[h].id == HIT_LC_BOOT_PULL)
            printf("@ hit %d %.0f %.0f %.0f %.0f\n", a->hits[h].id, a->hits[h].r.x, a->hits[h].r.y, a->hits[h].r.w, a->hits[h].r.h);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    printf("@ ids %d %d %d %d\n", HIT_LC_BOOT_SERVER, HIT_LC_BOOT_LOCAL, HIT_LC_BOOT_PULL, HIT_PULL_DRAWER_BASE);
    load(&a, argv[1]); app_route(&a); draw(&a);
    for (int i = 2; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "state:", 6)) { load(&a, v + 6); app_route(&a); draw(&a); printf("@ reload\n"); continue; }
        if (!strcmp(v, "hits")) { hits(&a); continue; }
        if (!strcmp(v, "station")) {
            a.force_cloner = 0; a.signed_in = 1; a.mode = MODE_RESTORE;
            app_route(&a); draw(&a); continue;
        }
        if (!strcmp(v, "dark")) { a.theme = &THEME_DARK; draw(&a); continue; }
        if (!strncmp(v, "png:", 4)) {
            draw_to(&a, v + 4);
            printf("@ indigo %d %d %d\n", (int)(a.theme->indigo.r * 255 + .5), (int)(a.theme->indigo.g * 255 + .5), (int)(a.theme->indigo.b * 255 + .5));
            continue;
        }
        if (!strncmp(v, "img:", 4)) { a.restore_image_sel = atoi(v + 4); draw(&a); continue; }
        if (!strncmp(v, "key:", 4)) {
            const char *k = v + 4; e.type = UIEV_KEY;
            e.key = !strcmp(k, "LEFT") ? KEYSYM_LEFT : !strcmp(k, "RIGHT") ? KEYSYM_RIGHT : !strcmp(k, "UP") ? KEYSYM_UP
                  : !strcmp(k, "DOWN") ? KEYSYM_DOWN : !strcmp(k, "TAB") ? KEYSYM_TAB : !strcmp(k, "ENTER") ? KEYSYM_ENTER : KEYSYM_ESC;
        } else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; }
        else if (!strncmp(v, "hit:", 4)) {
            int id = hit_id(v + 4), found = 0;
            for (int h = a.nhits - 1; h >= 0 && !found; h--) if (a.hits[h].id == id) {
                e.type = UIEV_CLICK; e.x = a.hits[h].r.x + a.hits[h].r.w / 2; e.y = a.hits[h].r.y + a.hits[h].r.h / 2; found = 1; }
            if (!found) { printf("@ nohit %s\n", v + 4); continue; }
        }
        app_route(&a); lc_handle(&a, &e); app_route(&a); draw(&a);
        printf("@ screen=%d focus=%d pf=%d confirm=%d sel=", a.screen, a.lcu.boot_focus, a.pull_focus, a.confirm);
        for (int p = 1; p <= MAX_DRAWERS; p++) if (a.pull_sel[p]) printf("%d,", p);
        printf(" err=%s\n", a.st.form_error); fflush(stdout);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("pulldrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    assert "src/lc_pull.c" in srcs, srcs
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=rtl_block_make -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def drive(driver: Path, tmp_path: Path, state: str, *events: str) -> list[str]:
    path = tmp_path / "state.txt"
    path.write_text(state, encoding="utf-8", newline="\n")
    extra = []
    for ev in events:
        if ev.startswith("state="):
            p = tmp_path / f"s{len(extra)}.txt"; p.write_text(ev[6:], encoding="utf-8", newline="\n"); ev = f"state:{posix(p)}"
        extra.append(ev)
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), posix(path), *extra], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return [l for l in run.stdout.splitlines() if l]


def records(lines: list[str]) -> list[str]:
    return [l for l in lines if l.startswith(("boot-", "lc-"))]


def ids(lines: list[str]) -> dict[str, int]:
    """The ui.h values, as the driver was built with them."""
    v = next(l for l in lines if l.startswith("@ ids ")).split()[2:]
    return dict(zip(("server", "local", "pull", "base"), map(int, v)))


def hits(lines: list[str]) -> dict[int, tuple[float, float, float, float]]:
    out = {}
    for l in lines:
        if l.startswith("@ hit "):
            i, x, y, w, h = l.split()[2:]
            out[int(i)] = (float(x), float(y), float(w), float(h))
    return out


BOOT2 = "lc_screen=boot\nlc_boot=2000000000|0|1999999990\nlc_local=1\n"
BOOT1 = "lc_screen=boot\nlc_boot=2000000000|0|1999999990\nlc_local=0\n"
#: the restore screen: two images, three drawers and a disk with no slot (port 0)
PULL = ("lc_screen=pull\nmachine_name=CLONER-02\n"
        "image=img_7f3a91|Office 2024|Office\nimage=img_2c8e04|CAD|Office\n"
        "cdisk=1|sdc|Crucial MX500|480103981056|passed||6.0|6.0|\n"
        "cdisk=2|sda|Samsung SSD 870|500107862016|fail|health_failed|6.0|6.0|\n"
        "cdisk=3|sdb|WDC|500107862016|failed_last|disk|6.0|6.0|\n"
        "cdisk=0|nvme0n1|NVMe|512110190592|passed||||\n")
SCREEN_RESTORE = 10   # ui.h Screen


@pytest.mark.parametrize(("record", "expected"), [
    ("", "טוען את רשימת האימג'ים…"),
    ("images_known=0", "טוען את רשימת האימג'ים…"),
    ("images_known=1", "אין אימג'ים זמינים לשחזור."),
    ("images_known=-1", "לא הצלחנו לקרוא את רשימת האימג'ים."),
])
@pytest.mark.parametrize("station", [True, False])
def test_restore_image_list_message(driver, tmp_path, monkeypatch, record, expected, station):
    monkeypatch.setenv("TRACE_RESTORE_TEXT", "1")
    state = "" if station else "lc_screen=pull\n"
    lines = drive(driver, tmp_path, state + record + "\n", "station" if station else "hits")
    assert f"@ text {expected}" in lines
    if record != "images_known=1":
        assert "@ text אין אימג'ים זמינים לשחזור." not in lines


# --- the boot choice ----------------------------------------------------------------------

def test_three_is_the_restore_from_the_server(driver, tmp_path):
    assert records(drive(driver, tmp_path, BOOT2, "char:3")) == ["boot-pause", "boot-pull"]
    assert records(drive(driver, tmp_path, BOOT2, "hit:BOOT_PULL")) == ["boot-pause", "boot-pull"]
    assert records(drive(driver, tmp_path, BOOT2, "key:DOWN", "key:ENTER")) == ["boot-pause", "boot-pull"]
    # the two of the mockup are unchanged: Enter is the server, 2 the local clone
    assert records(drive(driver, tmp_path, BOOT2, "key:ENTER")) == ["boot-pause", "boot-server"]
    assert records(drive(driver, tmp_path, BOOT2, "char:2")) == ["boot-pause", "boot-local"]


def test_the_third_card_is_under_the_two_across_the_width(driver, tmp_path):
    out = drive(driver, tmp_path, BOOT2, "hits")
    h, i = hits(out), ids(out)
    server, local, pull = h[i["server"]], h[i["local"]], h[i["pull"]]
    assert server[1] == local[1] and pull[1] > server[1] + server[3], (server, pull)   # a row below
    assert pull[0] == local[0] and pull[2] > server[2] + local[2], (local, pull)      # both columns wide
    assert server[0] > local[0]                                                     # RTL: the server on the right


def test_with_one_disk_the_local_card_is_off(driver, tmp_path):
    out = drive(driver, tmp_path, BOOT1, "hits", "char:2", "hit:BOOT_LOCAL", "key:TAB", "key:ENTER")
    assert ids(out)["local"] not in hits(out) and ids(out)["pull"] in hits(out), out
    assert "@ nohit BOOT_LOCAL" in out and "boot-local" not in records(out), out
    assert records(out) == ["boot-pause", "boot-pull"], out          # Tab skips the card that is off


# --- the restore screen on the cloner --------------------------------------------------------

def test_the_pull_screen_is_the_build_restore_screen_with_the_drawers(driver, tmp_path):
    out = drive(driver, tmp_path, PULL, "hits", "hit:PULL0")
    h, base = hits(out), ids(out)["base"]
    drawers = sorted(i - base for i in h if i > base)
    # #1732 (owner 18/10): drawer 2 is red (SMART fail) -- drawn, dimmed, never a target: no hit
    assert drawers == [1, 3], drawers                                    # the disk with no slot is not offered
    assert "@ nohit PULL0" in out
    assert h[base + 1][0] > h[base + 3][0], h                              # RTL: drawer 1 on the right


def test_nothing_is_ticked_and_the_red_button_needs_an_image_and_a_drawer(driver, tmp_path):
    # "מחק ושחזר…" is drawn disabled -- with no hit -- until it can be used (the
    # build screen's own rule), so the attempts before that go through Enter.
    out = drive(driver, tmp_path, PULL, "hit:START", "key:ENTER", "img:1", "hit:START", "key:ENTER",
                "hit:PULL1", "hit:PULL3", "hit:START")
    assert out.count("@ nohit START") == 2, out                        # no image, then no drawer
    states = [l for l in out if l.startswith("@ screen")]
    assert len(states) == 5, states
    assert f"screen={SCREEN_RESTORE}" in states[0] and "sel= " in states[0], states   # nothing by itself
    assert "confirm=0" in states[0] and "בחרו אימג'" in states[0]
    assert "confirm=0" in states[1] and "sel= " in states[1] and "סמנו לפחות מגירה" in states[1]
    assert "confirm=1" in states[-1] and "sel=1,3," in states[-1], states
    assert records(out) == [], out                                        # the dialog asks first


def test_only_the_dialogs_ok_sends_the_drawers(driver, tmp_path):
    ok = drive(driver, tmp_path, PULL, "img:1", "hit:PULL3", "hit:PULL1", "hit:PULL2", "hit:PULL2", "hit:START", "hit:OK")
    assert records(ok) == ["lc-pull|img_7f3a91|1,3"], ok
    assert "@ nohit PULL2" in ok, ok                                      # #1732: the red drawer takes no click
    enter = drive(driver, tmp_path, PULL, "img:2", "hit:PULL3", "key:ENTER", "key:ENTER")
    assert records(enter) == ["lc-pull|img_2c8e04|3"], enter
    cancel = drive(driver, tmp_path, PULL, "img:1", "hit:PULL1", "hit:START", "key:ESC", "hit:START", "hit:CANCEL")
    assert records(cancel) == [], cancel


def test_a_drawer_that_is_gone_is_no_longer_ticked(driver, tmp_path):
    gone = PULL.replace("cdisk=3|sdb|WDC|500107862016|failed_last|disk|6.0|6.0|\n", "")
    out = drive(driver, tmp_path, PULL, "img:1", "hit:PULL1", "hit:PULL3", "state=" + gone, "hit:START", "hit:OK")
    assert records(out) == ["lc-pull|img_7f3a91|1"], out


def test_back_and_esc_return_to_the_choice(driver, tmp_path):
    assert records(drive(driver, tmp_path, PULL, "hit:BACK")) == ["lc-back"]
    assert records(drive(driver, tmp_path, PULL, "hit:PULL1", "key:ESC")) == ["lc-back"]


def test_the_png_cards_route_to_their_screens(tmp_path):
    """--png renders the new cards and checks each one's route (render_png)."""
    binary = _build_gui(tmp_path)
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "p"), "--size", "1280x800"],
                         capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    for card in ("lcboot-one", "lcboot-pull", "lcpull", "lcpull-confirm", "lcpull-keys"):
        for theme in ("light", "dark"):
            assert (tmp_path / f"p-{card}-{theme}.png").exists(), (card, theme)


# --- the keyboard (owner, 16/10: mouse only, keyboard only, or both) -----------------------
#
# Negative control that still compiles (Testrunner, from the repo root):
#   cp native-gui/src/lc_pull.c /tmp/lc_pull.c.keep
#   sed -i "/the drawer row's keys (owner 16\/10)/,/end of the drawer row's keys/d" native-gui/src/lc_pull.c
#   grep -c "KEYSYM_LEFT\|e->ch == ' '" native-gui/src/lc_pull.c        # must print 0
#   python3 -m pytest tests/test_native_cloner_pull.py -q -k "keyboard or row_ends or ticks_only or enter_opens or digits_stay or visible_ring"
#   cp /tmp/lc_pull.c.keep native-gui/src/lc_pull.c

def states(lines: list[str]) -> list[str]:
    return [l for l in lines if l.startswith("@ screen")]


def test_keyboard_only_ticks_drawers_and_sends_them(driver, tmp_path):
    out = drive(driver, tmp_path, PULL, "img:1", "key:TAB", "char: ", "key:LEFT", "key:LEFT", "char: ",
                "key:ENTER", "key:ENTER")
    st = states(out)
    assert "pf=1" in st[0] and "pf=1" in st[1] and "sel=1," in st[1], st      # Tab: the first drawer; Space ticks it
    # Left = the next disk number (RTL); #1732: the red drawer 2 is skipped, and 3 is the row's end
    assert "pf=3" in st[2] and "pf=3" in st[3], st
    assert "sel=1,3," in st[4] and "confirm=1" in st[5], st
    assert records(out) == ["lc-pull|img_7f3a91|1,3"], out


def test_the_row_ends_stop_and_space_untoggles(driver, tmp_path):
    out = drive(driver, tmp_path, PULL, "key:DOWN", "key:RIGHT", "char: ", "char: ", "key:UP")
    st = states(out)
    assert "pf=1" in st[1], st                          # nothing to the right of disk 1
    assert "sel=1," in st[2] and "sel= " in st[3], st
    assert "pf=0" in st[4], st                          # Up: back to the image list


def test_a_ticks_only_the_healthy_drawers(driver, tmp_path):
    """PULL: disk 1 green, disk 2 red (SMART fail), disk 3 orange (failed before) -- smart_level."""
    st = states(drive(driver, tmp_path, PULL, "char:a"))
    assert "sel=1, " in st[0], st
    st = states(drive(driver, tmp_path, PULL, "char:A", "hit:PULL3"))
    assert "sel=1,3, " in st[1], st                     # an orange one is still the person's to tick
    out = drive(driver, tmp_path, PULL, "char:A", "hit:PULL2")
    assert "@ nohit PULL2" in out and "sel=1, " in states(out)[-1], out   # #1732: a red one never is


def test_enter_opens_the_dialog_only_with_a_drawer(driver, tmp_path):
    st = states(drive(driver, tmp_path, PULL, "img:1", "key:ENTER", "key:TAB", "char: ", "key:ENTER"))
    assert "confirm=0" in st[0] and "סמנו לפחות מגירה" in st[0], st
    assert "confirm=1" in st[-1], st


def test_the_digits_stay_with_the_images_in_the_drawer_row(driver, tmp_path):
    """lc_pull.c does not take the digits: they reach main.c's folder/image pick."""
    st = states(drive(driver, tmp_path, PULL, "key:TAB", "char:2", "char:3"))
    assert all("pf=1" in l and "sel= " in l for l in st[1:]), st


def _ring(png: Path, rect, rgb) -> int:
    from test_cloner_gui import _png_rgb
    w, _h, ch, px = _png_rgb(png)
    x, y, rw, rh = (int(v) for v in rect)
    n = 0
    for yy in range(y - 4, y + rh + 4):
        for xx in (x - 3, x - 2, x + rw + 1, x + rw + 2):
            i = (yy * w + xx) * ch
            if all(abs(px[i + k] - rgb[k]) <= 30 for k in range(3)):
                n += 1
    return n


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_focused_drawer_has_a_visible_ring(driver, tmp_path, theme):
    extra = ["dark"] if theme == "dark" else []
    png_on, png_off = tmp_path / "on.png", tmp_path / "off.png"
    out = drive(driver, tmp_path, PULL, *extra, "key:TAB", "key:LEFT", "key:LEFT", "hits", f"png:{posix(png_on)}",
                "key:TAB", f"png:{posix(png_off)}")
    base = ids(out)["base"]
    cell = hits(out)[base + 3]
    rgb = tuple(int(v) for v in next(l for l in out if l.startswith("@ indigo")).split()[2:])
    on, off = _ring(png_on, cell, rgb), _ring(png_off, cell, rgb)
    assert on > 2 * cell[3] and off < on / 4, (on, off, cell, rgb)
