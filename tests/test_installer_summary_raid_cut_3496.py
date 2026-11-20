"""#3496: the installer's summary row for a RAID array never loses a member.

`value` was 520 bytes and the member list up to 2559: with a dozen drives the row was cut after the
first few, the compiler said so (-Wformat-truncation, hidden in tests/test_drawer_fill_screen_3064.py by
-Wno-error=format-truncation), and a cut could land inside a Hebrew letter. The row now holds
INSTALL_MAX_DISKS members whole. Real summary(), real drawn text, like test_installer_summary_net_2129.py.

Negative control: native-gui/src/screens_install_final.c from the base.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_installer_summary_net_2129 import GUI, NATIVE

DISKS = 16   # INSTALL_MAX_DISKS (install_state.h)

DRIVER = r'''
#include "%(final_c)s"
#include <stdlib.h>
void __real_text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c);
static int log_draws;
void __wrap_text_draw_r(cairo_t *cr, Text *t, double x_right, double y, Rgb c) {
    if (log_draws) printf("draw\t%%s\n", pango_layout_get_text(t->layout));
    __real_text_draw_r(cr, t, x_right, y, c);
}
static App app;
int main(void) {
    InstallState *s = &app.install; app.theme = &THEME_LIGHT;
    s->ndisks = INSTALL_MAX_DISKS; s->disk = 0; s->raid = RAID_10;
    for (int i = 0; i < s->ndisks; i++) {
        snprintf(s->disks[i].path, sizeof s->disks[i].path, "/dev/sd%%c", 'a' + i);
        snprintf(s->disks[i].bus, sizeof s->disks[i].bus, "sata");
        s->disks[i].slot = i + 1;
        snprintf(s->disks[i].slotname, sizeof s->disks[i].slotname, "מגירה מספר %%d", i + 1);
    }
    snprintf(s->hostname, sizeof s->hostname, "imagesrv-ta"); snprintf(s->timezone, sizeof s->timezone, "Asia/Jerusalem");
    snprintf(s->admin_user, sizeof s->admin_user, "admin");
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1280, 800);
    cairo_t *cr = cairo_create(sf);
    InstallLayout l = { { 0, 0, 1030, 800 }, { 135, 105, 760, 615 }, { 0, 736, 1030, 64 }, { 1030, 0, 250, 800 }, 895 };
    log_draws = 1; summary(&app, cr, l); log_draws = 0;
    cairo_destroy(cr); cairo_surface_destroy(sf); return 0;
}
'''


@NATIVE
def test_a_full_raid_array_is_listed_whole(tmp_path: Path) -> None:
    source = tmp_path / "raid_driver.c"
    source.write_text(DRIVER % {"final_c": (GUI / "src" / "screens_install_final.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "raid-driver"
    command = (
        'set -e; cd "$1"; flags="-O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\n" | grep -v -x -e src/main.c -e src/screens_install_final.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -Wl,--wrap=text_draw_r -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    build = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(source)],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary)], capture_output=True, text=True, encoding="utf-8", timeout=60, env=env,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, (run.returncode, run.stderr)
    rows = [line[5:] for line in run.stdout.splitlines() if line.startswith("draw\t") and f"{DISKS} כוננים" in line]
    assert len(rows) == 1, run.stdout
    row = rows[0]
    for i in range(DISKS):
        assert f"מגירה מספר {i + 1}" in row and f"/dev/sd{chr(ord('a') + i)}" in row, f"member {i + 1} is missing: {row}"
    assert row.rstrip("\u2069").endswith(")"), row   # ends on a whole member, not mid-word
