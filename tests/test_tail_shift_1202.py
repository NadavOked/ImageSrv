"""#1202 -- the tail of the layout moves back, like FOG (owner's decision 22/09).

"256GB" from one maker is a few megabytes smaller than "256GB" from another:
measured on metal 20/09, source NVMe 256,062,259,200 -> target Samsung
256,060,514,304, and the drawer was refused. When C: is nearly full the
regular shrink (margin 10% / 2GiB) moves nothing, the layout ends at the end
of the source disk, and every slightly smaller drive is refused.

The rule (agent/lib/shrink/shrinkplan.sh, ``_shrink_tail``): the captured layout must
end at least ``SHRINK_TAIL_BYTES`` (64MiB) before the end of the source disk.
What is missing is taken from the LAST system partition that still keeps that
much free above ntfsresize's minimum; everything after it (recovery, MSR) moves
back in the manifest only. Nothing after the system partition is ever written
or measured on the source, and a partition without room is not shrunk -- the
restore then refuses by name and by how much (``disk_fits``).

These run the real ``capture_disk`` against the stateful sgdisk/ntfsresize
stubs of ``test_shrink_on_capture.py``, with the numbers measured 20/09.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import capture_run, refusal_reason
from test_shrink_on_capture import (
    BLOCKDEV_OK,
    FS_MAP,
    REC_GUID,
    REC_UGUID,
    WIN_GUID,
    WIN_UGUID,
    manifest,
    order,
    part,
    rewrites,
    source_writes,
    stubs,
    view_sys,
)

pytestmark = requires_native(("bash", BASH))

MIB = 1 << 20
TAIL = 64 * MIB

#: The two drives of 20/09, in bytes (issue #1202).
SOURCE_DISK_BYTES = 256_062_259_200
TARGET_BYTES = 256_060_514_304
DISK_SECTORS = SOURCE_DISK_BYTES // 512
#: Windows 11 on the source: ESP, C:, recovery last -- and 344KiB free after
#: the recovery before the 33 sectors of the GPT backup (a live disk, 22/09).
REC_END = DISK_SECTORS - 33 - 688
REC_START = 498_153_472                    # a multiple of 2048
REC_SECTORS = REC_END - REC_START
WIN_START = 1_085_440
WIN_SECTORS = REC_START - WIN_START
WIN_BYTES = WIN_SECTORS * 512
#: C: nearly full: 500MB free -- far below the regular margin (10% / 2GiB),
#: so the regular shrink moves nothing; but room for a 64MiB tail.
NEARLY_FULL_MIN = WIN_BYTES - 500_000_000
#: C: packed: 100MB free -- shrinking 64MiB would leave less than 64MiB.
PACKED_MIN = WIN_BYTES - 100_000_000

SGDISK = f'''#!/bin/sh
echo "$*" >> "$RUN_DIR/sgdisk.calls"
sizes="$RUN_DIR/sgdisk.sizes"
[ -f "$sizes" ] || printf '3={WIN_SECTORS}\\n4={REC_SECTORS}\\n' > "$sizes"
[ "$1" = "-a" ] && shift 2
if [ "$1" = "-d" ]; then
  idx="$2"; spec="$4"
  start=${{spec#*:}}; start=${{start%%:*}}; end=${{spec##*:}}
  sed -i "s/^$idx=.*/$idx=$((end - start + 1))/" "$sizes"
  exit 0
fi
if [ "$1" = "-i" ]; then
  n=$(sed -n "s/^$2=//p" "$sizes")
  case "$2" in
    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI system partition)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000001"
       echo "First sector: 2048 (at 1024.0 KiB)"
       echo "Partition size: 204800 sectors (100.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'EFI system partition'" ;;
    3) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {WIN_UGUID}"
       echo "First sector: {WIN_START} (at 530.0 MiB)"
       echo "Partition size: $n sectors (huge)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Basic data partition'" ;;
    4) echo "Partition GUID code: {REC_GUID} (Windows RE)"
       echo "Partition unique GUID: {REC_UGUID}"
       echo "First sector: {REC_START} (at the end)"
       echo "Partition size: $n sectors (960.6 MiB)"
       echo "Attribute flags: 8000000000000001"
       echo "Partition name: 'Recovery'" ;;
  esac
  exit 0
fi
echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
echo "   1            2048          206847   100.0 MiB   EF00  EFI system partition"
echo "   3         {WIN_START}      {REC_START - 1}   237.0 GiB   0700  Basic data partition"
echo "   4       {REC_START}       {REC_END - 1}   960.6 MiB  2700  Recovery"
'''

#: ntfsresize: --info reports $NTFS_MIN as the minimum; -n (#1466: the budget
#: of the RAM view) reports 10 MB of relocations; -s and -f -f exit 0.
NTFSRESIZE = '''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "You might resize at $NTFS_MIN bytes or 1 MB (freeing 1 MB)."; exit 0 ;;
  "-n "*) echo "Needed relocations : 2560 (10 MB)"; exit 0 ;;
  "-s "*|*" -s "*) IFS= read -r ans ;;
esac
exit 0
'''

ASK = 'shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo continue; }; '


def _capture(tmp_path, ntfs_min: int, disk_sectors: int = DISK_SECTORS, env: dict | None = None):
    size = f'echo {disk_sectors} > "$SYSROOT/sys/block/sda/size"; '
    # #1466: the kernel's view of THIS layout's partitions (FS_MAP's is the 512GB disk's)
    sysfs = view_sys({3: WIN_SECTORS, 4: REC_SECTORS})
    return capture_run(tmp_path, stubs=stubs(sgdisk=SGDISK, ntfsresize=NTFSRESIZE, blockdev=BLOCKDEV_OK),
                       shell_pre=FS_MAP + sysfs + size + ASK, env={"NTFS_MIN": str(ntfs_min), **(env or {})})


def _layout_need(parts: list[tuple[int, int]]) -> int:
    """required_bytes of the layout (start sector, size bytes) on the source disk."""
    from server.library.images import required_bytes
    return required_bytes({"sector_size": 512, "source_disk_bytes": SOURCE_DISK_BYTES,
                           "partitions": [{"start_sector": s, "size_bytes": b} for s, b in parts]})


def test_the_measured_case_is_refused_without_the_tail():
    """The scenario itself, before any code: the full layout needs more than
    the Samsung holds -- this is the drawer that was refused on 20/09."""
    need = _layout_need([(2048, 204800 * 512), (WIN_START, WIN_BYTES), (REC_START, REC_SECTORS * 512)])
    assert need > TARGET_BYTES, need


def test_a_nearly_full_c_gives_the_tail_and_the_image_fits_the_smaller_drive(tmp_path):
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN)
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    steps = order(run)
    assert "asked" in steps, "a shrink nobody said yes to is not a shrink"
    dev = posix(box / "dev")
    # #1466: the tail is taken on the RAM view of C:, never on the source
    assert f"ntfsresize -s {WIN_BYTES - TAIL} --no-progress-bar {dev}/mapper/lc-src-3" in steps, steps
    assert source_writes(steps, box) == [], source_writes(steps, box)
    m = manifest(run)
    win, rec = part(m, 3), part(m, 4)
    assert win["size_bytes"] == WIN_BYTES - TAIL and win["shrunk_from_bytes"] == WIN_BYTES
    assert win["start_sector"] == WIN_START
    # the recovery moved back by exactly the tail, in the manifest only
    assert rec["start_sector"] == REC_START - TAIL // 512
    assert rec["source_start_sector"] == REC_START
    assert rec["size_bytes"] == REC_SECTORS * 512 and "shrunk_from_bytes" not in rec
    assert rec["start_sector"] % 2048 == 0
    from server.library.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"] <= TARGET_BYTES, (m["min_target_bytes"], TARGET_BYTES)


def test_the_recovery_is_never_measured_written_or_moved_on_the_source(tmp_path):
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN)
    assert out.strip().endswith("rc=0"), out
    assert not [s for s in order(run) if s.startswith("ntfsresize") and s.endswith("sda4")]
    assert not [s for s in order(run) if "lc-src-4" in s], "no RAM view of the recovery is made"
    # #1466: the source table is never written -- the tail lives in the manifest only
    assert rewrites(run) == [], rewrites(run)
    assert source_writes(order(run), box) == [], source_writes(order(run), box)


def test_a_packed_c_is_not_shrunk_and_the_shortfall_is_named(tmp_path):
    """No room for the tail: nothing is asked or written -- and with the
    target known the capture refuses by name, with how many bytes."""
    box, run, out = _capture(tmp_path, PACKED_MIN, env={"CAPTURE_TARGET_BYTES": str(TARGET_BYTES)})
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason, reason
    short = _layout_need([(2048, 204800 * 512), (WIN_START, WIN_BYTES), (REC_START, REC_SECTORS * 512)]) - TARGET_BYTES
    assert f"יש לכווץ {short} בייט" in reason, reason
    assert "asked" not in order(run) and not rewrites(run)
    assert not [s for s in order(run) if s.startswith("ntfsresize -s")]


def test_room_already_after_the_layout_needs_no_shrink(tmp_path):
    """100MiB unallocated at the end of the source: the tail is already there,
    nobody is asked and the disk is only read."""
    box, run, out = _capture(tmp_path, NEARLY_FULL_MIN, disk_sectors=REC_END + 33 + 100 * MIB // 512)
    assert out.strip().endswith("rc=0"), out
    assert "asked" not in order(run) and not rewrites(run)
    assert part(manifest(run), 3)["size_bytes"] == WIN_BYTES


# --- _shrink_tail, the rule alone ---------------------------------------------

def _tail(tmp_path: Path, meas: list[str], slack: int, tail: int = TAIL) -> tuple[list[str], str]:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    (run / "meas").write_text("".join(f"{m}\n" for m in meas), encoding="utf-8", newline="\n")
    out = sh(f"export RUN_DIR={posix(run)!r} SHRINK_TAIL_BYTES={tail}; . {posix(AGENT)}/lib/shrink/shrinkplan.sh; "
             f"_shrink_tail {posix(run / 'meas')!r} {slack}")
    return out.splitlines(), (run / "shrink.tail").read_text(encoding="utf-8").strip()


GB = 10**9


def test_a_regular_shrink_bigger_than_the_tail_is_left_alone(tmp_path):
    plan, took = _tail(tmp_path, [f"3|g|u|100|200|{200 * GB}|{110 * GB}|{100 * GB}"], 0)
    assert plan == [f"3|g|u|100|200|{200 * GB}|{110 * GB}"] and took == ""


def test_the_last_partition_with_room_takes_the_tail_rounded_up_to_a_mebibyte(tmp_path):
    """C: before D:; D: has room -> D: takes it (it is the one that is
    stretched back on a larger drive). The slack is subtracted, then rounded up."""
    plan, took = _tail(tmp_path, [f"3|g|u|100|200|{200 * GB}|{200 * GB}|{199 * GB}",
                                  f"4|g|u|900|200|{50 * GB}|{50 * GB}|{49 * GB}"], 369_152)
    assert plan == [f"4|g|u|900|200|{50 * GB}|{50 * GB - TAIL}"], plan
    assert took == f"4|{TAIL}"


def test_a_packed_last_partition_passes_the_tail_to_the_one_before(tmp_path):
    plan, took = _tail(tmp_path, [f"3|g|u|100|200|{200 * GB}|{200 * GB}|{199 * GB}",
                                  f"4|g|u|900|200|{50 * GB}|{50 * GB}|{50 * GB - 100 * MIB}"], 0)
    assert plan == [f"3|g|u|100|200|{200 * GB}|{200 * GB - TAIL}"], plan
    assert took == f"3|{TAIL}"


def test_a_small_regular_shrink_is_topped_up_not_doubled(tmp_path):
    plan, _ = _tail(tmp_path, [f"3|g|u|100|200|{200 * GB}|{200 * GB - 10 * MIB}|{190 * GB}"], 0)
    assert plan == [f"3|g|u|100|200|{200 * GB}|{200 * GB - TAIL}"], plan


def test_no_room_anywhere_and_switched_off_both_leave_the_plan_as_it_was(tmp_path):
    packed = [f"3|g|u|100|200|{200 * GB}|{200 * GB}|{200 * GB - 100 * MIB}"]
    assert _tail(tmp_path, packed, 0) == ([], "")
    roomy = [f"3|g|u|100|200|{200 * GB}|{200 * GB}|{190 * GB}"]
    assert _tail(tmp_path, roomy, 0, tail=0) == ([], "")
