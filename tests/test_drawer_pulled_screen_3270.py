"""#3270 (metal 14/11, cloner 2): what the cloner's screen says about a pulled drawer.

Nadav pulled a disk mid-write and put it back 20 seconds later. The screen drew red "נכשל" (a verdict) for a
state of the process. A pull is grey: "נשלף · ממתין להחזרה", then "הוחזר · ממתין להצטרפות" -- with the dot and
the two lines beside the row, and a tag the size of "SMART תקין" (process states are grey, not red or orange).

Built over #3278 (the hero that does not move, the end that stays while the stream waits, the tag size): those
have their tests there (test_cloner_screen_steady_3270.py). Here only the pulled drawer, two layers:

* the agent (`stall.sh` drawer_gui_word, `clonergui.sh`): the word of a drawer's `drawer=` record -- `pulled` /
  `returned` for a pulled disk, `failed` for every real failure;
* the screen (`state.c`, `screens_cloner.c`, `lcbay.c`): the row's words, its tag and its dot.
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
from test_cloner_gui import (BASH, GUI, PORTS, PRELUDE, _pkgconfig, env, posix, sh, target)

G = 1_000_000_000


# --- the agent: the word of the drawer ------------------------------------------------------------------


def _word(tmp_path: Path, state: str, markers: tuple[str, ...], bound: str, present: dict[str, str],
          run_state: str | None = "writing") -> str:
    """drawer_gui_word for sda with <markers> in its target dir; <present> = {drawer: serial} of what is in the machine;
    <run_state> = the machine's own $RUN_DIR/state (#3209: pulled / returned only while it is `writing`; None = no file)."""
    run = tmp_path / "run"; t = run / "targets" / "sda"; t.mkdir(parents=True)
    if run_state is not None:
        (run / "state").write_text(run_state + "\n", newline="\n")
    for m in markers:
        (t / m).write_text("x\n", newline="\n")
    if bound:
        (t / "bound").write_text(bound + "\n", newline="\n")
    sysroot = tmp_path / "sys"
    for dev in present:
        (sysroot / "sys" / "block" / dev).mkdir(parents=True)
        (sysroot / "sys" / "block" / dev / "size").write_text("1000\n", newline="\n")
    serials = "".join(f"{d}) echo {s};; " for d, s in present.items())
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(sysroot)!r}; "
             + agent_sources("common", "sysinfo", "failmark", "stall") +
             f'disk_serial() {{ case "$1" in {serials} esac; }}; '
             f"list_drawers() {{ {'echo ' + ' '.join(present) if present else ':'}; }}; "
             f"drawer_gui_word sda {state}")
    assert out.returncode == 0, out.stderr
    return out.stdout


@requires_native(("bash", BASH))
@pytest.mark.parametrize("state, markers, bound, present, want", [
    # pulled, the disk is not in the machine: grey, waiting for it to come back
    ("failed", ("pulled",), "S-1|1000|1", {}, "pulled"),
    # pulled and a drawer holds the same serial and size, under another name (it came back as sdd): "returned"
    ("failed", ("pulled",), "S-1|1000|1", {"sdd": "S-1"}, "returned"),
    # another disk in the drawers is not it
    ("failed", ("pulled",), "S-1|1000|1", {"sdd": "S-2"}, "pulled"),
    # nothing to know it by: it can never be seen as back, but it stays a pull
    ("failed", ("pulled",), "|1000|1", {"sdd": "S-1"}, "pulled"),
    # real failures stay failed: no pull, a foreign disk, a completion that failed
    ("failed", (), "S-1|1000|1", {"sdd": "S-1"}, "failed"),
    ("failed", ("pulled", "foreign"), "S-1|1000|1", {}, "failed"),
    ("failed", ("pulled", "back_failed"), "S-1|1000|1", {}, "failed"),
    # only a failed drawer is rewritten
    ("writing", ("pulled",), "S-1|1000|1", {}, "writing"),
    ("done", ("pulled",), "S-1|1000|1", {}, "done"),
])
def test_the_word_of_a_pulled_drawer(tmp_path, state, markers, bound, present, want):
    assert _word(tmp_path, state, markers, bound, present) == want


