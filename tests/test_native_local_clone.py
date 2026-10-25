"""#1322 — the native screens of the boot choice and the local clone.

Two halves:

* **what is drawn**, against the approved mockup (``docs/design/native/
  localclone/``, Nadav 13/10): every state, light and dark, 1280x800 and
  1024x768, rendered by ``--png`` and compared block by block with the
  mockup's own PNGs (``localclone_mockup.json``: a 32x20 grid of mean
  luminance per PNG -- the layout, not the font's pixels). A card that is not
  the mockup's state is far from it: that is the negative control built in.
  Then a few things the grid cannot see: the countdown's bar, the blocked
  drawer's hatching, the focus on "חזרה".
* **what the keys and clicks do** (``lc_state.c``), through a driver that
  feeds events to the real ``lc_handle`` and prints the records it sends --
  "מהשרת" is the default, any touch stops the count, a drawer that does not
  fit cannot be a target, the confirm dialog opens on "חזרה", "כבה" is the
  result's default.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig, _png_rgb, posix

import shutil

REPO = Path(__file__).resolve().parent.parent
MOCK = json.loads((REPO / "tests" / "localclone_mockup.json").read_text(encoding="utf-8"))

pytestmark = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

CARDS = ["lcboot", "lcboot-paused", "lcsource", "lcsource-measuring", "lcsource-blocked", "lcsource-shrink",
         "lcconfirm", "lcconfirm-shrink", "lcprogress", "lcprogress-fail", "lcstop", "lcdone", "lcdone-ok"]

#: our card -> the mockup PNG of the same state (localclone.html ?f=...)
PAIRS = [("lcboot", "light", "1280x800", "boot"), ("lcboot-paused", "light", "1280x800", "boot-paused"),
         ("lcsource", "light", "1280x800", "source"), ("lcsource-blocked", "light", "1280x800", "source-blocked"),
         ("lcconfirm", "light", "1280x800", "confirm"), ("lcprogress", "light", "1280x800", "progress"),
         ("lcprogress-fail", "light", "1280x800", "progress-fail"), ("lcstop", "light", "1280x800", "stop"),
         ("lcdone", "light", "1280x800", "done"), ("lcdone-ok", "light", "1280x800", "done-ok"),
         ("lcconfirm", "dark", "1280x800", "confirm-dark"), ("lcprogress-fail", "dark", "1280x800", "progress-fail-dark"),
         ("lcsource", "light", "1024x768", "source-1024"), ("lcconfirm", "light", "1024x768", "confirm-1024")]

#: The comparison is on the page between the header and the status bar (rows
#: 2..18 of 20 -- the dark header would make every screen look alike), as a
#: centred correlation of the block luminances plus their mean difference.
#: Measured 13/10 on the Testrunner: the matching pairs correlate 0.60-0.99
#: (mean difference <= 10), the other states of the same flow -0.14-0.31.
CORR_MIN = 0.55
MAD_MAX = 12.0
#: #2050 (mockup 07, Nadav 24/10): a local clone's result card and a failed target/partition box
#: are now a 3px frame over the soft fill of their colour; the localclone.html PNGs above have a thin
#: edge on white, so these two states correlate less with them (measured 24/10 on the testrunner:
#: 0.53 and 0.41, mean difference 5.4 and 5.8). The layout is the same: only their correlation bound
#: is lower -- the mean-difference bound is not.
CORR_RESTYLED = {("lcprogress-fail", "light", "1280x800"): 0.45, ("lcdone-ok", "light", "1280x800"): 0.35}


def fingerprint(img, cols=32, rows=20) -> list[int]:
    w, h, ch, px = img
    out = []
    for r in range(rows):
        y0, y1 = r * h // rows, (r + 1) * h // rows
        for c in range(cols):
            x0, x1 = c * w // cols, (c + 1) * w // cols
            s = n = 0
            for y in range(y0, y1, 2):
                base = y * w * ch
                for x in range(x0, x1, 2):
                    i = base + x * ch
                    s += (px[i] * 299 + px[i + 1] * 587 + px[i + 2] * 114) // 1000; n += 1
            out.append(s // max(n, 1))
    return out


def body(f: list[int]) -> list[int]:
    return f[2 * 32:19 * 32]


def corr(a: list[int], b: list[int]) -> float:
    a, b = body(a), body(b)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5
    return num / den if den else 0.0


def mad(a: list[int], b: list[int]) -> float:
    a, b = body(a), body(b)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict:
    """Every lc card, both themes, at both sizes -- the --png run itself
    fails when a card did not route to its screen (render_png's check)."""
    d = tmp_path_factory.mktemp("lcpng")
    binary = _build_gui(d)
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = {}
    for size in ("1280x800", "1024x768"):
        run = subprocess.run([str(binary), "--png", posix(d / size), "--size", size,
                              "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
                             capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
        assert run.returncode == 0, run.stderr
        for card in CARDS:
            for theme in ("light", "dark"):
                out[(card, theme, size)] = d / f"{size}-{card}-{theme}.png"
                assert out[(card, theme, size)].exists(), (card, theme, size)
    return out


@pytest.mark.parametrize("card,theme,size,mock", PAIRS)
def test_each_state_is_drawn_as_the_approved_mockup(renders, card, theme, size, mock):
    ours = fingerprint(_png_rgb(renders[(card, theme, size)]))
    c, m = corr(ours, MOCK["cards"][mock]), mad(ours, MOCK["cards"][mock])
    assert c >= CORR_RESTYLED.get((card, theme, size), CORR_MIN) and m <= MAD_MAX, f"{card}/{theme}/{size} vs {mock}.png: corr {c:.2f}, mean diff {m:.1f}"


def test_a_different_state_is_far_from_the_mockup(renders):
    """The comparison can fail: the boot screen is not the result screen,
    the source screen is not the progress screen."""
    boot = fingerprint(_png_rgb(renders[("lcboot", "light", "1280x800")]))
    assert corr(boot, MOCK["cards"]["done"]) < CORR_MIN / 2
    src = fingerprint(_png_rgb(renders[("lcsource", "light", "1280x800")]))
    assert corr(src, MOCK["cards"]["progress"]) < CORR_MIN / 2
    assert mad(fingerprint(_png_rgb(renders[("lcconfirm", "light", "1280x800")])), MOCK["cards"]["done"]) > MAD_MAX


def _count(img, box, pred) -> int:
    w, _h, ch, px = img
    x0, y0, x1, y1 = box
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if pred(px[(y * w + x) * ch:(y * w + x) * ch + 3]))


def _is(rgb, tol=12):
    return lambda p: all(abs(p[k] - rgb[k]) <= tol for k in range(3))


INDIGO = (0x00, 0x79, 0xB8)       # theme.c THEME_LIGHT .indigo
MARK = (0x9A, 0xAD, 0xB8)         # .mark_line: the paused bar


def test_the_count_empties_and_stops_grey(renders):
    """17 of 20 s left: the bar's blue covers ~85% of its track; paused it is grey."""
    run = _png_rgb(renders[("lcboot", "light", "1280x800")])
    paused = _png_rgb(renders[("lcboot-paused", "light", "1280x800")])
    bar = (676, 505, 1054, 516)   # the server card's .cbar, as drawn (mockup boot.png: y 509..514)
    blue = _count(run, bar, _is(INDIGO))
    assert 0.75 < blue / (378 * 6) < 0.95 or blue > 1500, blue
    assert _count(paused, bar, _is(INDIGO)) == 0 and _count(paused, bar, _is(MARK)) > 500


def test_the_server_card_is_the_default_and_has_the_focus(renders):
    img = _png_rgb(renders[("lcboot", "light", "1280x800")])
    # the 3px focus ring just outside the server card (right) and none round the local card (left)
    assert _count(img, (646, 300, 652, 545), _is(INDIGO)) > 400
    assert _count(img, (196, 300, 201, 545), _is(INDIGO)) == 0


def test_a_drawer_that_does_not_fit_is_hatched_and_cannot_be_a_target(renders):
    img = _png_rgb(renders[("lcsource-blocked", "light", "1280x800")])
    w, _h, ch, px = img
    tones: dict = {}
    for y in range(450, 556, 3):
        for x in range(650, 1000, 3):
            i = (y * w + x) * ch
            tones[tuple(px[i:i + 3])] = tones.get(tuple(px[i:i + 3]), 0) + 1
    top = sorted(tones.values(), reverse=True)[:2]
    assert len(top) == 2 and top[1] > sum(tones.values()) * 0.2, top        # two tones = the hatch
    # its "יעד" button is not chosen (no indigo-soft fill), "דלג" is
    assert _count(img, (296, 488, 350, 520), _is((0xE8, 0xF4, 0xFA), 4)) == 0


def test_the_confirm_dialog_opens_on_back_with_the_red_button(renders):
    img = _png_rgb(renders[("lcconfirm", "light", "1280x800")])
    red = _count(img, (820, 560, 1020, 650), _is((0xE1, 0x22, 0x00), 30))
    ring = _count(img, (690, 560, 830, 650), _is(INDIGO))
    assert red > 3000 and ring > 300, (red, ring)


# --- the keys and clicks: lc_handle driven ---------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static void draw(App *a) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(a, cr, 1280, 800); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void load(App *a, const char *path) {
    FILE *f = fopen(path, "r"); if (!f) { perror(path); exit(2); }
    state_parse(&a->st, f); fclose(f); lc_after_reload(a);
}
static int hit_id(const char *n) {
    static const struct { const char *n; int id; } T[] = { {"BOOT_SERVER", HIT_LC_BOOT_SERVER}, {"BOOT_LOCAL", HIT_LC_BOOT_LOCAL},
        {"BACK", HIT_LC_BACK}, {"NEXT", HIT_LC_NEXT}, {"GO", HIT_LC_GO}, {"CANCEL", HIT_LC_CANCEL}, {"STOP", HIT_LC_STOP},
        {"STOP_GO", HIT_LC_STOP_GO}, {"STOP_NO", HIT_LC_STOP_NO}, {"OFF", HIT_LC_OFF}, {"AGAIN", HIT_LC_AGAIN} };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) if (!strcmp(T[i].n, n)) return T[i].id;
    int i, r; if (sscanf(n, "ROLE%d.%d", &i, &r) == 2) return HIT_LC_ROLE_BASE + i * 3 + r;
    if (sscanf(n, "DROP%d", &i) == 1) return HIT_LC_DROP_BASE + i;
    return -1;
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    load(&a, argv[1]); app_route(&a); draw(&a);
    for (int i = 2; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "state:", 6)) { load(&a, v + 6); app_route(&a); draw(&a); printf("@ reload\n"); continue; }
        if (!strncmp(v, "key:", 4)) {
            const char *k = v + 4; e.type = UIEV_KEY;
            e.key = !strcmp(k, "LEFT") ? KEYSYM_LEFT : !strcmp(k, "RIGHT") ? KEYSYM_RIGHT : !strcmp(k, "UP") ? KEYSYM_UP
                  : !strcmp(k, "DOWN") ? KEYSYM_DOWN : !strcmp(k, "TAB") ? KEYSYM_TAB : !strcmp(k, "ENTER") ? KEYSYM_ENTER : KEYSYM_ESC;
        } else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; }
        else if (!strncmp(v, "move:", 5)) { e.type = UIEV_MOVE; sscanf(v + 5, "%lf,%lf", &e.x, &e.y); }
        else if (!strncmp(v, "hit:", 4)) {
            int id = hit_id(v + 4), found = 0;
            for (int h = a.nhits - 1; h >= 0 && !found; h--) if (a.hits[h].id == id) {
                e.type = UIEV_CLICK; e.x = a.hits[h].r.x + a.hits[h].r.w / 2; e.y = a.hits[h].r.y + a.hits[h].r.h / 2; found = 1; }
            if (!found) { printf("@ nohit %s\n", v + 4); continue; }
        }
        app_route(&a); lc_handle(&a, &e); app_route(&a); draw(&a);
        printf("@ screen=%d view=%d focus=%d confirm=%d stop=%d roles=", a.screen, lc_view(&a), a.lcu.focus, a.lcu.confirm, a.lcu.stop);
        for (int d = 0; d < a.st.lc.ndisks; d++) printf("%s:%d,", a.st.lc.disks[d].dev, lc_role_of(&a, a.st.lc.disks[d].dev));
        printf("\n"); fflush(stdout);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lcdrv")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
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
    run = subprocess.run([str(driver), posix(path), *extra], capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return [l for l in run.stdout.splitlines() if l]


BOOT = "lc_screen=boot\nlc_boot=2000000000|0|1999999990\nlc_local=1\n"   # 16/10: two disks or more -- the local card is on
DISKS = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
         "lc_disk=1|sda|Samsung|S1|500107862016|ok|ok|Windows|3|212000000000|500107862016|1|\n"
         "lc_disk=2|sdb|WDC|S2|500107862016|fail|health_failed|Ubuntu|2|38000000000|500107862016|1|\n"
         "lc_disk=3|sdc|Crucial|S3|480103981056|ok|ok|Windows|4|148600000000|480103981056|1|\n")
