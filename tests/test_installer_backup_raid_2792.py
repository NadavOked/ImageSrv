"""#2792: with RAID5/RAID10 the space kept for backups was measured against
one drive, not the array, and the proposed 5 GB was refused.

ESXi 09-10/11, v0.91.7, nt1109-r5 (3x16GB RAID5, an array of ~32GB): the step
said "מתוך 17 GB פנויים בכונן" and refused "5 GB · מוצע" ("not more than 20%
of the drive"); it went through only with 3. RAID1 is right by accident (the
array is one drive's size).

Both halves compute the array: the engine (installer/lib/backup.sh,
backup_install_cap_gb, after validate_raid) and the GUI's figure
(install_raid.h install_store_bytes) -- the same rule, or the GUI would show
what the engine refuses.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_installer_backup_1956 import LIB, SH, SH_ONLY
from test_native_installer_raid_1989 import NATIVE, SRC

GB16 = 16 * 10 ** 9 // 512          # sectors of a 16 GB drive: 20% is 3 GB
GB500 = 500 * 10 ** 9 // 512


def _sysroot(tmp_path: Path, sizes: dict[str, int]) -> Path:
    for name, sectors in sizes.items():
        block = tmp_path / "sys" / "block" / name
        block.mkdir(parents=True)
        (block / "size").write_text(f"{sectors}\n", newline="\n")
    return tmp_path


def _validate(sysroot: Path, gb: str, level: str = "none", members: str = "", spares: str = "0"):
    count = len(members.split())
    prelude = (f'fail() {{ printf "FAIL %s %s\n" "$1" "$2" >&2; exit 2; }}; SYSROOT="{sysroot.as_posix()}"; '
               f'DISK_NAME=sda; RAID_LEVEL={level}; RAID_MEMBERS="{members}"; RAID_COUNT={count}; '
               f'RAID_SPARES={spares}; . "{(LIB / "backup.sh").as_posix()}"; ')
    script = 'ANSWER_BACKUP_TIME=; ANSWER_BACKUP_RESERVE_GB=$1; validate_backup; echo ok; backup_install_cap_gb'
    return subprocess.run([SH, "-c", prelude + script, "_", gb], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)


@SH_ONLY
@pytest.mark.parametrize("level,members,spares,cap", [
    ("raid5", "sda sdb sdc", "0", 6),          # (3-1) x 16GB = 32GB -> 6 GB
    ("raid10", "sda sdb sdc sdd", "0", 6),     # 4/2 x 16GB
    ("raid6", "sda sdb sdc sdd", "0", 6),      # (4-2) x 16GB
    ("raid1", "sda sdb", "0", 3),              # one drive: unchanged
    ("raid1", "sda sdb sdc", "1", 3),          # the spare holds no data
])
def test_the_proposed_5gb_against_the_array(tmp_path, level, members, spares, cap):
    root = _sysroot(tmp_path, {name: GB16 for name in members.split()})
    got = _validate(root, "5", level, members, spares)
    if cap >= 5:
        assert got.stdout.split()[:1] == ["ok"], got.stderr
    else:
        assert "invalid-backup-reserve" in got.stderr, got
    probe = _validate(root, "1", level, members, spares)
    assert probe.stdout.split() == ["ok", str(cap)], probe


@SH_ONLY
def test_the_smallest_member_sets_the_array(tmp_path):
    root = _sysroot(tmp_path, {"sda": GB500, "sdb": GB16, "sdc": GB500})
    assert _validate(root, "1", "raid5", "sda sdb sdc").stdout.split() == ["ok", "6"]


@SH_ONLY
def test_no_raid_is_still_the_one_disk(tmp_path):
    root = _sysroot(tmp_path, {"sda": GB16})
    assert "invalid-backup-reserve" in _validate(root, "5").stderr
    assert _validate(root, "3").stdout.split() == ["ok", "3"]


@SH_ONLY
def test_an_unreadable_member_is_a_named_failure(tmp_path):
    root = _sysroot(tmp_path, {"sda": GB16, "sdb": GB16})
    got = _validate(root, "1", "raid5", "sda sdb sdc")
    assert "FAIL backup-disk-size" in got.stderr and "sdc" in got.stderr


DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>
int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }
static App app;
/* argv: CHOICE SIZE-GB... -- the first disk is the chosen one */
int main(int argc, char **argv) {
    InstallState *s = &app.install; s->virt = INSTALL_VIRT_METAL; s->disk = 0; s->ndisks = argc - 2;
    for (int i = 0; i < s->ndisks; i++) {
        snprintf(s->disks[i].path, sizeof s->disks[i].path, "/dev/sd%%c", 'a' + i);
        snprintf(s->disks[i].bus, sizeof s->disks[i].bus, "sata");
        s->disks[i].size_bytes = strtoull(argv[i + 2], NULL, 10) * 1000000000ULL;
    }
    s->raid = atoi(argv[1]);
    printf("%%.0f\n", install_store_bytes(s) / 1e9);
    return 0;
}
'''

NONE, R1, R1S, R5, R6, R10 = range(6)


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("store-bytes")
    source = tmp / "store.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "store"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr

    def run(choice: int, *sizes: int) -> int:
        out = subprocess.run([str(binary), str(choice), *map(str, sizes)], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL)
        assert out.returncode == 0, out.stderr
        return int(out.stdout)
    return run


@NATIVE
@pytest.mark.parametrize("choice,sizes,gb", [
    (R5, (16, 16, 16), 32), (R10, (16, 16, 16, 16), 32), (R6, (16, 16, 16, 16), 32),
    (R1, (16, 16), 16), (R1S, (16, 16, 16), 16), (NONE, (16, 16), 16), (R5, (500, 16, 500), 32),
])
def test_the_gui_shows_the_array_as_the_engine_counts_it(store, choice, sizes, gb):
    assert store(choice, *sizes) == gb
