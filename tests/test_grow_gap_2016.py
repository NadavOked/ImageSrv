"""#2016 -- the grown partition starts where it starts on the source, aligned up to 1MiB.

From the field (22/10): a local clone of a SAMSUNG MZ7TE256 (238GiB: C:,
Recovery 972MiB, EFI 100MiB) to an 860 (465GiB) came out with C: starting at
58653315 -- 28GB unallocated before it, and an odd sector, not aligned for an
SSD. The engine does not invent that start: expand_last rebuilds the candidate
at the manifest's ``start_sector`` (`-n i:start:0`), which is the source's own
`First sector` (lcplan.sh), and lc_table_check demanded exactly that start back.
Run on the source shape below with p1 at 58653315, the code before this fix
writes the field's table to the sector: p1 58653315, p2 974577664, p3 976568320
-- the start and the gap came from the SOURCE.

What changes: the candidate is rebuilt anyway, so its start is aligned UP to
2048 sectors when the source's is not (an aligned source start stays exactly
where it is); a head partition that moves forward (#1898) too. The grown one
still takes every sector up to the tail, the tail sits at the end of the disk,
and every start is a multiple of 2048. lc_table_check accepts the aligned start
-- and only it. The same expand_last runs after a restore from the server
(restore.sh, drawers.sh), so this covers both paths.

Real sgdisk under busybox ash on sparse files the size of the field's disks
(like test_sgdisk_one_call_real_1714.py). Linux only: the Testrunner.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_json_escape import BUSYBOX, bb, source_line

SGDISK = shutil.which("sgdisk")

pytestmark = requires_native(
    ("busybox", BUSYBOX), ("sgdisk", SGDISK), posix=True,
    why="the claim is about the table real sgdisk writes -- a stub would check our own assumption")

SOURCE = 500118192           # SAMSUNG MZ7TE256 -- 238.47GiB
TARGET = 976773168           # Samsung 860 500GB -- 465.76GiB
FIELD_START = 58653315       # p1 on the field's target (sgdisk, 22/10) -- and so on its source
DISK_GUID = "20162016-0000-0000-0000-000000002016"
SHA = "a" * 64
BASIC, RECOVERY = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7", "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
REC_ATTRS = "8000000000000001"
REC_N, ESP_N = 1990656, 204800          # 972MiB, 100MiB -- as on the field's target
ESP_AT = 499912704                      # the source's tail: Recovery, then EFI at its end
REC_AT = ESP_AT - REC_N


def _parts(p1_start: int) -> list[tuple]:
    """idx, type, role, fs, start, sectors, unique GUID, attrs -- Windows, Recovery, EFI."""
    return [
        (1, BASIC, "windows", "ntfs", p1_start, REC_AT - p1_start, "11111111-1111-1111-1111-111111111111", ""),
        (2, RECOVERY, "recovery", "ntfs", REC_AT, REC_N, "22222222-2222-2222-2222-222222222222", REC_ATTRS),
        (3, ESP, "esp", "vfat", ESP_AT, ESP_N, "33333333-3333-3333-3333-333333333333", ""),
    ]


def _run(tmp_path: Path, p1_start: int, check: str = "lc_table_check sda manifest.json") -> str:
    parts = _parts(p1_start)
    plan = tmp_path / "plan.txt"
    plan.write_text("".join(f"{i}|{t}|{r}|{fs}|{s}|{n * 512}|local:p{i}|{SHA}|false|{u}||{a}\n"
                            for i, t, r, fs, s, n, u, a in parts), encoding="utf-8")
    lc = tmp_path / "lc" / "run"
    lc.mkdir(parents=True)
    # lc_entry: idx|name|attrs as the source's sgdisk -i says them
    (lc / "entries").write_text("".join(f"{i}|Basic data partition|{a or '0' * 16}\n"
                                        for i, *_m, a in parts), encoding="utf-8")
    sysblock = tmp_path / "sys" / "block" / "sda"
    sysblock.mkdir(parents=True)
    (sysblock / "size").write_text(f"{TARGET}\n")
    (tmp_path / "run").mkdir()
    dev = tmp_path / "dev"
    dev.mkdir()
    with (dev / "sda").open("wb") as f:
        f.truncate(TARGET * 512)
    body = (
        f'RUN_DIR="{(tmp_path / "run").as_posix()}"; LOG_FILE="{(tmp_path / "log").as_posix()}"\n'
        f'DEVROOT="{dev.as_posix()}"; SYSROOT="{tmp_path.as_posix()}"; LC="{(tmp_path / "lc").as_posix()}"\n'
        'LC_GPT_TAIL=262144\n'
        'log() { echo "LOG $*" >> "$LOG_FILE"; }\n'
        + source_line("waits.sh", "restore.sh", "manifest.sh", "expand.sh", "expandshare.sh",
                      "writemark.sh", "lcplan.sh")
        + f'manifest_plan() {{ cat "{plan.as_posix()}"; }}\n'
        f'json_get() {{ case "$2" in .sector_size) echo 512 ;; .scheme) echo gpt ;; '
        f'.disk_guid) echo {DISK_GUID} ;; *) echo null ;; esac; }}\n'
        f'blockdev() {{ case "$1" in --getss) echo 512 ;; --getsz) echo {TARGET} ;; *) return 0 ;; esac; }}\n'
        'target_write_started() { :; }\n'
        'verify_table() { :; }   # the partition nodes; the table itself is read below\n'
        'share_tail() { printf "%s\\n" "$4"; }   # nothing was shrunk: test_expand_share_1897.py\n'
        'apply_gpt sda manifest.json || { echo "FAIL apply"; exit 0; }\n'
        'EXPAND_OVERRIDE=auto expand_last sda manifest.json || { echo "FAIL expand"; exit 0; }\n'
        f'if {check}; then echo OK; else echo "FAIL check: $LC_ERROR"; fi\n'
    )
    return bb(tmp_path, body).strip()


def _table(tmp_path: Path) -> dict[int, tuple[int, int]]:
    """{index: (first, last)} read back from the target with sgdisk -i."""
    out = {}
    for i in (1, 2, 3):
        info = subprocess.run([SGDISK, "-i", str(i), str(tmp_path / "dev" / "sda")],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        first = next(int(l.split(":")[1].split()[0]) for l in info.splitlines() if l.startswith("First sector"))
        last = next(int(l.split(":")[1].split()[0]) for l in info.splitlines() if l.startswith("Last sector"))
        out[i] = (first, last)
    return out


def _tail_of_the_target() -> tuple[int, int]:
    esp = ((TARGET - 34) - ESP_N + 1) // 2048 * 2048
    rec = ((esp - 1) - REC_N + 1) // 2048 * 2048
    return rec, esp


@pytest.mark.parametrize("p1_start,want", [
    (2048, 2048),                        # the common source: C: stays at 2048, to the sector
    (FIELD_START, 58654720),             # the field's source: aligned UP (28640 x 2048), never down
])
def test_the_grown_partition_keeps_its_source_start_aligned_and_takes_all_the_room(tmp_path, p1_start, want):
    assert _run(tmp_path, p1_start) == "OK", (tmp_path / "log").read_text()
    t = _table(tmp_path)
    rec, esp = _tail_of_the_target()
    assert t[1][0] == want, t
    assert t[2] == (rec, rec + REC_N - 1) and t[3] == (esp, esp + ESP_N - 1), t   # the tail at the end
    assert t[1][1] == rec - 1, t                         # C: runs up to the Recovery: all the room
    assert all(first % 2048 == 0 for first, _last in t.values()), t
    assert (tmp_path / "run" / "targets" / "sda" / "expanded").read_text().strip() == "1|ntfs"


def test_the_table_check_takes_the_aligned_start_and_nothing_else(tmp_path):
    """The read-back of the local clone (lc_table_check) compares the grown one's
    start with the start expand_last writes -- not with the source's, which is no
    longer on the disk, and not with any start: one sector off still fails."""
    moved = (f'sgdisk -d 1 -n 1:{58654720 + 2048}:0 -t 1:{BASIC} '
             f'-u 1:11111111-1111-1111-1111-111111111111 -c 1:imagectl-incomplete "$DEVROOT/sda" >> "$LOG_FILE" 2>&1 '
             '&& lc_table_check sda manifest.json')
    out = _run(tmp_path, FIELD_START, check=moved)
    assert out.startswith("FAIL check: ") and "58656768" in out, out


def test_a_head_partition_that_moves_forward_starts_on_a_mib_and_so_does_the_root(tmp_path):
    """#1898's split (approved on the cloner): C: grows in place, and what sits
    between it and the root moves forward. A moved start is aligned up like the
    grown one's -- and what follows moves by that too, so nothing overlaps."""
    from test_agent import run_expand
    from test_head_share_1898 import DISK, GIB, dual_boot, plan_of

    m = dual_boot()
    esp, c, root, swap = m["partitions"]
    c_end = c["start_sector"] + c["size_bytes"] // 512
    boot = dict(root, index=5, start_sector=c_end + 7, size_bytes=GIB, used_bytes=GIB // 2)   # odd, as on the field
    root["start_sector"] = boot["start_sector"] + GIB // 512 + 3
    m["partitions"] = [esp, c, root, swap, boot]
    rc, calls, marker = run_expand(tmp_path, plan_of(m), disk_sectors=DISK, manifest=m, share_ok=True)
    assert rc == "rc=0" and marker == "3|ext4", (rc, calls)
    starts = {int(i): int(s) for i, s in re.findall(r" -n (\d+):(-?\d+):", " " + calls[0])}
    assert all(s % 2048 == 0 for i, s in starts.items() if i != 2), (starts, calls[0])   # C: stays put
    assert starts[2] == c["start_sector"], calls[0]
    room = DISK - 34 - (root["start_sector"] + root["size_bytes"] // 512) - swap["size_bytes"] // 512
    g = room * 60 // 90 // 2048 * 2048
    b = (boot["start_sector"] + 2047) // 2048 * 2048 + g
    assert starts[5] == b, (starts, g)
    off = b - boot["start_sector"]
    assert starts[3] == (root["start_sector"] + off + 2047) // 2048 * 2048, (starts, off)
