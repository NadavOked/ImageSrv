"""#1487 -- the NTFS backup boot sector after ``partclone.ntfs -r``.

Measured on cloner 2 (16/10, a local clone disk 3 -> a 256GB target): the
last sector of every NTFS target partition differed from its sector 0, except
the partition ntfsresize grew afterwards, and ``ntfsfix -n`` said "Failed to
fix the alternate boot sector". partclone writes the volume's clusters; the
backup boot sector is after them (sector ``number_of_sectors``, boot sector
bytes 0x28..0x2F), so nobody wrote it.

The partition here is a regular file standing in for the node -- no loop
device, no root. ``blockdev`` is faked for the two questions the agent asks
it (the size, and dropping the cache before the read-back); ``dd``, ``od``
and ``cmp`` are the real ones, except where the test is about dd itself
failing or "succeeding" without the bytes landing (principle 5).

The real thing -- partclone.ntfs, mkntfs and loop devices -- is in
``test_local_clone_engine.py`` (the Testrunner): the first and last sector of
every NTFS partition, source and targets, read back with dd.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_local_clone_agent import run as lc_run
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

GARBAGE = 0xEE   # what the target's last sector held before: not a boot sector

#: ``blockdev`` answers the size from the file and drops a cache it does not
#: have. ``$BOX/flush_fail`` makes --flushbufs fail.
BLOCKDEV = (
    '#!/bin/sh\n'
    'case "$1" in\n'
    '  --getsize64) wc -c < "$2" | tr -d " " ;;\n'
    '  --flushbufs) [ ! -f "$BOX/flush_fail" ] ;;\n'
    '  *) exit 1 ;;\n'
    'esac\n'
)

#: The real dd, except for the write (``seek=``) to a node named in
#: ``$BOX/dd_target``: ``$BOX/dd_mode`` = fail (rc 5, nothing written) or lie
#: (rc 0, nothing written -- the write that did not land).
DD = (
    '#!/bin/sh\n'
    'case "$*" in\n'
    '  *seek=*)\n'
    '    if [ -f "$BOX/dd_mode" ] && case "$*" in *"$(cat "$BOX/dd_target")"*) true ;; *) false ;; esac; then\n'
    '      case "$(cat "$BOX/dd_mode")" in fail) echo "dd: write error" >&2; exit 5 ;; lie) cat > /dev/null 2>&1; exit 0 ;; esac\n'
    '    fi ;;\n'
    'esac\n'
    'exec "$REAL_DD" "$@"\n'
)

#: partclone.ntfs -r: the stream to -O, as the real one writes the clusters.
PARTCLONE = (
    '#!/bin/sh\n'
    'while [ $# -gt 0 ]; do [ "$1" = -O ] && out=$2; shift; done\n'
    'mkdir -p "$(dirname "$out")"\n'
    'cat > "$out"\n'
)


def ntfs_part(sectors: int, total: int, bps: int = 512, oem: bytes = b"NTFS    ") -> bytes:
    """A partition of ``sectors`` sectors whose boot sector says ``total``."""
    boot = bytearray(bytes(range(256)) * (bps // 256))
    boot[3:11] = oem
    boot[11:13] = bps.to_bytes(2, "little")
    boot[0x28:0x30] = total.to_bytes(8, "little")
    boot[bps - 2:bps] = b"\x55\xaa"
    body = bytes([0x11]) * (bps * (sectors - 2))
    return bytes(boot) + body + bytes([GARBAGE]) * bps


def sector(data: bytes, n: int, bps: int = 512) -> bytes:
    return data[n * bps:(n + 1) * bps]


def prelude(box: Path, *, dd: bool = False) -> str:
    stubs = {"blockdev": BLOCKDEV, "partclone.ntfs": PARTCLONE}
    if dd:
        stubs["dd"] = DD
    return (
        f'export REAL_DD="$(command -v dd)" BOX={posix(box)!r}\n'
        + make_stubs(box / "stubs", stubs)
        + f"export TMPDIR={posix(box)!r} RUN_DIR={posix(box / 'run')!r} DEVROOT={posix(box / 'dev')!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=5 WAIT_HELPER_S=5 WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20\n"
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; . {posix(AGENT)}/lib/core/progress.sh\n"
        f". {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; . {posix(AGENT)}/lib/restore/verdict.sh\n"
        f". {posix(AGENT)}/lib/restore/failmark.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh\n"
        "ata_capture() { :; }\n"
    )


def make_box(tmp_path: Path, parts: dict[str, bytes]) -> Path:
    box = tmp_path / "box"
    (box / "dev").mkdir(parents=True)
    (box / "run" / "targets").mkdir(parents=True)
    for name, data in parts.items():
        (box / "dev" / name).write_bytes(data)
    return box


def backup(tmp_path: Path, part: bytes, *, dd_mode: str = "", flush_fail: bool = False) -> tuple[str, bytes]:
    box = make_box(tmp_path, {"sda3": part})
    if dd_mode:
        (box / "dd_mode").write_text(dd_mode)
        (box / "dd_target").write_text("sda3")
    if flush_fail:
        (box / "flush_fail").write_text("")
    out = run_sh(prelude(box, dd=bool(dd_mode))
                 + f'ntfs_backup_boot {posix(box / "dev" / "sda3")!r}; echo "rc=$? err=$NTFS_BOOT_ERROR"')
    return out, (box / "dev" / "sda3").read_bytes()


# --- the sector itself (ntfs_backup_boot) ----------------------------------------------


@pytest.mark.parametrize("bps,sectors", [(512, 64), (4096, 16)])
def test_the_last_sector_becomes_the_boot_sector(tmp_path, bps, sectors):
    """The volume fills the partition (number_of_sectors = sectors - 1), as
    Windows and mkntfs make it: the backup is the partition's last sector --
    the md5(first) == md5(last) check from the metal."""
    part = ntfs_part(sectors, sectors - 1, bps)
    assert sector(part, sectors - 1, bps) != sector(part, 0, bps)   # the state partclone -r left
    out, after = backup(tmp_path, part)
    assert "rc=0" in out, out
    assert sector(after, sectors - 1, bps) == sector(after, 0, bps)
    assert after[:-bps] == part[:-bps] and len(after) == len(part)   # nothing else moved


def test_a_volume_smaller_than_its_partition_gets_the_backup_where_ntfs_looks(tmp_path):
    """A shrunk volume in a larger partition: the backup goes to sector
    number_of_sectors (40), not to the partition's last sector (63)."""
    part = ntfs_part(64, 40)
    out, after = backup(tmp_path, part)
    assert "rc=0" in out, out
    assert sector(after, 40) == sector(after, 0)
    assert sector(after, 63) == bytes([GARBAGE]) * 512
    assert after[:40 * 512] == part[:40 * 512] and after[41 * 512:] == part[41 * 512:]


