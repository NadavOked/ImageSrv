"""#3051: the installer tells identical disks apart -- the stick's "דיסק N" and the serial.

Three drawers of one model look the same by model and size, and the disk chosen is erased. The
engine's inventory now carries, per disk, ``serial=`` (disk_serial), ``slot=`` (the drawer of the
clone stick's mapping, $RUN_DIR/drawer_slots) and ``slotname=`` (that connection's name,
$RUN_DIR/slot_names). The fields are there only when known: no mapping, a file that does not say
"ok", or a connection that is not in it is *no* slot -- never a number guessed from /dev/sdX.
The GUI parses them, draws "דיסק N" and the serial on the disk step, and says the same in the summary.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_installer_engine import LINUX_ONLY, _command_shims, _machine, _run
from test_native_installer_gui import GUI, NATIVE
from test_native_installer_raid_1989 import NATIVE as NATIVE_CAIRO
from test_native_installer_raid_1989 import SRC

FSI, PDI = "\u2068", "\u2069"


# --- the engine's inventory -----------------------------------------------------------------------

def _wired(box, key_dir: str, serial: str | None = "WD-WX1234567890") -> None:
    """The disk sda behind `key_dir` (a sysfs-like path whose ata/PCI part is the connection)."""
    real = box["root"] / "sys" / "devices" / key_dir
    real.mkdir(parents=True)
    old = box["root"] / "sys" / "block" / "sda" / "device"
    for item in old.iterdir():
        item.rename(real / item.name)
    old.rmdir()
    old.symlink_to(real)
    if serial is not None:
        (real / "serial").write_text(serial + "\n", newline="\n")


def _inventory(box, tmp_path: Path, drawer_slots: str | None = None, slot_names: str | None = None) -> str:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    box["env"]["RUN_DIR"] = str(run)
    if drawer_slots is not None:
        (run / "drawer_slots").write_text(drawer_slots, newline="\n")
    if slot_names is not None:
        (run / "slot_names").write_text(slot_names, newline="\n")
    disk = box["root"] / "sys" / "block" / "sda"
    for part in ("sda1", "sda2"):
        (disk / part).mkdir()
        (disk / part / "partition").write_text("1\n", newline="\n")
    _command_shims(tmp_path, box["env"])
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


SATA = "pci0000:00/0000:00:1f.2/ata2/host1/target1:0:0/1:0:0:0"
NVME = "pci0000:00/0000:00:1d.0/0000:03:00.0/nvme/nvme0"


@LINUX_ONLY
def test_the_inventory_carries_the_serial_the_slot_and_its_name(tmp_path):
    box = _machine(tmp_path)
    _wired(box, SATA)
    line = _inventory(box, tmp_path, "ok\n1|sata:1\n3|sata:2\n", "ok\n2|Backup (3)\n")
    assert line.endswith("|iso=0|smart=unknown|serial=WD-WX1234567890|slot=3|slotname=Backup (3)"), line


@LINUX_ONLY
def test_a_mapped_connection_without_a_name_has_a_slot_only(tmp_path):
    box = _machine(tmp_path)
    _wired(box, SATA)
    line = _inventory(box, tmp_path, "ok\n3|sata:2\n", "ok\n7|Other\n")
    assert line.endswith("|serial=WD-WX1234567890|slot=3"), line


@LINUX_ONLY
@pytest.mark.parametrize(("slots", "why"), [
    (None, "no mapping file: the order of /dev/sdX is not a drawer number"),
    ("ok\n1|sata:5\n", "the disk's connection is not in the mapping"),
    ("1|sata:2\n", "a file that does not say ok is not an answer"),
    ("ok\nx|sata:2\n", "a drawer that is not a number"),
])
def test_no_mapping_no_slot_only_the_serial(tmp_path, slots, why):
    box = _machine(tmp_path)
    _wired(box, SATA)
    line = _inventory(box, tmp_path, slots, "ok\n2|Backup\n")
    assert line.endswith("|smart=unknown|serial=WD-WX1234567890"), (why, line)


@LINUX_ONLY
def test_an_nvme_is_mapped_by_its_controller(tmp_path):
    box = _machine(tmp_path)
    _wired(box, NVME, "NV0001")
    line = _inventory(box, tmp_path, "ok\n1|sata:1\n2|nvme:0000:03:00.0\n", "ok\n")
    assert line.endswith("|serial=NV0001|slot=2"), line


@LINUX_ONLY
def test_a_disk_with_no_serial_has_no_serial_field(tmp_path):
    box = _machine(tmp_path)
    _wired(box, SATA, None)
    line = _inventory(box, tmp_path, "ok\n3|sata:2\n")
    assert "serial=" not in line and line.endswith("|smart=unknown|slot=3"), line


@LINUX_ONLY
def test_a_separator_in_a_serial_or_name_cannot_split_the_line(tmp_path):
    box = _machine(tmp_path)
    _wired(box, SATA, "AB|slot=9")
    line = _inventory(box, tmp_path, "ok\n3|sata:2\n", "ok\n2|a|b\n")
    assert "|serial=AB slot=9|slot=3|slotname=a b" in line and line.count("|slot=") == 1, line


@LINUX_ONLY
def test_the_install_medium_is_not_identified(tmp_path):
    box = _machine(tmp_path)
    _wired(box, SATA)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    line = _inventory(box, tmp_path, "ok\n3|sata:2\n")
    assert line.endswith("|iso=1|smart=unknown"), line


# --- the GUI ---------------------------------------------------------------------------------------

PARSE = r'''
#include "%(install_c)s"
#include <stdio.h>
int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;
/* argv: INVENTORY-FILE -- one line per disk: serial, slotname, slot */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    InstallState *s = &app.install; memset(s, 0, sizeof *s);
    static char text[8192]; FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    size_t n = fread(text, 1, sizeof text - 1, f); text[n] = 0; fclose(f);
    parse_lines(s, text, "inventory");
    for (int i = 0; i < s->ndisks; i++)
        printf("[%%s] [%%s] %%d\n", s->disks[i].serial, s->disks[i].slotname, s->disks[i].slot);
    return 0;
}
'''

LABELS = r'''
#include "%(final_c)s"
/* argv: SLOT SLOTNAME SERIAL MODEL PATH, in fives -- the disk step's label, then the summary's line */
int main(int argc, char **argv) {
    char label[160], line[520];
    for (int i = 1; i + 4 < argc; i += 5) {
        InstallDisk d; memset(&d, 0, sizeof d);
        d.slot = atoi(argv[i]); snprintf(d.slotname, sizeof d.slotname, "%%s", argv[i + 1]);
        snprintf(d.serial, sizeof d.serial, "%%s", argv[i + 2]); snprintf(d.model, sizeof d.model, "%%s", argv[i + 3]);
        snprintf(d.path, sizeof d.path, "%%s", argv[i + 4]);
        install_disk_slot_label(&d, label, sizeof label); summary_disk(&d, line, sizeof line);
        printf("%%s\n%%s\n", label, line);
    }
    return 0;
}
'''


@NATIVE_CAIRO
def test_the_inventory_fields_reach_the_disk(tmp_path):
    source = tmp_path / "parse.c"
    source.write_text(PARSE % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp_path / "parse"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    inv = tmp_path / "inv"
    base = "|model=M|size=1|bus=sata|removable=0|has=empty|iso=0|smart=passed"
    inv.write_text(f"disk=/dev/sda{base}|serial=Z3T0AB12|slot=1|slotname=Top\n"
                   f"disk=/dev/sdb{base}|serial=Z3T0CD34\n"          # no mapping: a serial alone
                   f"disk=/dev/sdc{base}\n"                           # an older bridge: nothing
                   f"disk=/dev/sdd{base}|slot=0|slotname=\n"          # 0 = not in the mapping
                   f"disk=/dev/sde{base}|slot=500\n", encoding="utf-8", newline="\n")   # out of range = no slot
    got = subprocess.run([str(binary), str(inv)], capture_output=True, text=True, timeout=30,
                         stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    assert got.stdout.splitlines() == ["[Z3T0AB12] [Top] 1", "[Z3T0CD34] [] 0", "[] [] 0", "[] [] 0", "[] [] 0"]


def _labels(tmp_path: Path, *disks: tuple) -> list[str]:
    driver = tmp_path / "labels.c"
    driver.write_text(LABELS % {"final_c": (GUI / "src" / "screens_install_final.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "labels"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_final.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(driver)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run([str(binary), *[str(x) for d in disks for x in d]], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout.split("\n")[:-1]


@NATIVE
def test_the_label_and_the_summary_line_name_the_disk(tmp_path):
    got = _labels(tmp_path,
                  (2, "", "Z3T0CD34", "ST500DM002-1BD142", "/dev/sdb"),     # the mapping, no name
                  (3, "Backup-2", "", "ST500DM002-1BD142", "/dev/sdc"),     # the stick's own name replaces "דיסק N"
                  (0, "", "Z3T0EF56", "ST500DM002-1BD142", "/dev/sda"),     # not in the mapping: no number, the serial only
                  (0, "", "", "", "/dev/vda"))                              # nothing known: as before
    m = f"{FSI}ST500DM002-1BD142{PDI}"
    assert got == [
        "דיסק 2", f"דיסק 2 · {m} · {FSI}/dev/sdb{PDI} · {FSI}SN Z3T0CD34{PDI}",
        f"{FSI}Backup-2{PDI}", f"{FSI}Backup-2{PDI} · {m} · {FSI}/dev/sdc{PDI}",
        "", f"{m} · {FSI}/dev/sda{PDI} · {FSI}SN Z3T0EF56{PDI}",
        "", f"{FSI}—{PDI} · {FSI}/dev/vda{PDI}",
    ]


def test_the_disk_row_draws_the_label_and_the_serial():
    source = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    assert "install_disk_slot_label(d,slot,sizeof slot)" in source
    assert 'snprintf(ident,sizeof ident,"%s · SN %s",d->path,d->serial)' in source
    assert "double rh=disk_row_h(s);" in source
