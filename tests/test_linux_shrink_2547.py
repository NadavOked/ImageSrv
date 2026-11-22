"""Capture geometry and filesystem tools on a disposable snapshot (#2547)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from server.library.minfree import min_disk_bytes, system_candidates, system_partition

ROOT = Path(__file__).resolve().parents[1]


def shell(tmp_path, body, stubs, **env):
    for name, script in stubs.items():
        tool = tmp_path / name
        tool.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8", newline="\n")
        tool.chmod(0o755)
    cmd = 'export PATH="$PWD:/usr/bin:$PATH" RUN_DIR="$PWD" LOG_FILE="$PWD/log"; ' + body
    return subprocess.run([shutil.which("sh") or "sh", "-c", cmd], cwd=tmp_path,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, **env})


EXT = f'. "{ROOT.as_posix()}/agent/lib/shrink/shrinkext4.sh"; '
HEADER = 'printf "Block count:  %s\\nBlock size:   4096\\nFree blocks:  5\\n" "$COUNT"'
STUBS = {"dumpe2fs": HEADER, "e2fsck": 'echo "$*" >> calls; exit "$CHECK"',
         "resize2fs": 'echo "$*" >> calls; exit "$RESIZE"'}


@pytest.mark.parametrize("check,resize,count,error", [
    ("0", "0", "16", ""), ("1", "0", "16", ""), ("4", "0", "16", "e2fsck"),
    ("2", "0", "16", "e2fsck"), ("0", "1", "16", "resize2fs"),
    ("0", "0", "", "dumpe2fs"), ("0", "0", "bad", "dumpe2fs"),
    ("0", "0", "17", "הגאומטריה"),
])
def test_ext4_view(tmp_path, check, resize, count, error):
    result = shell(tmp_path, EXT + 'shrink_ext4 view 65536 || { echo "$LC_ERROR"; exit 1; }',
                   STUBS, CHECK=check, RESIZE=resize, COUNT=count)
    assert result.returncode == bool(error), result.stderr
    assert error in result.stdout
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[0] == "-f -y view"
    assert (calls[1:] == []) == (check in ("2", "4"))
    if check in ("0", "1"):
        assert calls[1] == "-M view"


@pytest.mark.parametrize("count,ok", [("16", True), ("", False), ("bad", False)])
def test_ext4_measure(tmp_path, count, ok):
    result = shell(tmp_path, EXT + 'ext4_geometry source used', STUBS, COUNT=count)
    assert (result.returncode == 0) == ok, result.stderr
    if ok:
        assert result.stdout.strip() == str(11 * 4096)


@pytest.mark.parametrize("fs,used", [("ext4", 860856320), ("btrfs", 870686720)])
def test_linux_capture_floor_and_separate_boot(fs, used):
    # Layout and usage from the 20 November BIOS/GPT captures.
    root = dict(index=2, role="linux", fs=fs, start_sector=4096,
                size_bytes=8586788864, used_bytes=used, expandable=True, system=True)
    boot = dict(index=1, role="data", fs="unknown", start_sector=2048, size_bytes=1048576)
    manifest = dict(source_disk_bytes=8589934592, sector_size=512, partitions=[boot, root])
    assert min_disk_bytes(manifest, 1) == 8589148160
    root.update(shrunk_from_bytes=root["size_bytes"], size_bytes=3 << 30)
    floor = (3 << 30) + (4096 * 512) + 262144
    assert min_disk_bytes(manifest, 1) == floor
    assert min_disk_bytes(manifest, 4) == floor + used + (1 << 30)
    assert system_candidates(manifest) == []
    boot.update(role="linux", fs="ext4")
    assert system_candidates(manifest) == [boot, root]
    assert system_partition(manifest) is root
    root.pop("system")
    assert min_disk_bytes(manifest, 1) is None


BTRFS = f'. "{ROOT.as_posix()}/agent/lib/shrink/shrinkbtrfs.sh"; '
BTRFS_TOOL = """
echo "$*" >> calls
case "$1 $2" in
  "inspect-internal dump-super")
    printf 'num_devices %s\\nlog_root %s\\n' "$DEVICES" "$JOURNAL"
    printf 'dev_item.devid 2\\nsectorsize 4096\\ndev_item.total_bytes %s\\n' "$TOTAL" ;;
  "filesystem show") echo "devid 2 size 1048576 used 65536 path $MOUNTED" ;;
  "filesystem resize") exit "$RESIZE" ;;
  *) exit 7 ;;
