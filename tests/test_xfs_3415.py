"""#3415 (part 1: agent, server, build) -- xfs is captured and restored with `partclone.xfs`.

Until here xfs (RHEL, Rocky, Alma, Fedora Server: the DEFAULT install) was refused at the capture gate (#1900, #1961), and
`partclone_for_fs` mapped it to `partclone.dd` -- the whole partition, byte for byte -- so the refusal was the right call: there
was no tool that read only the used blocks. `partclone.xfs` is in Debian's partclone and was not in the initramfs.

What changed, one test section each:

1. the build: `partclone.xfs` is in `BINARIES` of tools/build_initramfs.sh -- and every tool `partclone_for_fs` can name is
   (a mapping to a tool the initramfs does not carry fails on the metal, "not found", mid-restore);
2. the capture gate lets xfs through to the space check; what is still unsupported is still refused;
3. the root of an xfs source is probed with `ro,norecovery` (a bare `ro` replays the log: a write to the source);
4. the restored root is mounted `nodev,nosuid,nouuid`: every cloner's copy carries the same xfs UUID;
5. the real tool: a real xfs on a file, `partclone.xfs -c -a0 -z 524288` (the capture's flags) -> zstd ->
   `zstd -dc | partclone.xfs -r` (the restore's pipe) onto a drive that held something else: the filesystem's own checker
   passes it, and (as root, a loop device, as the Testrunner is) the grow after it (`finish_grow`, `xfs_growfs`) stretches it to
   the whole partition with every file still there.

Not proved here: the chunk engine's road for xfs (part 2, its own PR), and a real distribution booting from the restored
disk (the VM step of #3415).

Negative control (bakara-shlilit): restore.sh, capturegate.sh, manifest.sh, hostname_linux.sh, build_initramfs.sh,
images.py and vmdisk.py back to lab/vm-1114f -- every test below that names xfs fails; the ones that do not stay green.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native, with_sbin
from test_agent import AGENT, BASH, REPO, posix, sh
from test_capture_gates import CURL_LOG, ONE_PARTITION, _blkid_fs, capture_run, refusal_reason
from test_captured_stdout import LINUX_PLAN
from test_hostname_linux_dual import box as name_box, name
from test_json_escape import BUSYBOX
from test_ro_mount_no_replay_2430 import WANT, mounted_with, options

SBIN_PATH = with_sbin(os.environ.get("PATH", "/usr/bin:/bin"))
XFS_PLAN = LINUX_PLAN.replace("|ext4|", "|xfs|")


def _tool(name: str):
    return shutil.which(name, path=SBIN_PATH)


# --- 1: the build ------------------------------------------------------------------------------------------


def _binaries() -> set[str]:
    text = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    block = re.search(r"^BINARIES=\((.*?)\)", text, re.S | re.M)
    assert block, "BINARIES=( ... ) not found in tools/build_initramfs.sh"
    return set(re.sub(r"#.*", "", block.group(1)).split())


@requires_native(("bash", BASH))
def test_partclone_xfs_is_packed_into_the_initramfs():
    assert "partclone.xfs" in _binaries()


@requires_native(("bash", BASH))
@pytest.mark.parametrize("fs", ["ntfs", "vfat", "ext4", "ext3", "btrfs", "xfs", "swap", "unknown", "BitLocker"])
def test_every_tool_the_agent_names_for_a_supported_filesystem_is_in_the_initramfs(fs):
    tool = sh(f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/restore/restore.sh; partclone_for_fs {fs}").strip()
    assert tool in _binaries(), f"partclone_for_fs {fs} = {tool}, which the initramfs does not carry"


# --- 2: the capture gate -----------------------------------------------------------------------------------


@requires_native(("bash", BASH))
def test_an_xfs_partition_goes_on_to_the_space_check(tmp_path):
    curl = CURL_LOG.replace('"status":"ok","reason":"space checked"', '"status":"refused","reason":"STOP-AT-SPACE"')
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": ONE_PARTITION, "blkid": _blkid_fs("xfs"),
                                                             "curl": curl})
    assert "preflight" in (run / "curl.log").read_text(encoding="utf-8"), "xfs never reached the space check"
    reason = refusal_reason(box, run, out)
    assert "STOP-AT-SPACE" in reason and "אינה נתמכת" not in reason, reason


@requires_native(("bash", BASH))
@pytest.mark.parametrize("fs", ["crypto_LUKS", "LVM2_member", "ext3", "ext2", "exfat", "ReFS"])
def test_what_is_still_unsupported_is_still_refused_and_the_message_lists_xfs(tmp_path, fs):
    box, run, out = capture_run(tmp_path, raid=True, stubs={"sgdisk": ONE_PARTITION, "blkid": _blkid_fs(fs),
                                                             "curl": CURL_LOG})
    reason = refusal_reason(box, run, out)
    assert f"מערכת הקבצים {fs} אינה נתמכת" in reason and "btrfs, xfs)" in reason, reason


def test_the_server_accepts_an_xfs_manifest_and_the_two_lists_agree():
    from server.library.images import FILESYSTEMS
    from server.vm import vmdisk
    assert "xfs" in FILESYSTEMS
    assert vmdisk.partclone_for_fs("xfs") == "partclone.xfs"
    text = (AGENT / "lib" / "capture" / "capturegate.sh").read_text(encoding="utf-8")
    listed = set(re.search(r'^CAPTURE_FILESYSTEMS="([^"]*)"', text, re.M).group(1).split())
    assert listed == FILESYSTEMS - {""}


# --- 3: the probe of an xfs source never replays its log ----------------------------------------------------


@requires_native(("bash", BASH))
def test_the_capture_source_root_probe_of_xfs_never_replays(tmp_path):
    got = mounted_with(tmp_path, "_linux_system 3 /dev/sda3 xfs", ("common", "manifest"))
    assert got, "the probe did not mount at all -- xfs is not probed as a Linux root"
    assert options(got) == {WANT["xfs"]}, got


# --- 4: the restored root ----------------------------------------------------------------------------------


@requires_native(("bash", BASH))
def test_the_restored_xfs_root_is_mounted_with_nouuid_and_the_others_are_not(tmp_path):
    script, run = name_box(tmp_path / "xfs", [XFS_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    mounts = [m for m in (run / "mounts").read_text().splitlines() if m.startswith("mount ")]
    assert mounts and all("-t xfs -o nodev,nosuid,nouuid " in m for m in mounts), mounts
    script, run = name_box(tmp_path / "ext4", [LINUX_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    mounts = [m for m in (run / "mounts").read_text().splitlines() if m.startswith("mount ")]
    assert mounts and all("nouuid" not in m and "-o nodev,nosuid " in m for m in mounts), mounts


# --- 5: the real tool, on a real xfs -----------------------------------------------------------------------

SIZE = 320 << 20                       # xfs's floor is 300 MiB
BIG = 2 * SIZE
GARBAGE = b"\xa5"
FILES = {
    "big.bin": os.urandom(9 * 1024 * 1024 + 123),
    "zeros.bin": b"\0" * (5 * 1024 * 1024),
    "etc/hostname": b"BUILD-01\n",
    "deep/a/b/note.txt": b"xfs through partclone\n" * 1000,
}
TOOLS = ("mkfs.xfs", "partclone.xfs", "xfs_repair", "xfs_db", "xfs_growfs", "zstd")
REAL = requires_native(*[(t, _tool(t)) for t in TOOLS], posix=True, why="a real xfs and the real partclone.xfs")
needs_root = requires_native(("sudo -n", os.name != "nt" and bool(shutil.which("sudo"))
                              and subprocess.run(["sudo", "-n", "true"], capture_output=True,
                                                 stdin=subprocess.DEVNULL).returncode == 0),
                             ("losetup", _tool("losetup")), ("busybox", BUSYBOX), posix=True,
                             why="xfs grows only mounted: a loop device, as root (the Testrunner)")


def _run(*cmd, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    return subprocess.run([str(c) for c in cmd], env={**os.environ, "PATH": SBIN_PATH},
                          stdin=subprocess.DEVNULL, **kw)


def _proto(tree: Path) -> str:
    """The tool's protofile (`-p`) for a directory: xfsprogs of Debian 13 takes a file, not a tree."""
    def walk(d: Path) -> list[str]:
        out = []
        for child in sorted(d.iterdir()):
            if child.is_dir():
                out += [f"{child.name} d--755 0 0", *walk(child), "$"]
            else:
                out.append(f"{child.name} ---644 0 0 {child}")
        return out
    return "\n".join(["/dev/null", "0 0", "d--755 0 0", *walk(tree), "$", ""])