@requires_native(("bash", BASH))
@pytest.mark.parametrize("run_state, present, want", [
    ("writing", {}, "pulled"),
    # #3209: past the write (drawer_back all, drawers.sh) nobody waits for the drawer -- red, with its reason, as on the wire
    ("verifying", {}, "failed"),
    ("verifying", {"sdd": "S-1"}, "failed"),   # back in a drawer, but nobody joins it any more: not "returned"
    ("failed", {}, "failed"),
    ("done", {}, "failed"),
    ("", {}, "failed"),
    (None, {}, "failed"),   # a state that cannot be read is not `writing` (principle 5)
])
def test_pulled_is_grey_only_while_the_machine_is_writing(tmp_path, run_state, present, want):
    """The same gate as the wire's `"pulled": true` (drawerplug.sh, #3288): the screen must not say "waiting" after the write."""
    assert _word(tmp_path, "failed", ("pulled",), "S-1|1000|1", present, run_state) == want


def _record(tmp_path: Path, extra: str = "", drawer_state: str = "writing", moved_ago: int = 0) -> list[str]:
    """One cloner_gui_state: sda <drawer_state>, 8 GB of 20 GB, anchor 700 s ago at 1 GB, moved <moved_ago> s ago."""
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(exist_ok=True); gui.mkdir(exist_ok=True)
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", drawer_state, 20 * G, 0, str(8 * G))
    now = int(time.time())
    (gui / "moved.sda").write_text(f"{now - moved_ago} {8 * G}\n", newline="\n")
    (gui / "pace.sda").write_text(f"{now - 700} {G}\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + agent_sources("stall") + PORTS + extra + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_the_drawer_record_says_pulled_not_failed(tmp_path):
    run = tmp_path / "run"; (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "pulled").write_text("x\n", newline="\n")
    (run / "targets" / "sda" / "bound").write_text("S-1|1000|1\n", newline="\n")
    (run / "state").write_text("writing\n", newline="\n")
    lines = _record(tmp_path, 'list_drawers() { :; }; ', drawer_state="failed")
    drawer = next(l for l in lines if l.startswith("drawer=1|sda|"))
    assert drawer.split("|")[2] == "pulled", drawer


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_the_drawer_record_says_failed_once_the_write_is_over(tmp_path):
    """#3209: the whole chain (cloner_gui_state), not only the function: `verifying` after drawer_back all -> red."""
    run = tmp_path / "run"; (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "pulled").write_text("x\n", newline="\n")
    (run / "targets" / "sda" / "bound").write_text("S-1|1000|1\n", newline="\n")
    (run / "state").write_text("verifying\n", newline="\n")
    lines = _record(tmp_path, 'list_drawers() { :; }; ', drawer_state="failed")
    drawer = next(l for l in lines if l.startswith("drawer=1|sda|"))
    assert drawer.split("|")[2] == "failed", drawer


# --- the screen: the real parser, the real cloner screen ------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
#include "theme.h"
#include "screens_rounds.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
Text __real_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px);
Text __wrap_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make_ltr(cr, font, px, weight, utf8, width_px);
}
void __real_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g);
void __wrap_lc_bay_draw(App *a, cairo_t *cr, Rect b, const LcBay *g) {
    printf("bay=%.1f|%.1f|%s|%s\n", b.y, b.h, g->pill ? g->pill : "", g->line);
    __real_lc_bay_draw(a, cr, b, g);
}
double __real_lc_cage(App *a, cairo_t *cr, Rect cage, const char *title, const char *count);
double __wrap_lc_cage(App *a, cairo_t *cr, Rect cage, const char *title, const char *count) {
    printf("cage=%.1f|%.1f|%.1f\n", cage.x, cage.y, cage.h);
    return __real_lc_cage(a, cr, cage, title, count);
}
double __real_lc_hero_eta(App *a, cairo_t *cr, double x0, double xr, double y, const char *label, const char *h1,
                          const char *s2, const char *big, const LcEta *e, int alarm);