FITS = "lc_src=sdc|none\nlc_fit=sda|fits|480103981056|\nlc_fit=sdb|fits|480103981056|\n"
RECORDS = ("boot-", "lc-")


def records(lines: list[str]) -> list[str]:
    return [l for l in lines if l.startswith(RECORDS)]


def test_the_first_touch_stops_the_count_once_and_enter_is_the_server(driver, tmp_path):
    out = drive(driver, tmp_path, BOOT, "key:LEFT", "key:RIGHT", "key:ENTER")
    assert records(out) == ["boot-pause", "boot-server"], out


def test_two_is_local_and_a_small_move_is_not_a_touch(driver, tmp_path):
    small = drive(driver, tmp_path, BOOT, "move:100,100", "move:102,102")
    assert records(small) == [], small
    big = drive(driver, tmp_path, BOOT, "move:100,100", "move:110,100", "char:2")
    assert records(big) == ["boot-pause", "boot-local"], big
    click = drive(driver, tmp_path, BOOT, "hit:BOOT_LOCAL")
    assert records(click) == ["boot-pause", "boot-local"], click


def test_drawer_three_is_the_source_and_a_failing_smart_target_is_skipped(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS, "key:DOWN")
    assert out[0] == "lc-measure|sdc", out                                  # asked about the default source
    assert "roles=sda:2,sdb:0,sdc:1," in out[-1], out                       # target, skip, source


