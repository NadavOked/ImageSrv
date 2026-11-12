"""#2849: the build machine's direct send shows the cloners' drawers -- the cloner's own card.

At work (11/11) Nadav stood at the BUILD machine during a direct send. Its
screen was the capture-style progress card: one bar of what was read, and
nothing about the targets -- which drawer, how far each one got, which one
failed (drawer 3, "איטי"). Nadav's decision: on a direct send the build machine
and the cloner show the same card with the same data; only the title differs.

* the kiosk (``directgui.sh``) turns GET /room's drawer_list of the round's
  cloners into the cloner's own records: ``cloner_group=`` per cloner, then its
  ``drawer=`` rows. A room that could not be read is ``direct_targets=unread``,
  never an empty list, and never a failed state file;
* the native screen routes a running direct send that has drawers to
  ``SCREEN_CLONER``, one cage per cloner, no "בטל…" (the build machine cancels
  no cloner's drawer).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_cloner_gui import GUI, _pkgconfig

ROOM = {
    "group_label": "חדר שיכפול",
    "round": {"image_name": "Win11-Lab", "target_slots": [{"mac": "64:31:50:42:b1:9c", "ports": [1, 2, 3]}]},
    "machines": [
        {"name": "1", "mac": "64:31:50:42:b1:9c", "drawer_list": [
            {"dev": "sdc", "port": 1, "drawer": 1, "state": "writing", "size_bytes": 500107862016, "bytes_written": 40000000000,
             "bytes_total": 82587750957, "error": None, "rate_bps": 7200000.5, "eta_s": 5800},
            {"dev": "sda", "port": 2, "drawer": 2, "state": "failed", "bytes_written": 0, "bytes_total": 82587750957,
             "error": "איטי — 96 MB/s, פחות מחצי מ-209 MB/s", "rate_bps": None, "eta_s": None},
            {"dev": "sdb", "port": 3, "drawer": 3, "state": "writing", "bytes_written": 41000000000,
             "bytes_total": 82587750957, "error": None, "rate_bps": 7100000, "eta_s": 5900},
            {"dev": "sdd", "port": 4, "drawer": None, "state": None, "selected": False}]},
        {"name": "2", "mac": "aa:bb:cc:dd:ee:02", "drawer_list": [
            {"dev": "sdb", "port": 1, "drawer": 1, "state": "writing", "bytes_written": 5}]},
    ],
}
STATION = {"known": True, "disks": [], "task": {"type": "direct_send", "state": "running", "disk": "sda"}}


def records(tmp_path: Path, station: dict | str, mode: str = "direct", room: dict | None = ROOM,
            get_ok: bool = True) -> list[str]:
    gui = tmp_path / "gui"; gui.mkdir(exist_ok=True)
    (gui / "station.json").write_text(station if isinstance(station, str) else json.dumps(station),
                                      encoding="utf-8", newline="\n")
    (gui / "mode").write_text(mode + "\n", newline="\n")
    if room is not None:
        (gui / "room.json").write_text(json.dumps(room, ensure_ascii=False), encoding="utf-8", newline="\n")
    get = "gui_get() { return 0; }; " if get_ok else "gui_get() { return 1; }; "
    out = sh(f"export GUI_DIR={posix(gui)!r}; {get}. {posix(AGENT)}/lib/gui/directgui.sh; direct_gui_records")
    return out.splitlines()


@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
def test_the_cloners_drawers_become_the_cloners_own_records(tmp_path):
    lines = records(tmp_path, STATION)
    assert lines[0] == "cloner=Win11-Lab"
    # only the round's target cloner (target_slots), only its target drawers (sdd: no state, not selected)
    assert lines[1:] == [
        "cused=sdc|500107862016|-1",                       # one cloner: its sizes (the other drawers have none)
        "cloner_group=1|64:31:50:42:b1:9c",
        "drawer=1|sdc|writing|40000000000|82587750957||7200000|5800|1|SATA 0||",
        "drawer=2|sda|failed|0|82587750957|איטי — 96 MB/s, פחות מחצי מ-209 MB/s|-1|-1|2|SATA 1||",
        "drawer=3|sdb|writing|41000000000|82587750957||7100000|5900|3|SATA 2||",
    ]


@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
def test_two_cloners_two_groups(tmp_path):
    room = dict(ROOM, round={"image_name": "x"})            # no target_slots: every cloner with targets
    lines = records(tmp_path, STATION, room=room)
    groups = [l for l in lines if l.startswith("cloner_group=")]
    assert groups == ["cloner_group=1|64:31:50:42:b1:9c", "cloner_group=2|aa:bb:cc:dd:ee:02"]
    assert not any(l.startswith("cused=") for l in lines)   # two cloners: a dev is not one disk
    assert lines[-1] == "drawer=1|sdb|writing|5|0||-1|-1|1|SATA 0||"


@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
def test_not_a_direct_send_writes_nothing(tmp_path):
    assert records(tmp_path, dict(STATION, task={"type": "capture", "state": "running"})) == []
    assert records(tmp_path, dict(STATION, task={"type": "direct_send", "state": "done"})) == []
    assert records(tmp_path, dict(STATION, task=None)) == []


@requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="directgui.sh קורא ל-jq")
def test_a_room_that_was_not_read_is_said_never_an_empty_list(tmp_path):
    # the send was opened from the console: the GUI is not on the room screen and GET /room fails
    assert records(tmp_path, STATION, mode="menu", room=None, get_ok=False) == ["direct_targets=unread"]
    # a room that is not a room
    assert records(tmp_path, STATION, room={"machines": "x"}) == ["direct_targets=unread"]
    # a station file the check itself cannot read: not "not a direct send"
    assert records(tmp_path, "{broken", room=None) == ["direct_targets=unread"]


def test_the_kiosk_loads_it_and_the_state_writer_calls_it():
    bridge = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/directgui.sh"' in bridge
    assert "direct_gui_records" in (AGENT / "lib" / "gui" / "guistate.sh").read_text(encoding="utf-8")


# --- the native screen ----------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "canceldlg.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2;
    state_parse(&a.st, f); fclose(f); app_route(&a);
    CancelPick p[MAX_CANCEL_PICKS];
    /* only fields every version has: the negative control builds this against the code before #2849 */
    printf("@ screen %d cloner %d progress %d picks %d drawers %d\n", a.screen, SCREEN_CLONER, SCREEN_PROGRESS,
           cloner_cancel_picks(&a, p, MAX_CANCEL_PICKS), a.st.ndrawers);
    for (int i = 2; i + 2 < argc; i += 3) {
        int W = atoi(argv[i]), H = atoi(argv[i + 1]);
        cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
        cairo_t *cr = cairo_create(s); app_draw(&a, cr, W, H); cairo_destroy(cr);
        cairo_surface_write_to_png(s, argv[i + 2]); cairo_surface_destroy(s);
        int bay = 0;
        for (int h = 0; h < a.nhits; h++) bay += a.hits[h].id >= HIT_BAY_CANCEL_BASE && a.hits[h].id < HIT_BAY_CANCEL_BASE + 16;
        printf("@ drawn %dx%d screen %d cancel-targets %d\n", W, H, a.screen, bay);
    }
    return 0;
}
'''

STATE = ("task=running\ntask_name=\ntask_disk=sda\ntask_direct=1\nbytes=40000000000\npct=48\npartition=3\n"
         "machine_name=BUILD1\ncloner=Win11-Lab\nrb_mode=full\n"
         "cused=sdc|500107862016|-1\ncused=sda|256060514304|-1\ncused=sdb|256060514304|-1\n"
         "cloner_group=1|64:31:50:42:b1:9c\n"
         "drawer=1|sdc|writing|40000000000|82587750957||7200000|5800|1|SATA 0||\n"
         "drawer=2|sda|failed|0|82587750957|איטי — 96 MB/s, פחות מחצי מ-209 MB/s|-1|-1|2|SATA 1||\n"
         "drawer=3|sdb|writing|41000000000|82587750957||7100000|5900|3|SATA 2||\n"
         "updated=1794417933\n")


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("builddirect")
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


def drive(driver: Path, tmp_path: Path, state: str, *sizes: tuple[int, int]) -> list[list[str]]:
    path = tmp_path / "state.txt"; path.write_text(state, encoding="utf-8", newline="\n")
    args = [x for w, h in sizes for x in (str(w), str(h), posix(tmp_path / f"build-direct-{w}x{h}.png"))]
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    run = subprocess.run([str(driver), posix(path), *args], capture_output=True, text=True, encoding="utf-8",
                         timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return [line.split()[1:] for line in run.stdout.splitlines() if line.startswith("@ ")]


@requires_native(("cc", shutil.which("cc") or shutil.which("gcc")),
                 ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")), ("bash", BASH),
                 why="native-gui נבנה על המעבדה בלבד")
def test_a_direct_send_with_drawers_is_the_cloners_screen(driver, tmp_path):
    out = drive(driver, tmp_path, STATE, (1280, 768), (1024, 768), (800, 600))
    head = out[0]
    assert head[1] == head[3], f"routed to {head[1]}, not SCREEN_CLONER ({head[3]})"
    assert head[7] == "0", "the build machine offers no cancel of a cloner's drawer"
    assert head[9] == "3"
    for o in out:
        if o[0] == "drawn":
            assert o[3] == head[3] and o[5] == "0", o


@requires_native(("cc", shutil.which("cc") or shutil.which("gcc")),
                 ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")), ("bash", BASH),
                 why="native-gui נבנה על המעבדה בלבד")
def test_without_drawers_it_is_the_progress_card_as_before(driver, tmp_path):
    state = "\n".join(l for l in STATE.splitlines() if not l.startswith(("drawer=", "cloner_group="))) + "\n"
    head = drive(driver, tmp_path, state)[0]
    assert head[1] == head[5], f"routed to {head[1]}, not SCREEN_PROGRESS ({head[5]})"
    # a room that was not read is said on the cloner's screen, not hidden behind the progress card
    head = drive(driver, tmp_path, state + "direct_targets=unread\n")[0]
    assert head[1] == head[3]


@requires_native(("cc", shutil.which("cc") or shutil.which("gcc")),
                 ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")), ("bash", BASH),
                 why="native-gui נבנה על המעבדה בלבד")
def test_two_cloners_draw_one_cage_each(driver, tmp_path):
    state = STATE.replace("updated=", "cloner_group=2|aa:bb:cc:dd:ee:02\n"
                          "drawer=1|sdb|done|82587750957|82587750957||-1|-1|1|SATA 0||\nupdated=")
    out = drive(driver, tmp_path, state, (1280, 768), (1024, 768))
    assert out[0][1] == out[0][3] and out[0][9] == "4"
    assert [o[3] for o in out if o[0] == "drawn"] == [out[0][3]] * 2
