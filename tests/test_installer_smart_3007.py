"""#3007: the installer warns when the disk chosen for the server already failed SMART.

The work server was installed (v0.92.0) on a disk whose SMART said FAILED from
its first boot. The engine's inventory now carries ``smart=passed|failed|unknown``
per disk (installer/lib/disks.sh ``disk_smart``, ``smartctl -H`` only), and the
disk step says so in red for the chosen disk or any RAID member
(native-gui install_raid.h ``install_smart_failed``). "failed" only on
smartctl's DISK FAILING bit, "passed" only on its PASSED / OK line; no
smartctl, no SMART (a VM), a disk that would not open or a hang are
"unknown" -- never "passed" (principle 5), and never a warning either.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_installer_engine import LINUX_ONLY, _command_shims, _machine, _run, _shim
from test_native_installer_raid_1989 import NATIVE, SRC

FAILED_OUT = """smartctl 7.4 2023-08-01 r5530 [x86_64-linux-6.12.0-amd64] (local build)
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: FAILED!
Drive failure expected in less than 24 hours. SAVE ALL DATA.
"""
PASSED_OUT = "=== START OF READ SMART DATA SECTION ===\nSMART overall-health self-assessment test result: PASSED\n"
SCSI_OK = "=== START OF READ SMART DATA SECTION ===\nSMART Health Status: OK\n"
VIRTUAL = "Device: VMware   Virtual disk     Version: 2.0\nSMART support is: Unavailable - device lacks SMART capability.\n"


def _inventory(tmp_path: Path, smartctl: str) -> str:
    box = _machine(tmp_path)
    disk = box["root"] / "sys" / "block" / "sda"
    for part in ("sda1", "sda2"):
        (disk / part).mkdir()
        (disk / part / "partition").write_text("1\n", newline="\n")
    _command_shims(tmp_path, box["env"])
    seen = tmp_path / "smartctl-calls"
    _shim(tmp_path / "bin" / "smartctl", f'printf "%s\\n" "$*" >> "{seen}"\n{smartctl}\n')
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    assert seen.read_text().split() == ["-H", f"{box['env']['DEVROOT']}/sda"], "read-only: -H, the one disk"
    return got.stdout.strip().rsplit("|", 1)[1]


@LINUX_ONLY
@pytest.mark.parametrize(("smartctl", "smart"), [
    (f"cat <<'EOF'\n{FAILED_OUT}EOF\nexit 8", "smart=failed"),
    (f"cat <<'EOF'\n{FAILED_OUT}EOF\nexit 12", "smart=failed"),        # some SMART reads failed too
    (f"cat <<'EOF'\n{PASSED_OUT}EOF\nexit 0", "smart=passed"),
    (f"cat <<'EOF'\n{SCSI_OK}EOF\nexit 0", "smart=passed"),
    (f"cat <<'EOF'\n{PASSED_OUT}EOF\nexit 32", "smart=passed"),        # an attribute failed in the past: not FAILING
    (f"cat <<'EOF'\n{VIRTUAL}EOF\nexit 4", "smart=unknown"),
    ("echo 'Smartctl open device: /dev/sda failed: No such device' >&2; exit 2", "smart=unknown"),
    (f"cat <<'EOF'\n{PASSED_OUT}EOF\nexit 2", "smart=unknown"),        # PASSED text but the open failed
    ("echo 'no verdict line'; exit 0", "smart=unknown"),
])
def test_the_inventory_says_each_disks_smart(tmp_path, smartctl, smart):
    assert _inventory(tmp_path, smartctl) == smart


@LINUX_ONLY
def test_a_smartctl_that_hangs_is_unknown_not_failed(tmp_path):
    """timeout's 124 has bit 3 set: it must not read as DISK FAILING."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    _shim(tmp_path / "bin" / "timeout", 'exit 124\n')
    _shim(tmp_path / "bin" / "smartctl", "exit 0\n")
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip().endswith("|smart=unknown"), got.stdout


@LINUX_ONLY
def test_the_install_medium_is_not_probed(tmp_path):
    box = _machine(tmp_path)
    box["mounts"].write_text("/dev/sda1 /cdrom iso9660 ro 0 0\n", newline="\n")
    disk = box["root"] / "sys" / "block" / "sda"
    (disk / "sda1").mkdir()
    (disk / "sda1" / "partition").write_text("1\n", newline="\n")
    _command_shims(tmp_path, box["env"])
    seen = tmp_path / "smartctl-calls"
    _shim(tmp_path / "bin" / "smartctl", f'echo "$*" >> "{seen}"; exit 8\n')
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    assert "|iso=1|smart=unknown" in got.stdout and not seen.exists()


# --- the disk step ---------------------------------------------------------------------------------

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;

/* argv: INVENTORY-FILE DISK [RAID] -- prints install_smart_failed after choosing */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app; InstallState *s = &a->install;
    memset(s, 0, sizeof *s); s->active = 1; s->view = INSTALL_DISK; s->disk = s->nic = s->existing_disk = -1;
    static char text[8192]; FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    size_t n = fread(text, 1, sizeof text - 1, f); text[n] = 0; fclose(f);
    parse_lines(s, text, "inventory");
    printf("none=%%d\n", install_smart_failed(s));
    install_pick_disk(s, atoi(argv[2]));
    if (argc > 3) install_raid_set(s, atoi(argv[3]));
    printf("smart=%%s,%%s failed=%%d\n", s->disks[0].smart, s->disks[1].smart, install_smart_failed(s));
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-smart")
    source = tmp / "install_smart.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_smart"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _step(driver: Path, tmp_path: Path, smart_a: str, smart_b: str, *choice: int) -> list[str]:
    inv = tmp_path / "inventory"
    inv.write_text("virt=metal\n" + "".join(
        f"disk=/dev/{name}|model=M {name}|size=500107862016|bus=sata|removable=0|has=empty|iso=0{tail}\n"
        for name, tail in (("sda", smart_a), ("sdb", smart_b))), encoding="utf-8", newline="\n")
    got = subprocess.run([str(driver), str(inv), *map(str, choice)], capture_output=True, text=True,
                         timeout=30, stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    return got.stdout.split()


@NATIVE
def test_the_chosen_disk_that_failed_smart_is_named(driver, tmp_path):
    assert _step(driver, tmp_path, "|smart=passed", "|smart=failed", 1) == [
        "none=-1", "smart=passed,failed", "failed=1"]


@NATIVE
def test_a_failed_disk_that_is_not_chosen_is_not_the_warning(driver, tmp_path):
    assert _step(driver, tmp_path, "|smart=passed", "|smart=failed", 0)[-1] == "failed=-1"


@NATIVE
def test_a_failed_raid_member_is_the_warning(driver, tmp_path):
    """RAID1 over both: sdb is erased too, even though sda is the disk the operator picked."""
    assert _step(driver, tmp_path, "|smart=passed", "|smart=failed", 0, 1)[-1] == "failed=1"


@NATIVE
@pytest.mark.parametrize("tail", ["|smart=unknown", ""])   # "" = an older bridge without the field
def test_unknown_or_absent_is_no_warning(driver, tmp_path, tail):
    assert _step(driver, tmp_path, tail, tail, 1)[-1] == "failed=-1"