double __wrap_lc_hero_eta(App *a, cairo_t *cr, double x0, double xr, double y, const char *label, const char *h1,
                          const char *s2, const char *big, const LcEta *e, int alarm) {
    double h = __real_lc_hero_eta(a, cr, x0, xr, y, label, h1, s2, big, e, alarm);
    printf("hero=%.1f\n", h);
    return h;
}
#define SAME(p, q) ((p).r == (q).r && (p).g == (q).g && (p).b == (q).b)
double __real_sb_bay_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg, int emphasis);
double __wrap_sb_bay_pill(cairo_t *cr, double right, double cy, const char *label, Rgb fg, Rgb bg, int emphasis) {
    double w = __real_sb_bay_pill(cr, right, cy, label, fg, bg, emphasis);
    /* grey = the tag of a plain row: muted on hover (lcbay.c pfg/pbg) */
    printf("pill=%s|%d|%.1f|%d\n", label, emphasis, w, SAME(fg, THEME_LIGHT.muted) && SAME(bg, THEME_LIGHT.hover));
    return w;
}
void __real_sb_led(cairo_t *cr, double cx, double cy, double d, Rgb c, Rgb soft, int ring, double alpha);
void __wrap_sb_led(cairo_t *cr, double cx, double cy, double d, Rgb c, Rgb soft, int ring, double alpha) {
    printf("led=%d|%d\n", SAME(c, THEME_LIGHT.led_idle), SAME(c, THEME_LIGHT.danger));   /* the dot is drawn: grey? red? */
    __real_sb_led(cr, cx, cy, d, c, soft, ring, alpha);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2; state_parse(&a.st, f); fclose(f);
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

HISTORY_END = 4102483920   # 2100: "not over" whenever the test runs


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("pulled3270")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    wraps = "text_make,text_make_ltr,lc_bay_draw,lc_cage,lc_hero_eta,sb_bay_pill,sb_led"
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            f'-Wl,--wrap={wraps.replace(",", ",--wrap=")} -o "$2" "$3" ${{@:4}} $(pkg-config --libs pangocairo cairo libdrm) -lm',
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


def state(second: str = "writing|{b}|{t}||{rate}|{eta}|2|SATA 1", rate: int = 55_000_000, eta: int = 840,
          forecast: str = "1|%d|" % HISTORY_END, first: str | None = None, extra: str = "") -> str:
    """Two drawers of one machine, 8 of 20 GB written. <first> overrides drawer 1's tail (state onward)."""
    now = int(time.time())
    one = first or "writing|{b}|{t}||{rate}|{eta}|1|SATA 0"
    fmt = lambda tail: tail.format(b=8 * G, t=20 * G, rate=rate, eta=eta)
    return ("cloner=classroom-win11 v7\n"
            f"drawer=1|sda|{fmt(one)}\n"
            f"drawer=2|sdb|{fmt(second)}\n"
            + f"forecast={forecast}\n" + extra + f"updated={now}\n")


def one(lines: list[str], prefix: str) -> list[str]:
    return [l[len(prefix):] for l in lines if l.startswith(prefix)]


def first_led(lines: list[str]) -> list[str]:
    """The dot of the first row: lc_bay_draw draws it last, and nothing else between it and the next row does."""
    at = next(i for i, l in enumerate(lines) if l.startswith("bay="))
    return next(l for l in lines[at:] if l.startswith("led="))[4:].split("|")


PULLED = "pulled|{b}|{t}|הדיסק נעלם באמצע הכתיבה (40%). בדקו שהמגירה סגורה והכבל מחובר, וכתבו אותו שוב בגל הבא.|-1|-1|1|SATA 0"


@_GUI
@pytest.mark.parametrize("word, line, pill", [
    ("pulled", "נשלף · ממתין להחזרה", "נשלף"),
    ("returned", "הוחזר · ממתין להצטרפות", "הוחזר"),
])
def test_a_pulled_drawer_is_a_state_not_a_failure(driver, tmp_path, word, line, pill):
    got = run_driver(driver, tmp_path, state(first=PULLED.replace("pulled|", word + "|", 1)))
    first = one(got, "bay=")[0].split("|")
    assert first[2:] == [pill, line], first
    assert not any("נכשל" in l for l in got), [l for l in got if "נכשל" in l]
    tag = [p for p in one(got, "pill=") if p.startswith(pill + "|")][0].split("|")
    assert tag[1] == "0" and tag[3] == "1", tag          # the size of "SMART תקין", and grey -- not red, not orange
    assert first_led(got) == ["1", "0"], first_led(got)  # the dot stays, grey


@_GUI
def test_a_pulled_drawer_of_a_round_that_ended_is_the_failure_it_became(driver, tmp_path):
    """The end is recorded and the disk never came back: red, in the agent's sentence -- not grey for ever."""
    end = "end_poweroff=%d|partial|1|2|1\n" % (int(time.time()) + 60)
    got = run_driver(driver, tmp_path, state(first=PULLED, second="done|{b}|{t}||-1|-1|2|SATA 1", extra=end))
    first = one(got, "bay=")[0].split("|")
    assert first[2] == "נכשל" and first[3].startswith("נכשל: הדיסק נעלם"), first
    assert first_led(got) == ["0", "1"], first_led(got)  # red, as any failure
