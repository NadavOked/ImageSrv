"""#3166 -- ext4 and btrfs through the chunk engine, and the grow after it.

Until here only NTFS, FAT and dd went through `chunkcast` (test_chunkcast_2533.py,
test_chunkcut_2533.py), and "the code is generic" is not evidence (principle 5).
This file runs the road a Linux partition takes on a cloner, with the real tools
and on files:

    a real filesystem with files in it
      -> partclone.<fs> -c -a0 -z 524288      (the capture's flags, capture_pcl_flags)
      -> chunkcut -l 3 -T 0                   (format 2: the head, a zstd frame a 4MiB chunk, the index)
      -> chunkcast send / chunkcast recv      (loopback; the target is a file filled with garbage)

and judges the target three ways, none of which is the engine's own word:

* byte for byte, it is what the OLD pipe writes to the same garbage
  (`zstd -dc | partclone.<fs> -r`) -- the engine changes nothing a Linux
  partition is made of;
* the filesystem's own checker passes it (`e2fsck -fn`, `btrfs check --readonly`);
* every file comes back out of it, read by the filesystem's own tools
  (`debugfs`, `btrfs restore`), not by a mount of ours.

The second half is `finish_grow` (agent/lib/restore/grow.sh) on a target twice the
size of the source, the same function drawers.sh calls after either engine: the
filesystem the engine wrote grows to the whole partition (read back from the
superblock), stays clean, keeps its files, and the drawer is `done` with no
warning. btrfs grows only mounted, so this half is root: a loop device on a file
of ours (checked to be ours before anything is written), the Testrunner's `sudo -n`.

Not proved here: a real disk and a real distribution booting from it (the VM and
metal steps of #3166), and an image captured on a build machine.
"""

from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import chunkcast_rig as C
from native import requires_native, with_sbin
from test_agent import AGENT
from test_json_escape import BUSYBOX

SBIN_PATH = with_sbin(os.environ.get("PATH", "/usr/bin:/bin"))
MKFS = {"ext4": "mkfs.ext4", "btrfs": "mkfs.btrfs"}
PCL = {"ext4": "partclone.ext4", "btrfs": "partclone.btrfs"}
SIZE = 256 << 20                     # above mkfs.btrfs's floor for a single device
BIG = 2 * SIZE                       # the cloner's drive: twice the source
PART = 2


def _tool(name: str):
    return shutil.which(name, path=SBIN_PATH)


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


TOOLS = ("gcc", "zstd", *MKFS.values(), *PCL.values(), "e2fsck", "debugfs", "dumpe2fs", "btrfs")
pytestmark = requires_native(*[(t, _tool(t)) for t in TOOLS], posix=True,
                             why="real ext4/btrfs, partclone, and chunkcast built with gcc")
needs_root = requires_native(("sudo -n", _sudo_ok()), ("losetup", _tool("losetup")),
                             ("busybox", BUSYBOX), posix=True,
                             why="btrfs grows only mounted: a loop device, as root (the Testrunner)")

FILES = {
    "big.bin": os.urandom(9 * 1024 * 1024 + 123),           # incompressible, across chunk boundaries
    "zeros.bin": b"\0" * (5 * 1024 * 1024),
    "etc/hostname": b"BUILD-01\n",
    "deep/a/b/note.txt": b"image_format 2, chunk engine\n" * 1000,
}


def _run(*cmd, **kw) -> subprocess.CompletedProcess:
    stdin = kw.pop("stdin", subprocess.DEVNULL)               # explicit at the call, where test_subprocess_stdin looks (#541)
    kw.setdefault("capture_output", True)
    return subprocess.run([str(c) for c in cmd], env={**os.environ, "PATH": SBIN_PATH}, stdin=stdin, **kw)


