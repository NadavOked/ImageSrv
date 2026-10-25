"""#1202 on real block devices: the 20/09 case in miniature, through the local
clone engine (#1322), which measures, shrinks and copies with the capture's own
code (shrinkplan.sh / shrink.sh) and restores with apply_gpt / expand_last.

The source (``localclone-fixture.sh setup-tail``): ESP, MSR, C: (NTFS 256M,
nearly full), Recovery (NTFS) to the very last usable sector. The target is
2M smaller -- "the same 256GB" from another maker. The regular shrink (margin
2GiB) moves nothing on such a C:, so before #1202 the target is "short".

* nearly full: the source gives a 64MiB tail -- C: shrinks, the Recovery moves
  back on the TARGET only -- and every file arrives; the source comes back to
  its exact table, and its Recovery is never written (raw sha256);
* packed (no room for the tail): still refused by name, nothing written.

Root and loop devices: the Testrunner only (``native.py``), like
``test_local_clone_engine.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from native import requires_native
from test_local_clone_engine import (  # noqa: F401 -- fanout_bin is a fixture
    ENGINE_LOCK,
    LIB,
    LIBS,
    SBIN,
    _no_table,
    _root,
    _Server,
    _size,
    _table,
    _tool,
    _sudo_ok,
    fanout_bin,
)

FIXTURE = Path(__file__).resolve().parent / "localclone-fixture.sh"
FILE_PARTS = "1:vfat 3:ntfs-3g 4:ntfs-3g"
TAIL_SECTORS = (64 << 20) // 512

pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "partclone.ntfs", "partclone.fat", "partclone.dd",
                              "ntfsresize", "mkntfs", "mkfs.vfat", "ntfs-3g", "pv", "gcc", "jq", "curl", "flock")],
    posix=True,
    why="loop devices and partclone as root: the Testrunner",
)

DRIVER = r'''
set -u
F=$1; SHRINK=$2
export RUN_DIR=$F/run LIB_DIR=%(lib)s IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff
rm -rf "$RUN_DIR"; mkdir -p "$RUN_DIR"
export DEVROOT=$F/dev; mkdir -p "$DEVROOT"
for d in $(awk '{ print $2 }' "$F/devs"); do n=${d#/dev/}; ln -sf "$d" "$DEVROOT/$n"
  for p in 1 2 3 4; do ln -sf "${d}p$p" "$DEVROOT/${n}p$p"; done; done
# #1466: the RAM view's own nodes (device-mapper and brd) -- RAM, not a disk of the host
# (as in test_local_clone_engine's driver; without them lcsnap.sh refuses the view)
ln -sfn /dev/mapper "$DEVROOT/mapper"; for i in 0 1 2 3; do ln -sf "/dev/ram$i" "$DEVROOT/ram$i"; done
%(sources)s
dev() { awk -v n="$1" '$1 == n { sub("/dev/", "", $2); print $2 }' "$F/devs"; }
SRC=$(dev src); NEAR=$(dev near)
disk_serial() { echo "SER-$1"; }
list_drawers() { echo "$SRC"; echo "$NEAR"; }
mkdir -p "$LC"
lc_inventory
lc_measure "$SRC"
echo "verdict $(lc_verdict_for "$SRC" "$NEAR")"
lc_run "$SRC" " $NEAR" "$SHRINK"
t=$RUN_DIR/targets/$NEAR
echo "target $(cat "$t/state" 2>/dev/null)|$(cat "$t/error" 2>/dev/null | tr '\n' ' ')"
echo "restore $(cat "$LC/run/source_restore" 2>/dev/null)"
echo "path $(cat "$LC/run/shrink_path" 2>/dev/null)"
'''


@pytest.fixture(params=[100, 170], ids=["nearly_full", "packed"])
def tail_disks(request, tmp_path):
    fx = tmp_path / "fx"
    up = _root("sh", str(FIXTURE), "setup-tail", str(fx), str(request.param), timeout=300)
    try:
        assert up.returncode == 0, up.stdout + up.stderr
        devs = dict(line.split() for line in (fx / "devs").read_text().splitlines())
        yield request.param, fx, devs
    finally:
        _root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)


def _files(dev: str, fx: Path) -> list[str]:
    out = _root("sh", str(FIXTURE), "files", dev, str(fx), FILE_PARTS)
    lines = sorted(out.stdout.splitlines())
    assert lines and not any("mount-failed" in ln for ln in lines), out.stdout + out.stderr
    return lines


def _raw_sha(node: str) -> str:
    return _root("sha256sum", node).stdout.split()[0]


def _run(tmp_path: Path, fx: Path, fanout: Path, server: str) -> dict:
    script = tmp_path / "drive.sh"
    script.write_text(DRIVER % {"lib": LIB, "sources": "\n".join(f'. "$LIB_DIR/{n}.sh"' for n in LIBS)},
                      newline="\n")
    # #2099: SHRINK=1 loads `brd` (the RAM view), a module of the whole machine -- the same
    # turn-taking lock as test_local_clone_engine (#1594), or a parallel worker's engine run
    # finds brd "already loaded, not by the clone" and refuses.
    run = _root(f"PATH={fanout}:{os.environ.get('PATH', '')}:{SBIN}", f"SERVER={server}", "STOP_AT=",
                "LC_STALL_S=120", "flock", ENGINE_LOCK, "sh", str(script), str(fx), "1", timeout=1500)
    out = {"raw": run.stdout + run.stderr}
    for line in run.stdout.splitlines():
        key, _, val = line.partition(" ")
        if key in ("verdict", "target", "restore", "path"):
            out[key] = val
    assert run.returncode == 0, out["raw"][-4000:]
    return out


def test_the_tail_moves_back_and_the_smaller_drive_takes_the_copy(tmp_path, tail_disks, fanout_bin):
    fill, fx, devs = tail_disks
    if fill != 170 and _tool("dmsetup") is None:
        pytest.skip("dmsetup is not installed: the RAM view (#1466) cannot be built here")
    src, near = devs["src"], devs["near"]
    src_table, src_files, rec_raw = _table(src), _files(src, fx), _raw_sha(src + "p4")
    srv = _Server()
    try:
        res = _run(tmp_path, fx, fanout_bin, srv.url)
    finally:
        srv.close()
    state, _, error = res.get("target", "").partition("|")
    if fill == 170:
        # no room for the tail: refused by name, before a byte, and the source only read
        assert res["verdict"].split("|")[0] == "short", res["raw"][-3000:]
        assert state == "failed" and error.strip(), res["raw"][-3000:]
        assert _no_table(near)
        assert "shrink-open" not in [k for k, _b in srv.posts]
        assert _table(src) == src_table and _raw_sha(src + "p4") == rec_raw
        return
    assert res["verdict"].split("|")[0] == "shrink", res["raw"][-3000:]
    assert state == "done", res["raw"][-3000:]
    got = _table(near)
    assert got["guid"] == src_table["guid"]
    for i in (1, 2):
        assert got["parts"][i] == src_table["parts"][i], i
    c, c0 = got["parts"][3], src_table["parts"][3]
    assert c["First sector"] == c0["First sector"]
    assert _size(c) == _size(c0) - TAIL_SECTORS, (c, c0)
    r, r0 = got["parts"][4], src_table["parts"][4]
    assert int(r["First sector"].split()[0]) == int(r0["First sector"].split()[0]) - TAIL_SECTORS, (r, r0)
    same = ("Partition GUID code", "Partition unique GUID", "Attribute flags", "Partition name", "Partition size")
    assert {k: r[k] for k in same} == {k: r0[k] for k in same}
    assert _files(near, fx) == src_files
    # #1466: the tail was taken on a RAM view of C:, released afterwards -- the
    # source was never written, so there is nothing to grow back ("" = released)
    assert res.get("path") == "snapshot" and res["restore"] == "", res["raw"][-3000:]
    assert _table(src) == src_table and _files(src, fx) == src_files
    assert _raw_sha(src + "p4") == rec_raw
