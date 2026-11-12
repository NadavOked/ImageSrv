"""#2848 (Nadav 11/11, the work cloner): "דיסק 3 לא נבחר כיעד, בלי סיבה".

Drawer 3 (SATA 2) is the proposed source on the local clone screen (#1874). On the
source card "יעד" and "דלג" were drawn like live buttons, and a click on them did
nothing: ``role_click`` returns for the source ("the source changes by choosing
another"). Nadav's rule: a disk that cannot be a target says why on its card.

The real screen, through the production sources (``test_lc_card_fit_2205``'s driver):
the source card adds no click target for "יעד"/"דלג" (the button is grey) and its
name block says how to make it a target. The other two cards keep all three buttons.
"""

import os
import subprocess

import pytest

from test_cloner_gui import BASH, GUI, posix
from test_lc_card_fit_2205 import ROLE_BASE, ROLE_END, fit_driver  # noqa: F401 -- fixture
from test_native_local_clone import pytestmark  # noqa: F401

LCR_SKIP, LCR_SOURCE, LCR_TARGET = 0, 1, 2   # localclone.h
REASON = "כדי לבחור אותו כיעד — בחרו מקור אחר"

#: the work cloner, 11/11: three healthy disks, drawer 3 the source -- no CRC note (#2846)
WORK = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb=0\n"
        "lc_disk=1|sda|Samsung SSD 860 EVO 500GB|S1|500107862016|ok|ok|Windows|4|62000000000|62000000000|1||1|SATA||\n"
        "lc_disk=2|sdb|SAMSUNG MZ7TY256HDHP|S2|256060514304|ok|ok|Windows|4|62000000000|62000000000|1||2|SATA||\n"
        "lc_disk=3|sdc|SAMSUNG MZ7LN256HCHP|S3|256060514304|ok|ok|Windows|4|62000000000|62000000000|1||3|SATA||\n"
        "lc_src=sdc|done|62000000000|\nlc_fit=sda|fits|62000000000|\nlc_fit=sdb|fits|62000000000|\n")


