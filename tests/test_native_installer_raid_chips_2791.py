"""#2791: the RAID choice chips on the installer's disk step drew every label
one chip to the right, and the chosen chip (the leftmost by default) empty.

raid_row centred each label with ``text_draw(x + (w - q.w)/2)``, but an RTL
block with a set width is right-aligned in that width: Pango draws the glyphs
at ``width - span`` from the point (text.c ``text_draw_r`` documents exactly
this). Seen on ESXi 09/11 (v0.91.7) with 2, 3 and 4 drives.

The driver #includes screens_install_setup.c (raid_row is static), links the
rest of the GUI, draws the row onto an image surface and, for every chip the
row registered as a hit, counts the pixels inside it (the border excluded)
that are not the chip's fill: the label's ink. Every chip must carry its own.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_native_installer_gui import GUI, NATIVE

DRIVER = r'''
#include "%(setup_c)s"
#include <stdlib.h>
/* argv: NDISKS CHOICE -- CHOICE = the RAID level index chosen (it must be allowed) */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    static App app; App *a = &app; InstallState *s = &a->install;
    a->theme = &THEME_LIGHT; a->scale = 1; a->focus = -1;
    s->active = 1; s->view = INSTALL_DISK; s->virt = INSTALL_VIRT_METAL; s->disk = 0;
    s->ndisks = atoi(argv[1]);
    for (int i = 0; i < s->ndisks; i++) {
        snprintf(s->disks[i].path, sizeof s->disks[i].path, "/dev/sd%%c", 'a' + i);
        snprintf(s->disks[i].bus, sizeof s->disks[i].bus, "sata");
        s->disks[i].size_bytes = 16000000000ULL;
    }
    s->raid = atoi(argv[2]);
    if (!install_raid_allowed(s, s->raid)) return 4;
    const int W = 900, H = 160;
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(sf);
    cairo_set_source_rgb(cr, 1, 1, 1); cairo_paint(cr);
    raid_row(a, cr, 20, 10, 860);
    cairo_surface_flush(sf);
    unsigned char *px = cairo_image_surface_get_data(sf); int stride = cairo_image_surface_get_stride(sf);
    for (int k = 0; k < a->nhits; k++) {
        int id = a->hits[k].id - HIT_INSTALL_RAID_BASE; if (id < 0 || id >= RAID_CHOICES) continue;
        Rect r = a->hits[k].r; int inked = 0;
        int x0 = (int)r.x + 4, x1 = (int)(r.x + r.w) - 4, y0 = (int)r.y + 4, y1 = (int)(r.y + r.h) - 4;
        uint32_t fill = *(uint32_t *)(px + (y0 + 1) * stride + (x0 + 1) * 4) & 0xffffff;
        for (int y = y0; y < y1; y++) for (int x = x0; x < x1; x++) {
            uint32_t v = *(uint32_t *)(px + y * stride + x * 4) & 0xffffff;
            int d = abs((int)(v >> 16) - (int)(fill >> 16)) + abs((int)((v >> 8) & 255) - (int)((fill >> 8) & 255))
                  + abs((int)(v & 255) - (int)(fill & 255));
            inked += d > 120;
        }
        printf("%%d %%d %%d\n", id, id == s->raid, inked);
    }
    cairo_destroy(cr); cairo_surface_destroy(sf);
    return 0;
}
'''

NONE, R1, R1S, R5, R6, R10 = range(6)


@pytest.fixture(scope="module")
def chips(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("raid-chips")
    driver = tmp / "raid_chips.c"
    driver.write_text(DRIVER % {"setup_c": (GUI / "src" / "screens_install_setup.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp / "raid-chips"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\n" | grep -v -x -e src/main.c -e src/screens_install_setup.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp), str(driver)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")

    def run(disks: int, choice: int) -> dict[int, tuple[bool, int]]:
        out = subprocess.run([str(binary), str(disks), str(choice)], capture_output=True, text=True,
                             timeout=60, env=env, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, (out.returncode, out.stderr)
        rows = [tuple(map(int, line.split())) for line in out.stdout.splitlines()]
        return {cid: (bool(on), inked) for cid, on, inked in rows}
    return run


@NATIVE
@pytest.mark.parametrize("disks,choice,count", [
    (2, R1, 2), (2, NONE, 2),          # r2-01.png: the chosen RAID1 chip was empty
    (3, R5, 4), (3, R1S, 4),           # r5-01.png: RAID5 drawn in "RAID1 + רזרבי"
    (4, R10, 6), (4, R6, 6),           # r10-01.png: RAID10 drawn in RAID6's chip
])
def test_every_chip_carries_its_own_label(chips, disks, choice, count):
    got = chips(disks, choice)
    assert len(got) == count, got
    assert got[choice][0] is True
    empty = sorted(cid for cid, (_on, inked) in got.items() if inked < 15)
    assert not empty, f"chips without a label: {empty} -- {got}"