@pytest.mark.parametrize("total", [64, 1 << 40, 0])
def test_a_volume_that_does_not_fit_its_partition_writes_nothing(tmp_path, total):
    """Never past the partition: a boot sector that claims more sectors than
    the partition has (or none) fails, and not a byte is written."""
    part = ntfs_part(64, total)
    out, after = backup(tmp_path, part)
    assert "rc=1" in out and "לא נכתב דבר" in out, out
    assert after == part


def test_a_boot_sector_that_is_not_ntfs_is_refused(tmp_path):
    part = ntfs_part(64, 63, oem=b"-FVE-FS-")
    out, after = backup(tmp_path, part)
    assert "rc=1" in out and "אינו סקטור אתחול של NTFS" in out, out
    assert after == part


def test_a_failed_write_is_a_failure(tmp_path):
    out, _after = backup(tmp_path, ntfs_part(64, 63), dd_mode="fail")
    assert "rc=1" in out and "dd rc=5" in out, out


def test_a_write_that_did_not_land_is_caught_by_the_read_back(tmp_path):
    """Principle 5: dd said 0 and wrote nothing. Only reading the sector back
    tells this apart from a written backup."""
    out, after = backup(tmp_path, ntfs_part(64, 63), dd_mode="lie")
    assert "rc=1" in out and "שונה מסקטור 0" in out, out
    assert sector(after, 63) != sector(after, 0)


def test_no_cache_drop_means_no_read_back_evidence(tmp_path):
    """A read-back from the cache dd just filled proves nothing about the disk."""
    out, _after = backup(tmp_path, ntfs_part(64, 63), flush_fail=True)
    assert "rc=1" in out and "flushbufs" in out, out


def test_a_partition_that_is_not_ntfs_is_left_alone(tmp_path):
    box = make_box(tmp_path, {"sda1": bytes(64 * 512)})
    (box / "run" / "targets" / "sda").mkdir()
    out = run_sh(prelude(box) + 'ntfs_boot_target sda 1 vfat; echo "rc=$?"')
    assert "rc=0" in out, out
    assert (box / "dev" / "sda1").read_bytes() == bytes(64 * 512)
    assert not (box / "run" / "targets" / "sda" / "ntfs_boot").exists()


# --- the three restore paths -------------------------------------------------------------

PART = ntfs_part(2048, 2047)                 # 1 MiB
PART_SHA = hashlib.sha256(PART).hexdigest()  # the zstd stub is cat: compressed = raw


def stream_box(tmp_path: Path, disks=("sda",), dd_fail_on: str = "") -> tuple[Path, str]:
    box = make_box(tmp_path, {})
    (box / "part.bin").write_bytes(PART)
    if dd_fail_on:
        (box / "dd_mode").write_text("fail")
        (box / "dd_target").write_text(dd_fail_on)
    pre = (prelude(box, dd=bool(dd_fail_on))
           + "".join(f"target_init {d} {len(PART)}\n" for d in disks)
           + f'stream_source() {{ cat {posix(box / "part.bin")!r}; }}\n')
    return box, pre


def state(box: Path, dev: str) -> tuple[str, str]:
    t = box / "run" / "targets" / dev
    err = t / "error"
    return (t / "state").read_text().strip(), err.read_text(encoding="utf-8").strip() if err.exists() else ""