def _draw(binary, tmp_path, size="1024x768"):
    w, h = size.split("x")
    path = tmp_path / "state.txt"
    path.write_text(WORK, encoding="utf-8", newline="\n")
    run = subprocess.run([str(binary), posix(path), w, h, posix(tmp_path / "card.png"), "2"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr + run.stdout
    roles = {int(l.split()[1]) - ROLE_BASE for l in run.stdout.splitlines()
             if l.startswith("H ") and ROLE_BASE <= int(l.split()[1]) < ROLE_END}
    texts = [l.split(" ", 5)[5] for l in run.stdout.splitlines() if l.startswith("T ") and len(l.split(" ", 5)) > 5]
    return {i: {r for r in (LCR_SKIP, LCR_SOURCE, LCR_TARGET) if i * 3 + r in roles} for i in range(3)}, texts


def test_the_source_card_greys_target_and_skip_and_says_why(fit_driver, tmp_path):
    roles, texts = _draw(fit_driver, tmp_path)
    assert roles[2] == {LCR_SOURCE}, roles                       # "יעד"/"דלג" are not live on the source
    assert any(REASON in t for t in texts), texts


def test_the_other_cards_keep_their_three_buttons(fit_driver, tmp_path):
    """The guard: the fix greys the source's buttons only."""
    roles, texts = _draw(fit_driver, tmp_path)
    assert roles[0] == roles[1] == {LCR_SKIP, LCR_SOURCE, LCR_TARGET}, roles
    assert sum(REASON in t for t in texts) == 1, texts


# --- the coordinator's reading of Nadav's photo (11/11): the source moved to drawer 2, drawer 3
# drawn "דלג", and "יעד" on drawer 3 did nothing. The sequence, through lc_handle and the real
# state reload (main.c's loop: state_parse -> lc_after_reload -> app_draw -> a click).

SEQ_DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static Rect R[4096]; static int RID[4096], NR;
void __real_hit_add(App *, Rect, int);
void __wrap_hit_add(App *a, Rect r, int id) { if (NR < 4096) { R[NR] = r; RID[NR++] = id; } __real_hit_add(a, r, id); }
static void frame(App *a, const char *path) {
    State keep = a->st; FILE *f = fopen(path, "r"); if (!f) exit(3);
    state_parse(&a->st, f); fclose(f); (void)keep; lc_after_reload(a); app_route(a);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1024, 768);
    cairo_t *cr = cairo_create(s); NR = 0; app_draw(a, cr, 1024, 768); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void roles(App *a, const char *tag) {
    printf("%s", tag);
    for (int i = 0; i < a->st.lc.ndisks; i++) printf(" %d:%d", i, lc_role_of(a, a->st.lc.disks[i].dev));
    printf("\n");
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1; a.fixed_clock = 1;
    for (int k = 1; k < argc; k++) {
        if (!strncmp(argv[k], "state=", 6)) { frame(&a, argv[k] + 6); roles(&a, "after-reload"); continue; }
        int id = atoi(argv[k] + 6), hit = -1;   /* click=<hit id> */
        for (int i = 0; i < NR; i++) if (RID[i] == id) hit = i;
        if (hit < 0) { printf("no-hit %d\n", id); continue; }
        Event e; memset(&e, 0, sizeof e); e.type = UIEV_CLICK; e.x = R[hit].x + R[hit].w / 2; e.y = R[hit].y + R[hit].h / 2;
        lc_handle(&a, &e); roles(&a, argv[k]);
    }
    return 0;
}
'''

#: the agent after the person chose drawer 2 (sdb): it measured sdb, so the verdicts are about sdb
WORK_SRC2 = WORK.replace("lc_src=sdc|done|62000000000|\nlc_fit=sda|fits|62000000000|\nlc_fit=sdb|fits|62000000000|\n",
                         "lc_src=sdb|done|62000000000|\nlc_fit=sda|fits|62000000000|\nlc_fit=sdc|fits|62000000000|\n")


@pytest.fixture(scope="module")
def seq_driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("seq2848")
    driver, binary = d / "driver.c", d / "driver"
    driver.write_text(SEQ_DRIVER, encoding="utf-8", newline="\n")
    sources = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert sources.returncode == 0, sources.stderr
    srcs = [posix(GUI / s) for s in sources.stdout.split() if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c",
        'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
        '-Wl,--wrap=hit_add -o "$2" "$3" "${@:4}" $(pkg-config --libs pangocairo cairo libdrm) -lm',
        "_", posix(GUI), posix(binary), posix(driver), *srcs],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _seq(binary, tmp_path, *steps):
    args = []
    for i, step in enumerate(steps):
        if isinstance(step, str):
            p = tmp_path / f"s{i}.txt"; p.write_text(step, encoding="utf-8", newline="\n"); args.append(f"state={posix(p)}")
        else:
            disk, role = step; args.append(f"click={ROLE_BASE + disk * 3 + role}")
    run = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8", timeout=120,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr + run.stdout
    got = []
    for line in run.stdout.splitlines():   # "no-hit N": the button is not clickable -- the roles stay as they were
        got.append(dict(got[-1]) if line.startswith("no-hit") else dict(x.split(":") for x in line.split()[1:]))
    return got, run.stdout


def test_source_moved_to_drawer_2_then_drawer_3_becomes_a_target(seq_driver, tmp_path):
    """Default: drawer 3 the source. "מקור" on drawer 2, the agent's next state (sdb measured),
    then "יעד" on drawer 3: drawer 3 is a target, drawer 2 the source."""
    got, out = _seq(seq_driver, tmp_path, WORK, (1, LCR_SOURCE), WORK_SRC2, (2, LCR_SKIP), WORK_SRC2,
                    (2, LCR_TARGET), WORK_SRC2)
    assert got[0] == {"0": "2", "1": "2", "2": "1"}, out          # the proposal: 3 the source
    assert got[-1] == {"0": "2", "1": "1", "2": "2"}, out         # 2 the source, 3 a target
