"""#3054: the erase warning under the disk table keeps "40.0 GiB" whole when the disk has no model.

The line is "כל הנתונים בדיסק <שם> · <גודל> · <התקן> יימחקו". With a Latin model the
whole stretch was one LTR run and read right; with no model "ללא שם" (Hebrew)
took its place, the number was ordered alone and "GiB · /dev/vda" became a run
of its own: "/dev/vda 40.0 · GiB". Every Latin part is now its own isolate
(FSI ... PDI), so the size reads "40.0 GiB" either way.

The C function itself is run: a driver includes screens_install_setup.c
(disk_warning is static) and links the rest of the GUI, as
test_native_installer_gui._disk_contents does.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from test_native_installer_gui import GUI, NATIVE

FSI, PDI = "\u2068", "\u2069"
GIB = 1073741824

DRIVER = r'''
#include "%(setup_c)s"
/* argv: MODEL PATH SIZE_BYTES, in threes */
int main(int argc, char **argv) {
    char out[480];
    for (int i = 1; i + 2 < argc; i += 3) {
        InstallDisk d; memset(&d, 0, sizeof d);
        snprintf(d.model, sizeof d.model, "%%s", argv[i]);
        snprintf(d.path, sizeof d.path, "%%s", argv[i + 1]);
        d.size_bytes = strtoull(argv[i + 2], NULL, 10);
        disk_warning(&d, out, sizeof out);
        puts(out);
    }
    return 0;
}
'''


def _warnings(tmp_path: Path, *disks: tuple[str, str, int]) -> list[str]:
    driver = tmp_path / "disk_warning.c"
    driver.write_text(DRIVER % {"setup_c": (GUI / "src" / "screens_install_setup.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "disk-warning"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\n" | grep -v -x -e src/main.c -e src/screens_install_setup.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(driver)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    args = [str(x) for disk in disks for x in disk]
    run = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@NATIVE
def test_the_size_and_the_device_are_isolated_with_and_without_a_model(tmp_path) -> None:
    got = _warnings(tmp_path, ("", "/dev/vda", 40 * GIB), ("Samsung SSD 870 EVO", "/dev/sda", 500 * GIB))
    assert got == [
        f"כל הנתונים בדיסק ללא שם · {FSI}40.0 GiB{PDI} · {FSI}/dev/vda{PDI} יימחקו",
        f"כל הנתונים בדיסק {FSI}Samsung SSD 870 EVO{PDI} · {FSI}500.0 GiB{PDI} · {FSI}/dev/sda{PDI} יימחקו",
    ]


def test_the_warning_is_what_the_disk_screen_draws() -> None:
    """The draw path calls disk_warning for a single disk; the old inline format is gone."""
    source = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    assert "else disk_warning(d,warning,sizeof warning);" in source
    assert not re.search(r'"כל הנתונים בדיסק %s · %\.1f GiB', source)