def _make_xfs(tmp: Path) -> Path:
    tree = tmp / "tree"
    for name, data in FILES.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(data)
    proto = tmp / "proto"
    proto.write_text(_proto(tree), encoding="utf-8", newline="\n")
    src = tmp / "src.img"
    with src.open("wb") as fh:
        fh.truncate(SIZE)
    made = _run(_tool("mkfs.xfs"), "-q", "-f", "-p", proto, src)
    assert made.returncode == 0, made.stderr
    return src


def _garbage(path: Path, size: int) -> Path:
    block = GARBAGE * (4 << 20)
    with path.open("wb") as fh:
        for _ in range(size // len(block)):
            fh.write(block)
    return path


def _capture(src: Path, tmp: Path) -> Path:
    """What capture.sh runs: partclone.xfs with capture_pcl_flags' flags, into zstd."""
    packed = tmp / "p2.linux.pcl.zst"
    read = subprocess.Popen([_tool("partclone.xfs"), "-c", "-a0", "-z", "524288", "-s", str(src), "-L", str(tmp / "c.log")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with packed.open("wb") as fh:
        z = subprocess.run([_tool("zstd"), "-q", "-3"], stdin=read.stdout, stdout=fh, stderr=subprocess.PIPE)
    read.stdout.close()
    assert read.wait(timeout=300) == 0, (tmp / "c.log").read_text(errors="replace")
    assert z.returncode == 0, z.stderr
    return packed


def _restore(packed: Path, target: Path, tmp: Path) -> None:
    """The restore's pipe: zstd -dc | partclone.xfs -r."""
    plain = subprocess.Popen([_tool("zstd"), "-dc", str(packed)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    write = subprocess.run([_tool("partclone.xfs"), "-r", "-s", "-", "-O", str(target), "-L", str(tmp / "r.log")],
                           stdin=plain.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plain.stdout.close()
    assert plain.wait(timeout=300) == 0 and write.returncode == 0, (write.stderr, (tmp / "r.log").read_text(errors="replace"))


def _clean(image: Path) -> None:
    p = _run(_tool("xfs_repair"), "-n", image)
    assert p.returncode == 0, (p.returncode, p.stdout.decode(errors="replace")[-2000:], p.stderr.decode(errors="replace")[-2000:])


def _sb(image: Path) -> dict:
    p = _run(_tool("xfs_db"), "-r", "-c", "sb 0", "-c", "print blocksize dblocks uuid", image)
    assert p.returncode == 0, p.stderr
    return dict((k.strip(), v.strip()) for k, v in (line.split("=", 1) for line in p.stdout.decode().splitlines() if "=" in line))


def _fs_bytes(image: Path) -> int:
    sb = _sb(image)
    return int(sb["blocksize"]) * int(sb["dblocks"])


@REAL
def test_a_real_xfs_captured_and_restored_by_partclone_xfs_is_clean_and_has_its_identity(tmp_path):
    src = _make_xfs(tmp_path)
    assert _fs_bytes(src) <= SIZE
    packed = _capture(src, tmp_path)
    assert packed.stat().st_size < SIZE // 4, "the image is as big as the partition: the used blocks were not what was read"
    target = _garbage(tmp_path / "target.disk", SIZE)
    _restore(packed, target, tmp_path)
    _clean(target)
    assert _sb(target)["uuid"] == _sb(src)["uuid"], "the restored xfs is not the captured one"
    for name, data in FILES.items():
        p = _run(_tool("xfs_db"), "-r", "-c", f"path /{name}", "-c", "print core.size", target)
        assert p.returncode == 0 and f"core.size = {len(data)}" in p.stdout.decode(), (name, p.stdout, p.stderr)


#: finish_grow as drawers.sh calls it, on a loop device over the target; only the identity check (#2434), the partition-name
#: mark (#1798) and the drawer's state file are stubbed -- as test_chunk_linux_fs_3166 does for ext4 and btrfs.
GROW = r'''set -u
loop=""
cleanup() { umount "$W/mnt" 2>/dev/null; [ -z "$loop" ] || losetup -d "$loop"; chown -R "$OWNER" "$W"; }
trap cleanup EXIT
loop=$(losetup -f --show "$IMG") || exit 91
[ "$(losetup -n -O BACK-FILE "$loop")" = "$IMG" ] || { echo "$loop is not backed by $IMG" >&2; exit 92; }
mkdir -p "$W/run/targets/sda" "$W/mnt" || exit 93
echo "2|xfs" > "$W/run/targets/sda/expanded"
RUN_DIR="$W/run" LOOP="$loop" "$BB" sh -c '
    . "$AGENT/lib/core/common.sh"; . "$AGENT/lib/restore/grow.sh"
    partition_node() { echo "$LOOP"; }
    assert_target() { :; }; writemark_release() { :; }
    target_set() { printf "%s|%s\n" "$2" "$3" > "$RUN_DIR/target_state"; }
    finish_grow sda "" ""' > "$W/grow.out" 2>&1
echo $? > "$W/rc"
mount -t xfs -o ro,norecovery,nouuid "$loop" "$W/mnt" || exit 94
for f in big.bin zeros.bin etc/hostname deep/a/b/note.txt; do cmp "$W/mnt/$f" "$SRCTREE/$f" || { echo "differs: $f" >> "$W/rc.files"; }; done
'''


@REAL
@needs_root
def test_finish_grow_stretches_a_restored_xfs_to_the_whole_partition_with_its_files(tmp_path):
    src = _make_xfs(tmp_path)
    packed = _capture(src, tmp_path)
    target = _garbage(tmp_path / "big.disk", BIG)
    _restore(packed, target, tmp_path)
    assert _fs_bytes(target) <= SIZE                          # as captured: the grow has something to do
    w = tmp_path / "w"
    w.mkdir()
    env = {"W": str(w), "IMG": str(target), "AGENT": str(AGENT), "BB": BUSYBOX, "SRCTREE": str(tmp_path / "tree"),
           "OWNER": f"{os.getuid()}:{os.getgid()}", "PATH": SBIN_PATH}
    proc = subprocess.run(["sudo", "-n", "env", *[f"{k}={v}" for k, v in env.items()], "sh", "-c", GROW],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)          # the harness itself, not the agent
    out = (w / "grow.out").read_text(errors="replace") + (w / "run" / "agent.log").read_text(errors="replace")
    assert (w / "rc").read_text().strip() == "0", out
    assert (w / "run" / "target_state").read_text() == "done|\n", out
    assert not (w / "rc.files").exists(), (w / "rc.files").read_text()
    assert _fs_bytes(target) > SIZE, out                                  # grown past what was captured
    _clean(target)
