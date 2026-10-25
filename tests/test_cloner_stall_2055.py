"""#2055 -- the cloner's round screen while the stream stands (metal 23/10).

Nadav pulled the network cable for 3 minutes in a round (cloner 2, drawer 2). The
whole time the screen said "מעתיק לדיסק 2 · כותב 22 MB/s · 86% · כ-2 דק' נותרו ·
עודכן לפני 0 שניות"; the server saw the same 16,770,380,200 bytes in every sample.

Why, from the code (the hypothesis in the issue, half right):

* the rate is **not** partclone's: ``cloner_gui_pace`` (clonergui.sh) divides the bytes
  since the first sample this kiosk saw by the seconds since then -- an average since
  the wave began. With the bytes standing, the numerator stands and the denominator
  grows: 22 MB/s after ~12 minutes decays to ~19 after 3 more (the 19 Nadav saw);
* "עודכן לפני" is ``updated=`` -- the moment the kiosk wrote its state file, every tick,
  whether or not a byte moved. So it read 0 seconds forever.

The fix is display only (a 30 s cut came back and finished, 10:33): bytes that did not
move for CLONER_STALL_S -> rate 0, *measured* (``-1`` stays "not measured"), "ממתין
לזרם מהשרת" in orange; ``drawer_moved=`` carries when they last moved, and the head's
age counts from it. The write itself is not touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, PORTS, PRELUDE, _pkgconfig, env, posix, sh, target

G = 1_000_000_000


def _state(tmp_path: Path, moved_ago: int | None, cur: str) -> dict:
    """One cloner_gui_state with sda writing <cur> bytes; moved.sda set <moved_ago> s ago
    at the same byte count (None = no history: first sight)."""
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(exist_ok=True); gui.mkdir(exist_ok=True)
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "writing", 20 * G, 0, cur)
    now = int(time.time())
    if moved_ago is not None:
        (gui / "moved.sda").write_text(f"{now - moved_ago} {cur}\n", newline="\n")
        (gui / "pace.sda").write_text(f"{now - 700} 1000\n", newline="\n")   # the wave's anchor, 700 s ago
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (gui / "state").read_text(encoding="utf-8").splitlines()
    drawer = next(l for l in lines if l.startswith("drawer=1|sda|"))
    f = drawer.split("|")
    moved = [l for l in lines if l.startswith("drawer_moved=")]
    return {"rate": int(f[6]), "eta": int(f[7]), "moved": moved, "now": now, "lines": lines}


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_bytes_standing_past_the_ceiling_are_rate_zero_and_keep_their_moment(tmp_path):
    got = _state(tmp_path, 180, "16770380200")      # the metal cut: 3 minutes, not a byte
    assert got["rate"] == 0 and got["eta"] == -1, got["lines"]
    assert got["moved"] == [f"drawer_moved=sda|{got['now'] - 180}"], got["moved"]


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_a_short_pause_is_not_a_stall(tmp_path):
    """Under the ceiling: the average as before -- not 0, not "not measured"."""
    got = _state(tmp_path, 4, "16770380200")
    assert got["rate"] > 0, got["lines"]


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_bytes_that_moved_again_are_a_rate_again_and_a_new_moment(tmp_path):
    got = _state(tmp_path, 180, "16770380200")
    assert got["rate"] == 0
    target(tmp_path / "run", "sda", "writing", 20 * G, 0, "16800000000")   # the cable is back
    out = sh(env(tmp_path / "run", tmp_path / "gui") + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    lines = (tmp_path / "gui" / "state").read_text(encoding="utf-8").splitlines()
    f = next(l for l in lines if l.startswith("drawer=1|sda|")).split("|")
    assert int(f[6]) > 0, lines
    moved = [l for l in lines if l.startswith("drawer_moved=")]
    assert len(moved) == 1 and int(moved[0].split("|")[1]) >= got["now"], moved


@requires_native("jq", ("bash", BASH), why="json_get reads the image name with jq")
def test_a_drawer_that_is_not_writing_has_no_moment(tmp_path):
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    target(run, "sda", "done", 100, 100, "")
    (gui / "moved.sda").write_text("1 100\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    assert not any(l.startswith("drawer_moved=") for l in (gui / "state").read_text(encoding="utf-8").splitlines())
    assert not (gui / "moved.sda").exists()


# --- the screen: real parser, real cloner screen, every text it made ---------------

DRIVER = r'''
#include <stdio.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
Text __real_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir);
Text __wrap_text_make(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px, Dir dir) {
    if (utf8) printf("text=%s\n", utf8);
    return __real_text_make(cr, font, px, weight, utf8, width_px, dir);
}
Text __real_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px);
Text __wrap_text_make_ltr(cairo_t *cr, Font font, double px, int weight, const char *utf8, int width_px) {
    if (utf8) printf("text=%s\n", utf8);   /* the metric values ("163 MB/s") */
    return __real_text_make_ltr(cr, font, px, weight, utf8, width_px);
}
int main(int argc, char **argv) {
    (void)argc; static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
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


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("stall2055")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make,--wrap=text_make_ltr -o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def texts(driver: Path, tmp_path: Path, rate: int, moved_ago: int | None) -> list[str]:
    now = int(time.time())
    state = ("cloner=Office 2024\n"
             f"drawer=2|sdb|writing|{int(0.86 * 19.5 * G)}|{int(19.5 * G)}||{rate}|{120 if rate > 0 else -1}|2|SATA 1\n"
             + (f"drawer_moved=sdb|{now - moved_ago}\n" if moved_ago is not None else "")
             + f"updated={now}\n")
    p = tmp_path / "state.txt"; p.write_text(state, encoding="utf-8", newline="\n")
    env2 = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    r = subprocess.run([str(driver), posix(p)], capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=env2, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    got = [l[len("text="):] for l in r.stdout.splitlines() if l.startswith("text=")]
    assert any("מעתיק" in t for t in got), got   # the cloner's round screen, not another view
    return got


@_GUI
def test_the_standing_stream_reads_waiting_zero_and_the_age_of_the_last_byte(driver, tmp_path):
    got = texts(driver, tmp_path, 0, 180)
    joined = "\n".join(got)
    assert "ממתין לזרם מהשרת · 0 MB/s" in got, got          # the drawer's line
    assert "0 MB/s" in got, got                              # the speed card: measured zero
    assert got.count("ממתין לזרם מהשרת") == 2, got           # the hero's "left", the speed card's caption
    assert "22 MB/s" not in joined and "כותב · " not in joined, got
    assert "עודכן לפני 3 דקות" in joined, got                 # the last byte, not the last redraw
    assert "עודכן לפני 0 שניות" not in joined, got


@_GUI
def test_a_moving_stream_reads_as_before(driver, tmp_path):
    """The guard: rate > 0 is "כותב · N MB/s", and the age is the last byte's (just now)."""
    got = texts(driver, tmp_path, 22_000_000, 0)
    joined = "\n".join(got)
    assert any(t.startswith("כותב · ") and "22 MB/s" in t for t in got), got
    assert "ממתין לזרם מהשרת" not in joined, got


@_GUI
def test_not_measured_is_not_zero(driver, tmp_path):
    """Principle 5b: -1 (an older agent, the first sample) is "not measured", never "0"."""
    got = texts(driver, tmp_path, -1, None)
    joined = "\n".join(got)
    assert "כותב · הקצב טרם נמדד" in got, got
    assert "ממתין לזרם מהשרת" not in joined and "0 MB/s" not in got, got


# --- the single restore's card (srgui.sh) reads the same pace -----------------------


@pytest.mark.parametrize("rate, said", [
    ("0", "ממתין לזרם מהשרת · 0 MB/s"),   # measured still: not folded into "not measured"
    ("-1", "מודד את הקצב…"),               # the guard: not measured stays in words
])
def test_the_restore_card_says_a_standing_stream_too(rate, said):
    """cloner_gui_pace's 0 reaches sr_pace_text too (#1561's card): before this it read
    "מודד את הקצב…" -- a measured stall said as "not measured yet" (principle 5b)."""
    out = subprocess.run([BASH, "-c", f'. {posix(GUI.parent / "agent")}/lib/srgui.sh; _cg_rate={rate}; _cg_eta=-1; sr_pace_text'],
                         capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout == said, out.stdout