def test_unicast_restore_writes_the_backup_after_partclone(tmp_path):
    """restore.sh restore_partition: the single station (unicast and multicast)."""
    box, pre = stream_box(tmp_path)
    out = run_sh(pre + "node_is_block() { true; }\n"
                 f'restore_partition unicast http://s img sda 3 ntfs p3.zst {PART_SHA} "" > /dev/null 2>&1; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    after = (box / "dev" / "sda3").read_bytes()
    assert sector(after, 2047) == sector(after, 0)
    assert (box / "run" / "targets" / "sda" / "ntfs_boot").read_text().split() == ["3"]


def test_unicast_restore_fails_when_the_backup_does_not_land(tmp_path):
    box, pre = stream_box(tmp_path, dd_fail_on="sda3")
    out = run_sh(pre + "node_is_block() { true; }\n"
                 f'restore_partition unicast http://s img sda 3 ntfs p3.zst {PART_SHA} "" > /dev/null 2>&1; echo "rc=$?"')
    assert out.strip().endswith("rc=1"), out
    log = (box / "run" / "agent.log").read_text(encoding="utf-8")
    assert "מחיצה 3: סקטור אתחול הגיבוי של NTFS" in log and "dd rc=5" in log, log


def test_a_round_fails_only_the_drawer_whose_backup_did_not_land(tmp_path):
    """drawers.sh -> verdict.sh: a round (and a direct send's receivers). The
    failing drawer fails by name with the partition and the cause; its
    neighbour gets the backup and stays alive."""
    box, pre = stream_box(tmp_path, disks=("sda", "sdb"), dd_fail_on="sda1")
    out = run_sh(pre + f'restore_partition_drawers unicast http://s img 1 ntfs p1.zst {PART_SHA} "" sda sdb '
                 '> /dev/null 2>&1; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out              # one drawer alive
    st, err = state(box, "sda")
    assert st == "failed" and "מחיצה 1: סקטור אתחול הגיבוי של NTFS" in err and "dd rc=5" in err, (st, err)
    st, err = state(box, "sdb")
    assert st != "failed", (st, err)
    after = (box / "dev" / "sdb1").read_bytes()
    assert sector(after, 2047) == sector(after, 0)
    assert (box / "run" / "targets" / "sdb" / "ntfs_boot").read_text().split() == ["1"]


SHA16 = "a" * 16


def judge(tmp_path: Path, dd_fail: bool) -> tuple[str, Path]:
    """lccopy.sh lc_judge: the local clone. The four records agree; then the backup."""
    devroot = tmp_path / "dev"
    devroot.mkdir()
    (devroot / "sda3").write_bytes(ntfs_part(64, 63))
    stubs = make_stubs(tmp_path / "stubs", {"blockdev": BLOCKDEV, **({"dd": DD} if dd_fail else {})})
    if dd_fail:
        (tmp_path / "dd_mode").write_text("fail")
        (tmp_path / "dd_target").write_text("sda3")
    t = (f'export REAL_DD="$(command -v dd)" BOX={posix(tmp_path)!r}\n' + stubs
         + 'd=$RUN_DIR/targets/sda; mkdir -p "$LC/run"; target_init sda 100; ata_capture() { :; }; '
         'echo "$d/feed ok" > "$LC/run/fanout.out"; echo 0 > $d/pipeline.rc; echo 0 > $d/tee.rc; '
         f'echo "{SHA16}  -" > $d/sha.out; lc_judge sda 3 {SHA16} ntfs > /dev/null; '
         'echo "state=$(cat $d/state)"; echo "err=$(cat $d/error 2>/dev/null)"; '
         'echo "ok=$(cat $d/lc_ok 2>/dev/null)"; echo "nb=$(cat $d/ntfs_boot 2>/dev/null)"; true')
    out = lc_run(tmp_path, t, DEVROOT=posix(devroot))
    assert out.returncode == 0, out.stderr
    return out.stdout, devroot / "sda3"


def test_a_local_clone_partition_counts_with_its_backup_written(tmp_path):
    out, node = judge(tmp_path, dd_fail=False)
    assert "state=waiting" in out and "ok=3" in out and "nb=3" in out, out
    after = node.read_bytes()
    assert sector(after, 63) == sector(after, 0)


def test_a_local_clone_target_whose_backup_failed_fails_by_name(tmp_path):
    out, _node = judge(tmp_path, dd_fail=True)
    assert "state=failed" in out and "מחיצה 3: סקטור אתחול הגיבוי של NTFS" in out, out
    assert "ok=\n" in out and "nb=\n" in out, out                   # not counted


def test_the_end_screen_line_carries_the_count(tmp_path):
    """lcgui.sh lc_tgt: the 9th field is how many backups were written and read back."""
    t = ('d=$RUN_DIR/targets/sda; mkdir -p "$LC/run"; target_init sda 100; echo sda > "$LC/run/targets"; '
         'printf "3\\n4\\n" > $d/ntfs_boot; lc_gui_run; true')
    out = lc_run(tmp_path, t)
    line = [x for x in out.stdout.splitlines() if x.startswith("lc_tgt=")]
    assert line and line[0].split("|")[8] == "2", out.stdout + out.stderr
