"""#3415 (part 2) -- xfs through the chunk engine, and the grow after it. The same road as `test_chunk_linux_fs_3166` (ext4, btrfs).

Part 1 (`test_xfs_3415.py`) proved the agent's own tools: `partclone.xfs -c | zstd`, and `zstd -dc | partclone.xfs -r`. The cloner's
engine is another road, and "the code is generic" is not evidence (principle 5). This file runs the road an xfs partition takes
on a cloner, with the real tools and on files:

    a real xfs with files in it
      -> partclone.xfs -c -a0 -z 524288      (the capture's flags, capture_pcl_flags)
      -> chunkcut -l 3 -T 0                   (format 2: the head, a zstd frame a 4MiB chunk, the index)
      -> chunkcast send / chunkcast recv      (loopback; the target is a file filled with garbage)

and judges the target by what is not the engine's own word:

* byte for byte, it is what the OLD pipe writes to the same garbage (`zstd -dc | partclone.xfs -r`);
* the filesystem's own checker passes it (`xfs_repair -n`);
* it has the identity of the one that was captured -- the xfs UUID (a restored copy that kept another would not be the image's);
* the size of every file is what it was (`xfs_db`, no mount of ours).

The second half is `finish_grow` (agent/lib/restore/grow.sh) on a target twice the size of the source -- the same function
drawers.sh calls after either engine: `xfs_growfs`, mounted `nouuid`, so it is root (a loop device on a file of ours, the
Testrunner's `sudo -n`). The filesystem the engine wrote grows past what was captured, stays clean, keeps its UUID and every file
(compared with `cmp` against the source tree), and the drawer is `done` with no warning.

Not proved here: a real disk and a real distribution booting from it (the VM step of #3415, Argus), and an image captured on a build machine.

**Negative control:** (a) `agent/lib/restore/grow.sh` with its xfs branch off -- the grow test fails: the filesystem is not grown
and the drawer is not `done`; (b) `agent/chunkcast.c` with one chunk's write dropped -- the engine test fails on the byte comparison.
"""

from __future__ import annotations

import filecmp
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace


from test_agent import AGENT
from test_chunk_linux_fs_3166 import _through_the_engine, cc   # noqa: F401 -- cc is a fixture
from test_json_escape import BUSYBOX
from test_xfs_3415 import (   # the real xfs of part 1: its source, its checker, its identity, and its grow harness
    BIG, FILES, GROW, REAL, SBIN_PATH, SIZE, _clean, _fs_bytes, _garbage, _make_xfs, _run, _sb, _tool, needs_root,
)

PART = 2


def _capture(cc: Path, src: Path, tmp: Path) -> SimpleNamespace:
    """partclone.xfs -c | chunkcut, as capture.sh runs them. The result has what chunkcast_rig's recv/send read off an image."""
    index, packed = tmp / "idx", tmp / f"p{PART}.linux.pcl.zst"
    read = subprocess.Popen([_tool("partclone.xfs"), "-c", "-a0", "-z", "524288", "-s", str(src), "-L", str(tmp / "c.log")],
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


def _through_the_old_pipe(img: SimpleNamespace, target: Path, tmp: Path) -> None:
    plain = subprocess.Popen(["zstd", "-dc", str(img.file)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    write = subprocess.run([_tool("partclone.xfs"), "-r", "-s", "-", "-O", str(target), "-L", str(tmp / "r.log")],
                           stdin=plain.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    plain.stdout.close()
    assert plain.wait(timeout=300) == 0 and write.returncode == 0, write.stderr


def _file_sizes(image: Path) -> None:
    """Every file's size, read out of the image by xfs_db -- no mount of ours."""
    for name, data in FILES.items():
        p = _run(_tool("xfs_db"), "-r", "-c", f"path /{name}", "-c", "print core.size", image)
        assert p.returncode == 0 and f"core.size = {len(data)}" in p.stdout.decode(), (name, p.stdout, p.stderr)


@REAL
def test_a_real_xfs_through_the_chunk_engine_is_the_old_pipes_bytes_clean_and_with_its_identity(cc, tmp_path):
    src = _make_xfs(tmp_path)
    img = _capture(cc, src, tmp_path)
    engine, old = _garbage(tmp_path / "engine.disk", SIZE), _garbage(tmp_path / "old.disk", SIZE)
    _through_the_engine(cc, img, engine, tmp_path)
    _through_the_old_pipe(img, old, tmp_path)
    assert filecmp.cmp(engine, old, shallow=False), "the chunk engine wrote other bytes than partclone.xfs -r"
    _clean(engine)
    assert _sb(engine)["uuid"] == _sb(src)["uuid"], "the restored xfs is not the captured one"
    _file_sizes(engine)


@REAL
@needs_root
def test_finish_grow_stretches_what_the_chunk_engine_wrote_to_the_whole_partition(cc, tmp_path):
    src = _make_xfs(tmp_path)
    img = _capture(cc, src, tmp_path)
    target = _garbage(tmp_path / "big.disk", BIG)
    _through_the_engine(cc, img, target, tmp_path)
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
    assert (w / "run" / "target_state").read_text() == "done|\n", out    # done, and no "the grow was deferred"
    assert not (w / "rc.files").exists(), (w / "rc.files").read_text()  # every file equal to the source tree, read through a mount
    assert _fs_bytes(target) > SIZE, out                                  # grown past what was captured
    _clean(target)
    assert _sb(target)["uuid"] == _sb(src)["uuid"], "growing changed the filesystem's identity"
    _file_sizes(target)