def _garbage(path: Path, size: int) -> Path:
    """A target as a drive that held something else: every byte C.GARBAGE."""
    block = bytes([C.GARBAGE]) * (4 << 20)
    with path.open("wb") as fh:
        for _ in range(size // len(block)):
            fh.write(block)
    return path


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    """fanout + chunkcast (the rig's), and chunkcut -- built as tools/build_initramfs.sh builds them."""
    root = C.build_binaries(tmp_path_factory.mktemp("bin"))
    p = _run("gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", root / "chunkcut",
             C.REPO / "agent/chunkcut.c", "-l:libzstd.so.1")
    assert p.returncode == 0, p.stderr
    return root


def _make_fs(fs: str, tmp: Path) -> Path:
    tree = tmp / "tree"
    for name, data in FILES.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_bytes(data)
    src = tmp / "src.img"
    with src.open("wb") as fh:
        fh.truncate(SIZE)
    if fs == "ext4":
        made = _run(_tool(MKFS[fs]), "-q", "-F", "-b", "4096", "-E", "lazy_itable_init=0,lazy_journal_init=0",
                    "-d", tree, src)
    else:
        made = _run(_tool(MKFS[fs]), "-q", "-f", "--rootdir", tree, src)
    assert made.returncode == 0, made.stderr
    return src


def _capture(cc: Path, fs: str, src: Path, tmp: Path) -> SimpleNamespace:
    """partclone -c | chunkcut, as capture.sh runs them. The result has what chunkcast_rig's
    recv/send read off an image: kind, the partition file, its index and that index's XXH64, the head."""
    index, packed = tmp / "idx", tmp / f"p{PART}.linux.pcl.zst"
    read = subprocess.Popen([_tool(PCL[fs]), "-c", "-a0", "-z", "524288", "-s", str(src), "-L", str(tmp / "c.log")],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with packed.open("wb") as fh:
        cut = subprocess.run([str(cc / "chunkcut"), "-l", "3", "-T", "0", "-i", str(index)],
                             stdin=read.stdout, stdout=fh, stderr=subprocess.PIPE)
    read.stdout.close()
    assert read.wait(timeout=300) == 0, (tmp / "c.log").read_text(errors="replace")
    assert cut.returncode == 0, cut.stderr
    first = index.read_text(encoding="ascii").splitlines()[0].split()
    assert first[2] == "partclone" and int(first[8]) >= 3, first      # a head and more than one chunk
    head = tmp / "head.zst"
    head.write_bytes(packed.read_bytes()[:int(first[5])])           # what chunkrecv.sh downloads by Range
    return SimpleNamespace(kind="partclone", file=packed, idx=index, head_z=head,
                           index_xxh=Path(str(index) + ".xxh64").read_text(encoding="ascii").strip())


def _through_the_engine(cc: Path, img: SimpleNamespace, target: Path, tmp: Path) -> None:
    """A first pass, and the repair passes the server would order for what it did not make durable: a loaded
    test host's file can be "behind" (`skipped`, #3141/#3170) as a slow drive is, and that is the engine's way,
    not a failure. Every pass is the real receiver and sender; the list of a repair pass is what is missing."""
    session, state = C.new_session(), tmp / "state"
    chunks = int(img.idx.read_text(encoding="ascii").split()[8])
    missing: set[int] = set(range(chunks))
    for n in range(1, 5):
        port = C.free_port()
        r = C.recv(cc, img, [target], f"127.0.0.1:{port}", session, state, tag=tmp / f"recv{n}", part=PART, pass_=n)
        s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp / f"send{n}", part=PART, pass_=n,
                   chunks=None if n == 1 else C.ranges_of(missing))
        assert s.wait(120) == 0, s.log()
        assert r.wait(120) == 0, r.log()
        got = C.state_of(state)[str(target)]
        assert got["alive"], (got, r.log())
        missing = set(range(chunks)) - got["durable"]
        if not missing:
            return
    raise AssertionError(f"chunks {sorted(missing)} still missing after four passes")


def _through_the_old_pipe(fs: str, img: SimpleNamespace, target: Path, tmp: Path) -> None:
    plain = subprocess.Popen(["zstd", "-dc", str(img.file)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    write = subprocess.run([_tool(PCL[fs]), "-r", "-s", "-", "-O", str(target), "-L", str(tmp / "r.log")],
                           stdin=plain.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plain.stdout.close()
    assert plain.wait(timeout=300) == 0 and write.returncode == 0, write.stderr


def _clean(fs: str, image: Path) -> None:
    if fs == "ext4":
        p = _run(_tool("e2fsck"), "-f", "-n", image)
    else:
        p = _run(_tool("btrfs"), "check", "--readonly", image)
    assert p.returncode == 0, (fs, p.returncode, p.stdout.decode(errors="replace")[-2000:],
                               p.stderr.decode(errors="replace")[-2000:])


def _files_back(fs: str, image: Path, tmp: Path) -> None:
    """Every file, read out of the image by the filesystem's own tools -- no mount of ours."""
    if fs == "ext4":
        for name, data in FILES.items():
            p = _run(_tool("debugfs"), "-R", f"cat /{name}", image)
            assert p.returncode == 0 and p.stdout == data, (name, len(p.stdout), p.stderr[-500:])
        return
    out = tmp / "restored"
    out.mkdir()
    p = _run(_tool("btrfs"), "restore", image, out)
    assert p.returncode == 0, p.stderr.decode(errors="replace")[-2000:]
    for name, data in FILES.items():
        assert (out / name).read_bytes() == data, name


def _fs_bytes(fs: str, image: Path) -> int:
    """The size the filesystem says it has, from its superblock."""
    if fs == "ext4":
        p = _run(_tool("dumpe2fs"), "-h", image)
        assert p.returncode == 0, p.stderr
        head = dict(line.split(":", 1) for line in p.stdout.decode().splitlines() if ":" in line)
        return int(head["Block count"]) * int(head["Block size"])
    p = _run(_tool("btrfs"), "inspect-internal", "dump-super", image)
    assert p.returncode == 0, p.stderr
    fields = dict(line.split(None, 1) for line in p.stdout.decode().splitlines() if line.strip() and len(line.split(None, 1)) == 2)
    return int(fields["total_bytes"])


@pytest.mark.parametrize("fs", ["ext4", "btrfs"])
def test_a_real_linux_partition_through_the_chunk_engine_is_the_old_pipes_bytes_clean_and_whole(cc, tmp_path, fs):
    src = _make_fs(fs, tmp_path)
    img = _capture(cc, fs, src, tmp_path)
    engine, old = _garbage(tmp_path / "engine.disk", SIZE), _garbage(tmp_path / "old.disk", SIZE)
    _through_the_engine(cc, img, engine, tmp_path)
    _through_the_old_pipe(fs, img, old, tmp_path)
    assert filecmp.cmp(engine, old, shallow=False), "the chunk engine wrote other bytes than partclone -r"
    _clean(fs, engine)
    _files_back(fs, engine, tmp_path)


#: finish_grow as drawers.sh calls it, on a loop device over the target. Only what is not the grow is
#: stubbed: the identity check (#2434), the partition-name mark (#1798) and the drawer's state file.
GROW = r'''set -u
loop=""
cleanup() { [ -z "$loop" ] || losetup -d "$loop"; chown -R "$OWNER" "$W"; }
trap cleanup EXIT
loop=$(losetup -f --show "$IMG") || exit 91
[ "$(losetup -n -O BACK-FILE "$loop")" = "$IMG" ] || { echo "$loop is not backed by $IMG" >&2; exit 92; }
mkdir -p "$W/run/targets/sda" || exit 93
echo "PART|$FS" > "$W/run/targets/sda/expanded"
RUN_DIR="$W/run" LOOP="$loop" "$BB" sh -c '
    . "$AGENT/lib/core/common.sh"; . "$AGENT/lib/restore/grow.sh"
    partition_node() { echo "$LOOP"; }
    assert_target() { :; }; writemark_release() { :; }
    target_set() { printf "%s|%s\n" "$2" "$3" > "$RUN_DIR/target_state"; }
    finish_grow sda "" ""' > "$W/grow.out" 2>&1
echo $? > "$W/rc"
'''.replace("PART", str(PART))


@needs_root
@pytest.mark.parametrize("fs", ["ext4", "btrfs"])
def test_finish_grow_stretches_what_the_chunk_engine_wrote_to_the_whole_partition(cc, tmp_path, fs):
    src = _make_fs(fs, tmp_path)
    img = _capture(cc, fs, src, tmp_path)
    target = _garbage(tmp_path / "big.disk", BIG)
    _through_the_engine(cc, img, target, tmp_path)
    assert _fs_bytes(fs, target) == SIZE                     # as captured: the grow has something to do

    w = tmp_path / "w"
    w.mkdir()
    env = {"W": str(w), "IMG": str(target), "FS": fs, "AGENT": str(AGENT), "BB": BUSYBOX,
           "OWNER": f"{os.getuid()}:{os.getgid()}", "PATH": SBIN_PATH}
    proc = subprocess.run(["sudo", "-n", "env", *[f"{k}={v}" for k, v in env.items()], "sh", "-c", GROW],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)       # the harness itself, not the agent
    out = (w / "grow.out").read_text(errors="replace") + (w / "run" / "agent.log").read_text(errors="replace")
    assert (w / "rc").read_text().strip() == "0", out
    assert (w / "run" / "target_state").read_text() == "done|\n", out  # done, and no "the grow was deferred"
    assert _fs_bytes(fs, target) == BIG, out
    _clean(fs, target)
    _files_back(fs, target, tmp_path)