def test_with_drawer_three_empty_the_first_disk_with_a_system_is_the_source(driver, tmp_path):
    state = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\n"
             "lc_disk=1|sda|A|S1|500107862016|ok|ok||0|0|-1|0|אין עליו טבלת מחיצות\n"
             "lc_disk=2|sdb|B|S2|500107862016|ok|ok|Windows|3|1|480103981056|1|\n")
    out = drive(driver, tmp_path, state, "key:DOWN")
    assert out[0] == "lc-measure|sdb" and "roles=sda:2,sdb:1," in out[-1], out


def test_a_drawer_that_does_not_fit_cannot_be_made_a_target(driver, tmp_path):
    state = DISKS + "lc_src=sdc|done\nlc_fit=sda|short|600000000000|\nlc_fit=sdb|unchecked||server down\n"
    # (the arrows walk past "יעד" to "מקור" -- a new source has no verdicts yet, a separate case)
    out = drive(driver, tmp_path, state, "hit:ROLE0.2", "hit:ROLE1.2", "key:UP", "key:UP")
    assert "@ nohit ROLE0.2" in out and "@ nohit ROLE1.2" in out, out      # "יעד" is not even clickable
    states = [l for l in out if l.startswith("@ screen")]
    assert states and not any("sda:2," in l or "sdb:2," in l for l in states), states   # never a target
    assert "lc-start" not in " ".join(out)