esac
"""


@pytest.mark.parametrize("overrides,error", [
    ({}, ""), ({"DEVICES": "2"}, "מהתקן אחד"), ({"JOURNAL": "4096"}, "יומן"),
    ({"TOTAL": ""}, "הגאומטריה"), ({"TOTAL": "bad"}, "הגאומטריה"),
    ({"TOTAL": "131072"}, "הגאומטריה"), ({"MOUNTED": "source"}, "אינו העותק"),
    ({"RESIZE": "1"}, "resize rc=1"), ({"UMOUNT": "1"}, "umount rc=1"),
])
def test_btrfs_view(tmp_path, overrides, error):
    for name in ("source", "view"):
        (tmp_path / name).touch()
    env = dict(DEVICES="1", JOURNAL="0", TOTAL="65536", MOUNTED="view", RESIZE="0", UMOUNT="0")
    result = shell(tmp_path, BTRFS + 'shrink_btrfs view 65536 || { echo "$LC_ERROR"; exit 1; }',
                   {"btrfs": BTRFS_TOOL, "stat": 'case "$4" in view) echo fe:1 ;; source) echo 8:1 ;; *) exit 1 ;; esac',
                    "mount": 'echo "mount $*" >> calls',
                    "umount": 'echo umount >> calls; exit "$UMOUNT"'}, **(env | overrides))
    assert result.returncode == bool(error), result.stderr
    assert error in result.stdout
    calls = (tmp_path / "calls").read_text()
    if "mount -t" in calls:
        assert "umount" in calls
    if error in ("מהתקן אחד", "יומן", "אינו העותק"):
        assert "remount,rw" not in calls
        assert "filesystem resize" not in calls
    else:
        assert "filesystem resize 2:65536" in calls


@pytest.mark.parametrize("fs", ["ext4", "btrfs", "xfs"])
def test_capture_plan_for_linux_root(tmp_path, fs):
    lib = f"{ROOT.as_posix()}/agent/lib"
    code = f"""
    . "{lib}/capture/manifest.sh"
    . "{lib}/shrink/shrinkplan.sh"
    for helper in shrinkext4 shrinkbtrfs; do
        [ ! -f "{lib}/shrink/$helper.sh" ] || . "{lib}/shrink/$helper.sh"
    done
    SYSROOT="$PWD"
    mkdir -p sys/block/sda
    echo 16777216 > sys/block/sda/size
    echo '2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|u|4096|16771072' > parts
    partition_node() {{ echo source; }}
    _fs_of() {{ echo "$FS"; }}
    log() {{ echo "$*" >&2; }}
    shrink_plan sda parts 512 || {{ echo "$SHRINK_ERROR"; exit 1; }}
    cat shrink.plan
    """
    result = shell(tmp_path, code, {"dumpe2fs": HEADER, "btrfs":
                   'printf "num_devices 1\\nlog_root 0\\nbytes_used 1048576\\n"'}, COUNT="261", FS=fs)
    if fs == "xfs":
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""   # unsupported fs: left out of the plan, capture still works
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().split("|")[-1] == str(2148532224)


@pytest.mark.parametrize("wrong_cut", [False, True])
def test_snapshot_publishes_verified_cut(tmp_path, wrong_cut):
    from test_shrink_on_capture import BLOCKDEV_OK, DMSETUP_STUB, MODPROBE_STUB, RMMOD_STUB

    lib = f"{ROOT.as_posix()}/agent/lib"
    code = EXT + f"""
    . "{lib}/capture/manifest.sh"
    . "{lib}/shrink/shrink.sh"
    . "{lib}/localclone/lcsnap.sh"
    export DEVROOT="$PWD/dev" SYSROOT="$PWD" LC_SNAP_RUN="$PWD/run"
    mkdir -p "$LC_SNAP_RUN" "$DEVROOT" sys/class/block/sda2 proc
    touch "$DEVROOT/sda2"
    echo 32768 > sys/class/block/sda2/size
    echo 8:2 > sys/class/block/sda2/dev
    echo 'MemAvailable: 4194304 kB' > proc/meminfo
    echo '2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|u|4096|32768' > run/parts.txt
    sed 's/$/|16777216|65536/' run/parts.txt > run/shrink.plan
    partition_node() {{ echo "$DEVROOT/sda2"; }}
    _fs_of() {{ echo ext4; }}
    log() {{ echo "$*" >> "$LOG_FILE"; }}
    lc_snap_shrink sda || {{ echo "$LC_SNAP_WHY $LC_ERROR"; exit 1; }}
    cat run/parts.txt run/snap/2.node
    """
    blockdev = "echo 0" if wrong_cut else BLOCKDEV_OK
    result = shell(tmp_path, code, STUBS | {"dmsetup": DMSETUP_STUB, "modprobe": MODPROBE_STUB,
                   "rmmod": RMMOD_STUB, "blockdev": blockdev}, CHECK="0", RESIZE="0", COUNT="16")
    assert result.returncode == int(wrong_cut), result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "/mapper/lc-src-2" in calls and "sda2" not in calls
    if wrong_cut:
        assert "65536" in result.stdout
    else:
        assert "|4096|128" in result.stdout and "/mapper/lc-cut-2" in result.stdout


