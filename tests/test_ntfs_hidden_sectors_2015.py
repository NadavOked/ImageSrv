"""#2015 -- 'hidden sectors' (boot sector 0x1C..0x1F) of a partition the restore moved.

Found on the lab, 22/10 (read-only): cloner 2's Windows partition started at
5919226 and its NTFS boot sector said 10185210; HP-BUILD-2's recovery started
at 975110144 and said 467197952. partclone writes the source's boot sector as
it was, and the restore puts partitions elsewhere (shrink.sh compacts the
layout, expand_last moves the tail to the disk's end, share_head moves a head
forward). UEFI ignores the field; NTFS's boot code under BIOS/MBR does not.

The partition is a regular file standing in for the node and sysfs is a
directory (``SYSROOT``) -- no loop device, no root, no mkfs (the station's
guard forbids it). The boot sectors are built here, byte by byte, the way
mkntfs and mkfs.fat lay them out. ``blockdev``/``dd`` are the fakes of
``test_ntfs_backup_boot_1487.py``: dd is the real one unless a test makes the
write fail or "succeed" without landing (principle 5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_ntfs_backup_boot_1487 import BLOCKDEV, DD, GARBAGE, ntfs_part, sector
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

OLD = 10185210     # what the source's boot sector said (lab, cloner 2)
START = 5919226    # where the partition starts on the target (sysfs)


def dword(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 4], "little")


def ntfs_moved(sectors: int = 64, hidden: int = OLD, bps: int = 512) -> bytes:
    """An NTFS partition as partclone left it: the source's 'hidden sectors',
    and its backup boot sector already a copy of sector 0 (#1487 ran)."""
    part = bytearray(ntfs_part(sectors, sectors - 1, bps))
    part[0x1C:0x20] = hidden.to_bytes(4, "little")
    part[(sectors - 1) * bps:] = part[:bps]
    return bytes(part)


def fat_part(*, fat32: bool, hidden: int = OLD, sectors: int = 64) -> bytes:
    """A FAT boot sector (BPB): FAT32 has no 16-bit FAT size, 32 reserved
    sectors and its backup boot sector at 6; FAT16 has neither."""
    boot = bytearray([0x33]) * 512
    boot[0:3] = b"\xeb\x58\x90"
    boot[3:11] = b"mkfs.fat"
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = 1
    boot[14:16] = (32 if fat32 else 4).to_bytes(2, "little")
    boot[22:24] = (0 if fat32 else 8).to_bytes(2, "little")
    boot[0x1C:0x20] = hidden.to_bytes(4, "little")
    boot[50:52] = (6 if fat32 else 0x3333).to_bytes(2, "little")
    boot[510:512] = b"\x55\xaa"
    part = bytearray([0x11]) * (512 * sectors)
    part[:512] = boot
    part[6 * 512:7 * 512] = boot          # FAT32's backup -- for FAT16 just data that looks like it
    return bytes(part)


def box(tmp_path: Path, part: bytes, *, start: int | None = START, fs: str = "ntfs",
        dd_mode: str = "", flush_fail: bool = False) -> tuple[Path, str]:
    b = tmp_path / "box"
    (b / "dev").mkdir(parents=True)
    (b / "run" / "targets" / "sda").mkdir(parents=True)
    (b / "dev" / "sda3").write_bytes(part)
    sysd = b / "sys" / "sys" / "block" / "sda" / "sda3"
    sysd.mkdir(parents=True)
    if start is not None:
        (sysd / "start").write_text(f"{start}\n")
    if dd_mode:
        (b / "dd_mode").write_text(dd_mode)
        (b / "dd_target").write_text("sda3")
    if flush_fail:
        (b / "flush_fail").write_text("")
    pre = (
        f'export REAL_DD="$(command -v dd)" BOX={posix(b)!r}\n'
        + make_stubs(b / "stubs", {"blockdev": BLOCKDEV, **({"dd": DD} if dd_mode else {})})
        + f"export TMPDIR={posix(b)!r} RUN_DIR={posix(b / 'run')!r} DEVROOT={posix(b / 'dev')!r} "
        f"SYSROOT={posix(b / 'sys')!r} LOG_FILE={posix(b / 'run' / 'agent.log')!r}\n"
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh\n"
        f". {posix(AGENT)}/lib/grow.sh; . {posix(AGENT)}/lib/ntfsunhib.sh; . {posix(AGENT)}/lib/writemark.sh; . {posix(AGENT)}/lib/failmark.sh\n"
        f". {posix(AGENT)}/lib/ntfsboot.sh\n"
        "ata_capture() { :; }\n"
        f"manifest_plan() {{ echo '3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|{fs}|{OLD}|32768|p3|y|false|u'; }}\n"
    )
    return b, pre


def target(tmp_path: Path, part: bytes, **kw) -> tuple[str, bytes, str]:
    fs = kw.get("fs", "ntfs")
    b, pre = box(tmp_path, part, **kw)
    out = run_sh(pre + f'boot_hidden_target sda 3 {fs}; echo "rc=$? err=$BOOT_HIDDEN_ERROR"')
    log = b / "run" / "agent.log"
    return out, (b / "dev" / "sda3").read_bytes(), log.read_text(encoding="utf-8") if log.exists() else ""


# --- NTFS -----------------------------------------------------------------------------


def test_a_moved_ntfs_partition_says_where_it_starts(tmp_path):
    """The bug: sector 0 kept the source's start. After: the real one, and the
    backup boot sector (NTFS reads it when sector 0 is bad) follows."""
    part = ntfs_moved()
    assert dword(part, 0x1C) == OLD
    out, after, log = target(tmp_path, part)
    assert "rc=0" in out, out
    assert dword(after, 0x1C) == START
    assert sector(after, 63) == sector(after, 0)
    # Nothing but the four bytes, in both copies.
    assert after[:0x1C] == part[:0x1C] and after[0x20:63 * 512 + 0x1C] == part[0x20:63 * 512 + 0x1C]
    assert after[63 * 512 + 0x20:] == part[63 * 512 + 0x20:]
    assert f"hidden sectors {OLD} -> {START}" in log, log


def test_a_4k_volume_counts_its_own_sectors(tmp_path):
    """sysfs counts 512-byte sectors; the field counts the volume's."""
    part = ntfs_moved(sectors=16, bps=4096)
    out, after, _ = target(tmp_path, part, start=2048)
    assert "rc=0" in out, out
    assert dword(after, 0x1C) == 256
    assert sector(after, 15, 4096) == sector(after, 0, 4096)


def test_a_start_off_the_volume_sector_writes_nothing(tmp_path):
    part = ntfs_moved(sectors=16, bps=4096)
    out, after, _ = target(tmp_path, part, start=2049)
    assert "rc=1" in out and "לא נכתב דבר" in out, out
    assert after == part


def test_a_partition_that_did_not_move_is_not_written(tmp_path):
    """Already right: not a byte written -- the write would fail if tried."""
    part = ntfs_moved(hidden=START)
    out, after, _ = target(tmp_path, part, dd_mode="fail")
    assert "rc=0" in out, out
    assert after == part


def test_no_start_from_the_kernel_is_a_failure_not_a_pass(tmp_path):
    """Principle 5: 'could not read where it starts' is not 'it is right'."""
    part = ntfs_moved()
    out, after, _ = target(tmp_path, part, start=None)
    assert "rc=1" in out and "מחיצה 3" in out and "sysfs" in out, out
    assert after == part


def test_a_boot_sector_that_is_not_ntfs_is_refused(tmp_path):
    part = bytearray(ntfs_moved())
    part[3:11] = b"-FVE-FS-"
    out, after, _ = target(tmp_path, bytes(part))
    assert "rc=1" in out and "אינו סקטור אתחול של NTFS" in out, out
    assert after == bytes(part)


def test_a_failed_write_is_a_failure(tmp_path):
    out, after, _ = target(tmp_path, ntfs_moved(), dd_mode="fail")
    assert "rc=1" in out and "dd rc=5" in out and "מחיצה 3" in out, out
    assert dword(after, 0x1C) == OLD


def test_a_write_that_did_not_land_is_caught_by_the_read_back(tmp_path):
    """dd said 0 and wrote nothing: only reading the field back tells."""
    out, after, _ = target(tmp_path, ntfs_moved(), dd_mode="lie")
    assert "rc=1" in out and f"נקרא בחזרה עם {OLD} ולא {START}" in out, out
    assert dword(after, 0x1C) == OLD


def test_no_cache_drop_means_no_read_back_evidence(tmp_path):
    out, _after, _ = target(tmp_path, ntfs_moved(), flush_fail=True)
    assert "rc=1" in out and "flushbufs" in out, out


# --- FAT ------------------------------------------------------------------------------


def test_a_moved_fat32_partition_and_its_backup_boot_sector(tmp_path):
    """The EFI partition: FAT32, the same field at the same offset, and its
    backup boot sector (BPB 0x32 = 6) gets the same value."""
    part = fat_part(fat32=True)
    out, after, _ = target(tmp_path, part, fs="vfat")
    assert "rc=0" in out, out
    assert dword(after, 0x1C) == START and dword(after, 6 * 512 + 0x1C) == START
    assert after[:0x1C] == part[:0x1C] and after[0x20:6 * 512 + 0x1C] == part[0x20:6 * 512 + 0x1C]
    assert after[6 * 512 + 0x20:] == part[6 * 512 + 0x20:]


def test_fat16_has_no_backup_boot_sector_to_touch(tmp_path):
    part = fat_part(fat32=False)
    out, after, _ = target(tmp_path, part, fs="vfat")
    assert "rc=0" in out, out
    assert dword(after, 0x1C) == START
    assert after[512:] == part[512:]


def test_a_fat_partition_without_its_signature_is_refused(tmp_path):
    part = bytearray(fat_part(fat32=True))
    part[510:512] = b"\x00\x00"
    out, after, _ = target(tmp_path, bytes(part), fs="vfat")
    assert "rc=1" in out and "FAT" in out, out
    assert after == bytes(part)


@pytest.mark.parametrize("fs", ["ext4", "swap", "btrfs"])
def test_other_filesystems_are_left_alone(tmp_path, fs):
    part = bytes([GARBAGE]) * 4096
    out, after, _ = target(tmp_path, part, fs=fs, start=None)
    assert "rc=0" in out, out
    assert after == part


# --- where it runs: finish_grow, every restore path ----------------------------------


def finish(tmp_path: Path, part: bytes, **kw) -> tuple[Path, str]:
    b, pre = box(tmp_path, part, **kw)
    out = run_sh(
        pre
        + "target_init sda 100\n"
        # The grow and the mark: what they saw, not what they do.
        + 'grow_expanded() { od -An -tu4 -j 28 -N 4 "$DEVROOT/sda3" | tr -d " " > "$RUN_DIR/at_grow"; }\n'
        + 'writemark_release() { echo released > "$RUN_DIR/released"; }\n'
        + 'finish_grow sda "" m.json; echo "rc=$?"; echo "state=$(cat $RUN_DIR/targets/sda/state)"; '
        'echo "err=$(cat $RUN_DIR/targets/sda/error 2>/dev/null)"\n'
    )
    return b, out


def test_finish_grow_fixes_it_before_the_grow(tmp_path):
    """After the read-back (its callers run it there) and before ntfsresize,
    which copies sector 0 to the backup with whatever it says."""
    b, out = finish(tmp_path, ntfs_moved())
    assert "rc=0" in out and "state=done" in out, out
    assert (b / "run" / "at_grow").read_text().strip() == str(START)
    after = (b / "dev" / "sda3").read_bytes()
    assert dword(after, 0x1C) == START and sector(after, 63) == sector(after, 0)


def test_finish_grow_fails_the_drawer_by_name_and_keeps_the_mark(tmp_path):
    """A field that did not land is a failed target, in the open -- not a
    warning like a deferred grow -- and the incomplete-write mark stays."""
    b, out = finish(tmp_path, ntfs_moved(), dd_mode="fail")
    assert "rc=1" in out and "state=failed" in out, out
    assert "מחיצה 3" in out and "hidden sectors" in out and "dd rc=5" in out, out
    assert not (b / "run" / "released").exists()
    assert not (b / "run" / "at_grow").exists()