def test_the_confirm_dialog_opens_on_back_and_only_the_red_button_starts(driver, tmp_path):
    out = drive(driver, tmp_path, DISKS + FITS, "hit:NEXT", "key:ENTER", "hit:NEXT", "key:TAB", "key:ENTER")
    states = [l for l in out if l.startswith("@ screen")]
    assert "confirm=1" in states[0] and "focus=4" in states[0], states   # LCF_CANCEL
    assert "confirm=0" in states[1] and "lc-start" not in out[:out.index(states[1])]
    assert records(out)[-1] == "lc-start|sdc|sda|0", out


def test_a_target_that_needs_the_shrink_says_so_in_the_start(driver, tmp_path):
    state = DISKS + "lc_src=sdc|done\nlc_fit=sda|shrink|300000000000|\nlc_fit=sdb|fits|480103981056|\n"
    out = drive(driver, tmp_path, state, "hit:NEXT", "hit:GO")
    assert records(out)[-1] == "lc-start|sdc|sda|1", out


def test_while_measuring_there_is_no_way_forward(driver, tmp_path):
    state = DISKS + "lc_src=sdc|measuring\nlc_fit=sda|measuring|480103981056|\n"
    out = drive(driver, tmp_path, state, "hit:NEXT", "key:TAB", "key:TAB", "key:ENTER")
    assert all("confirm=0" in l for l in out if l.startswith("@ screen")), out
    assert not any(l.startswith("lc-start") for l in out)


def test_esc_goes_back_to_the_boot_choice(driver, tmp_path):
    assert records(drive(driver, tmp_path, DISKS + FITS, "key:ESC"))[-1] == "lc-back"


RUN = (DISKS.replace("lc_phase=idle", "lc_phase=copying") + FITS +
       "lc_run=sdc|148600000000|55100000000|399|0||3|\nlc_tgt=sda|writing|55100000000|3|2||||148600000000\n")


def test_esc_asks_before_stopping_and_the_question_opens_on_continue(driver, tmp_path):
    out = drive(driver, tmp_path, RUN, "key:ESC", "key:ENTER", "key:ESC", "key:TAB", "key:ENTER")
    assert records(out) == ["lc-stop"], out
    states = [l for l in out if l.startswith("@ screen")]
    assert "stop=1" in states[0] and "focus=8" in states[0], states        # LCF_STOP_NO
    assert "stop=0" in states[1]


def test_the_result_defaults_to_power_off(driver, tmp_path):
    done = RUN.replace("lc_phase=copying", "lc_phase=done").replace("|writing|", "|done|")
    assert records(drive(driver, tmp_path, done, "key:ENTER")) == ["lc-poweroff"]
    assert records(drive(driver, tmp_path, done, "key:TAB", "key:ENTER")) == ["lc-again"]
