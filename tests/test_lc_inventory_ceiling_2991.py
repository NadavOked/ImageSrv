"""#2991 -- the local clone's drawer check: one bad drawer held the whole screen.

Metal 12/11 (cloner 2, v0.92.0): "בודק את הדיסקים במחשב…" on an empty screen for some 3.5
minutes. ``lc_inventory`` read every drawer in turn with no ceiling, and the screen drew
nothing until ``inv.state`` said done. What the issue asks, and what is checked here:

* **a ceiling per drawer** (``LC_INV_MAX_S``, lcscan.sh): a drawer that does not answer in it
  ends as a line that says so (usable 0, no size, no serial), and the drawers after it are
  read -- the whole check is not held by it;
* such a drawer is **never a target**: the verdict on an unread size is "unchecked"
  (lc_verdict), and the screen treats a size it was not given as red (``lc_red``);
* **the log says how long each drawer took** -- who held the others;
* **the screen shows the drawers that are ready while the rest are read**, with the one being
  read and its time under them (``lc_scan`` from lcgui.sh), and "המשך לאישור" cannot go before
  the last one.

**Negative control** (Testrunner): ``agent/lib/localclone/lcinv.sh``, ``lcgui.sh``,
``agent/imagesrv-agent``, ``native-gui/src/lc_state.c``, ``lc_text.c``, ``lcui.h``,
``localclone.h`` and ``screens_local.c`` at the base, and ``lcscan.sh`` removed -> the agent
tests fail on the time and the missing line, the screen tests on the missing cards, the
missing "בודק" line and ``lc_can_go``.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, posix
from test_lc_card_fit_2205 import BAY_BASE, ROLE_BASE
from test_local_clone_agent import run
from test_native_local_clone import pytestmark as native_mark

SHELL = requires_native(("bash", BASH))
NATIVE = native_mark

LATE = "לא הגיבה בזמן — לא תיכתב"
#: three drawers; sdb does not answer (a stuck smartctl, a read the kernel retries)
DRAWERS = ('list_drawers() { echo sda; echo sdb; echo sdc; }; '
           'disk_port() { case "$1" in sda) echo 1 ;; sdb) echo 2 ;; sdc) echo 3 ;; esac; }; '
           'lc_inv_disk() { [ "$1" != sdb ] || sleep "${HANG_S:-40}"; '
           'echo "0|$1|M-$1|S-$1|500000000000|ok|ok|Windows|3|1|480000000000|1|"; }; ')


@SHELL
def test_a_drawer_that_does_not_answer_holds_no_other(tmp_path):
    t0 = time.monotonic()
    out = run(tmp_path, DRAWERS + 'export LOG_FILE="$RUN_DIR/agent.log"; LC_INV_MAX_S=2; lc_inventory; echo "rc=$?"; '
              'for f in "$LC"/inv/*.line; do echo "L $(basename "$f") $(cat "$f")"; done; '
              'echo "state=$(cat "$LC/inv.state")"; echo "scan=$([ -e "$LC/inv/scan" ] && echo yes || echo no)"; '
              'cat "$LOG_FILE"', timeout=90)
    took = time.monotonic() - t0
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout and "state=done" in out.stdout and "scan=no" in out.stdout, out.stdout
    # the ceiling, not the drawer's 40 s: the room's own start-up is the rest
    assert took < 25, (took, out.stdout)
    lines = dict(l.split(" ", 2)[1:] for l in out.stdout.splitlines() if l.startswith("L "))
    assert sorted(lines) == ["sda.line", "sdb.line", "sdc.line"], lines
    assert lines["sda.line"].endswith("|1|") and lines["sdc.line"].endswith("|1|"), lines   # read as before
    late = lines["sdb.line"].split("|")
    assert len(late) == 13 and late[1] == "sdb", late
    assert late[3] == "" and late[4] == "" and late[11] == "0" and late[12] == LATE, late   # no serial, no size, not usable
    for d in ("sda", "sdb", "sdc"):   # how long each one took -- who held the others
        assert re.search(rf"the check of {d} took \d+s", out.stdout), out.stdout
    assert "sdb did not answer its check in 2s" in out.stdout, out.stdout


@SHELL
def test_a_drawer_that_answers_in_time_is_its_own_line(tmp_path):
    """The guard: the ceiling only cuts what does not answer -- a slow drawer inside it is read."""
    out = run(tmp_path, DRAWERS + 'export LOG_FILE="$RUN_DIR/agent.log"; HANG_S=1; LC_INV_MAX_S=10; lc_inventory; '
              'cat "$LC/inv/sdb.line"', timeout=90)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("|S-sdb|500000000000|ok|ok|Windows|3|1|480000000000|1|"), out.stdout
    assert LATE not in out.stdout


@SHELL
def test_a_drawer_that_did_not_answer_is_never_a_target(tmp_path):
    out = run(tmp_path, 'mkdir -p "$LC/inv" "$LC/measure"; echo 0 > "$RUN_DIR/min_free_gb"; '
              'echo "3|sdc|C|s3|480000000000|ok|ok|Windows|4|1|480000000000|1|" > "$LC/inv/sdc.line"; '
              f'echo "2|sdb|B||||||0|-1|-1|0|{LATE}" > "$LC/inv/sdb.line"; echo "none||" > "$LC/measure/sdc"; '
              'lc_verdict_for sdc sdb')
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("|")[0] == "unchecked", out.stdout


@SHELL
def test_the_screen_hears_which_drawer_is_being_read(tmp_path):
    """While sdb is read, the bridge already carries sda's drawer and says which one is read
    now, for how long, and how many wait after it."""
    out = run(tmp_path, DRAWERS + 'export LOG_FILE="$RUN_DIR/agent.log" GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC"; '
              'echo 0 > "$RUN_DIR/min_free_gb"; echo local > "$LC/screen"; echo idle > "$LC/phase"; '
              'LC_INV_MAX_S=30; lc_inventory > /dev/null 2>&1 & '
              'n=0; until grep -q "^sdb|" "$LC/inv/scan" 2>/dev/null || [ $n -ge 200 ]; do sleep 0.1; n=$((n + 1)); done; '
              'lc_gui_state; cat "$GUI_DIR/state"', timeout=90)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert "lc_inv=scanning" in lines, out.stdout
    assert [l for l in lines if l.startswith("lc_disk=") and "|sda|" in l], out.stdout   # ready, so on the screen
    assert not [l for l in lines if l.startswith("lc_disk=") and "|sdb|" in l], out.stdout
    scan = [l for l in lines if l.startswith("lc_scan=")]
    assert len(scan) == 1 and re.fullmatch(r"lc_scan=\d+\|\d+\|1", scan[0]), out.stdout   # sdc waits after it


# --- the screen ---------------------------------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
void __real_hit_add(App *, Rect, int);
void __wrap_hit_add(App *a, Rect r, int id) { printf("H %d\n", id); __real_hit_add(a, r, id); }
void __real_text_draw_r(cairo_t *, Text *, double, double, Rgb);
void __wrap_text_draw_r(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    printf("T %s\n", pango_layout_get_text(t->layout)); __real_text_draw_r(cr, t, x, y, c);
}
void __real_text_draw(cairo_t *, Text *, double, double, Rgb);
void __wrap_text_draw(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    printf("T %s\n", pango_layout_get_text(t->layout)); __real_text_draw(cr, t, x, y, c);
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a);
    int n = a.st.lc.ndisks, src = atoi(argv[2]);
    a.lcu.nroles = n;   /* explicit roles: the source, and every other one a target */
    for (int i = 0; i < n; i++) {
        snprintf(a.lcu.role_dev[i], sizeof a.lcu.role_dev[i], "%s", a.st.lc.disks[i].dev);
        a.lcu.role[i] = i == src ? LCR_SOURCE : LCR_TARGET;
    }
    printf("GO %d\n", lc_can_go(&a));
    for (int i = 0; i < n; i++) printf("TOK %s %d\n", a.st.lc.disks[i].dev, lc_target_ok(&a, i));
    app_route(&a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800);
    int ok = cairo_status(cr) == CAIRO_STATUS_SUCCESS;
    cairo_destroy(cr); cairo_surface_destroy(s);
    return ok ? 0 : 5;
}
'''

G = 1_000_000_000
#: drawers 1 and 3 are read, drawer 2 is being read for 12 s, nothing after it
READY = (f"lc_disk=1|sda|Crucial|S1|{500 * G}|ok|ok|Windows|3|{100 * G}|{480 * G}|1||1|SATA||\n"
         f"lc_disk=3|sdc|Samsung|S3|{500 * G}|ok|ok|Windows|3|{100 * G}|{480 * G}|1||3|SATA||\n"
         f"lc_src=sdc|done|{100 * G}|\nlc_fit=sda|fits|{480 * G}|\n")
SCANNING = "lc_screen=local\nlc_inv=scanning\nlc_phase=idle\nlc_usb=0\nlc_scan=2|12|0\n" + READY
DONE = "lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb=0\n" + READY
#: the check is over, drawer 2 did not answer: its line has no size and no serial
LATE_DONE = DONE + f"lc_disk=2|sdb|B||||||0|-1|-1|0|{LATE}|2|SATA||\nlc_fit=sdb|unchecked||לא הצלחנו למדוד\n"


@pytest.fixture(scope="module")
def scan_driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("scan2991")
    driver, binary = d / "driver.c", d / "driver"
    driver.write_text(DRIVER, encoding="utf-8", newline="\n")
    sources = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert sources.returncode == 0, sources.stderr
    srcs = [posix(GUI / s) for s in sources.stdout.split() if not s.endswith("main.c")]
    assert srcs, "print-gui-src returned no production sources"
    build = subprocess.run([BASH, "-c",
        'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" '
        '$(pkg-config --cflags pangocairo cairo libdrm) '
        '-Wl,--wrap=hit_add,--wrap=text_draw_r,--wrap=text_draw '
        '-o "$2" "$3" "${@:4}" $(pkg-config --libs pangocairo cairo libdrm) -lm',
        "_", posix(GUI), posix(binary), posix(driver), *srcs],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _draw(binary, tmp_path, state, src):
    path = tmp_path / "state.txt"
    path.write_text(state, encoding="utf-8", newline="\n")
    out = subprocess.run([str(binary), posix(path), str(src)], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert out.returncode == 0, out.stderr + out.stdout
    lines = out.stdout.splitlines()
    cards = sorted({int(l.split()[1]) - BAY_BASE for l in lines if l.startswith("H ")
                    and BAY_BASE <= int(l.split()[1]) < ROLE_BASE})
    texts = [l[2:] for l in lines if l.startswith("T ")]
    go = int(next(l for l in lines if l.startswith("GO ")).split()[1])
    tok = {l.split()[1]: int(l.split()[2]) for l in lines if l.startswith("TOK ")}
    return cards, texts, go, tok


@NATIVE
def test_the_ready_drawers_are_on_the_screen_while_one_is_read(scan_driver, tmp_path):
    cards, texts, go, _ = _draw(scan_driver, tmp_path, SCANNING, src=1)
    assert cards == [0, 1], (cards, texts)   # both ready drawers, not an empty cage
    assert "בודק את הדיסקים במחשב…" not in texts, texts
    assert [t for t in texts if t.startswith("בודק את דיסק 2…") and "0:12" in t], texts
    assert go == 0, "nothing goes before the last drawer is read"


@NATIVE
def test_once_every_drawer_is_read_the_choice_goes_as_before(scan_driver, tmp_path):
    """The guard: the same drawers with the check over -- the cards, no "בודק" line, and it can go."""
    cards, texts, go, _ = _draw(scan_driver, tmp_path, DONE, src=1)
    assert cards == [0, 1], cards
    assert not [t for t in texts if t.startswith("בודק את")], texts
    assert go == 1


@NATIVE
def test_a_drawer_that_did_not_answer_is_drawn_and_is_no_target(scan_driver, tmp_path):
    cards, texts, _, tok = _draw(scan_driver, tmp_path, LATE_DONE, src=1)
    assert len(cards) == 3, cards
    assert [t for t in texts if LATE in t], texts
    # a size not read is not "0 GB" -- the number alone: "100 GB" and "500 GB" carry "0 GB" inside them
    assert not [t for t in texts if re.search(r"(?<![\d.])0(?:\.0+)? GB", t)], texts
    assert tok["sdb"] == 0 and tok["sda"] == 1, tok
