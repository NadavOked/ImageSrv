"""#2430 -- a read-only probe mount must not replay a journal.

`-o ro` alone does not keep ext4, btrfs or xfs off the disk: a dirty journal
(log tree, xfs log) is replayed on mount, and replaying writes. Two probes
mounted that way:

* `_lc_os_name` (agent/lib/localclone/lcinv.sh) -- on EVERY drawer, while the
  local-clone inventory is drawn, before anything was chosen. It already had
  `noload` for ext4; btrfs and xfs got a bare `ro`.
* `_linux_system` (agent/lib/capture/manifest.sh) -- on the capture source,
  ext4 and btrfs, bare `ro`.

`_used_bytes` (manifest.sh) and `_du_mount_used` (diskused.sh) were fixed for
ext4/btrfs earlier; xfs reached them with a bare `ro` too and is fixed here
with the same rule: ext* -> `ro,noload`, btrfs -> `ro,nologreplay`, xfs ->
`ro,norecovery`.

The real functions run; only the leaf `mount` is stood in -- it records the
options it was handed and fails, so nothing after it matters.

Negative control: lcinv.sh, manifest.sh and diskused.sh back to main -- the
btrfs/xfs cases of the inventory, the ext4/btrfs cases of the capture source
and the xfs cases of the two measurements fail on the recorded options.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_agent import BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

WANT = {"ext4": "ro,noload", "ext3": "ro,noload", "btrfs": "ro,nologreplay", "xfs": "ro,norecovery"}


def mounted_with(tmp_path: Path, call: str, libs: tuple[str, ...]) -> list[str]:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    rec = run / "mount.args"
    src = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in libs)
    sh(f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/log GUI_DIR={posix(run)!r}/gui IMAGESRV_TEST=1; "
       f"{src} "
       f'mount() {{ echo "$*" >> {posix(rec)!r}; return 32; }}; '
       f"{call} >/dev/null 2>&1; :")
    return rec.read_text(encoding="utf-8").splitlines() if rec.exists() else []


def options(lines: list[str]) -> set[str]:
    out = set()
    for line in lines:
        words = line.split()
        assert "-o" in words, line
        out.add(words[words.index("-o") + 1])
    return out


@pytest.mark.parametrize("fs", ["ext4", "btrfs", "xfs"])
def test_the_inventory_probe_of_every_drawer_never_replays(tmp_path, fs):
    got = mounted_with(tmp_path, f"_lc_os_name /dev/sdb2 {fs}", ("common", "sysinfo", "manifest", "lcinv"))
    assert got, "the probe did not mount at all -- the test checks nothing"
    assert options(got) == {WANT[fs]}, got


@pytest.mark.parametrize("fs", ["ext4", "btrfs"])
def test_the_capture_source_root_probe_never_replays(tmp_path, fs):
    got = mounted_with(tmp_path, f"_linux_system 3 /dev/sda3 {fs}", ("common", "manifest"))
    assert got, "the probe did not mount at all -- the test checks nothing"
    assert options(got) == {WANT[fs]}, got


@pytest.mark.parametrize("fs", ["ext4", "btrfs", "xfs"])
def test_the_capture_used_bytes_probe_never_replays(tmp_path, fs):
    got = mounted_with(tmp_path, f"_used_bytes /dev/sda3 {fs}", ("common", "manifest"))
    assert got, "the probe did not mount at all -- the test checks nothing"
    assert options(got) == {WANT[fs]}, got


@pytest.mark.parametrize("fs", ["ext4", "btrfs", "xfs"])
def test_the_drawer_card_used_bytes_probe_never_replays(tmp_path, fs):
    got = mounted_with(tmp_path, f"_du_mount_used /dev/sdb3 {fs}", ("common", "sysinfo", "manifest", "diskused"))
    assert got, "the probe did not mount at all -- the test checks nothing"
    assert options(got) == {WANT[fs]}, got


def test_a_filesystem_without_a_replay_switch_keeps_plain_ro(tmp_path):
    """vfat has no journal: a bare `ro`, not an option the kernel would refuse."""
    got = mounted_with(tmp_path, "_lc_os_name /dev/sdb1 vfat", ("common", "sysinfo", "manifest", "lcinv"))
    assert options(got) == {"ro"}, got
