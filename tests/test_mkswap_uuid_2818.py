"""#2818 -- the swap comes back with its UUID under the agent's own shell.

`make_swap` runs `mkswap -U <uuid>` (#48: Debian's fstab finds the swap by
`UUID=`). In the initramfs `/bin/sh` is Debian's busybox-static, whose `mkswap`
applet has no `-U` ("invalid option -- 'U'", v0.91.9's initrd, 10/11), so every
Linux image with a swap partition failed to restore. The other tests stub
`mkswap` and so check the arguments, never the tool that runs.

Packing util-linux's mkswap is not enough on its own: busybox sh (FEATURE_SH_
STANDALONE) runs its applet before anything on PATH -- measured on the
testrunner 10/11 with a real `mkswap` first on PATH. So here the real
util-linux mkswap is first on PATH *and* is what `MKSWAP_BIN` names, the shell
is busybox ash, and the evidence is the UUID read back from the swap header.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from agentlib import agent_sources
from native import requires_native, with_sbin
from test_agent import AGENT, REPO, SWAP_UUID, posix
from test_beeps_2067 import BUSYBOX, run_ash

MKSWAP = shutil.which("mkswap", path=with_sbin(os.environ.get("PATH", "")))
PAGE = 4096                            # x86-64: mkswap writes the header in the first page

_ASH = requires_native(
    ("busybox", BUSYBOX), ("mkswap", MKSWAP), posix=True,
    why="the agent's shell (busybox ash) and util-linux's mkswap, the one the initramfs packs",
)


def swap_header(path: Path) -> tuple[bytes, str]:
    """(signature, UUID) as the kernel reads them: `SWAPSPACE2` in the last
    10 bytes of the first page, the UUID at 1036 (union swap_header.info)."""
    head = path.read_bytes()[:PAGE]
    return head[PAGE - 10:], str(uuid.UUID(bytes=head[1036:1052]))


def make_swap(tmp_path: Path, manifest_uuid: str) -> tuple[str, Path]:
    target = tmp_path / "sda3"
    target.write_bytes(b"\0" * (1 << 20))
    log = tmp_path / "agent.log"
    real_dir = posix(Path(MKSWAP).parent)
    out = run_ash(
        f'export PATH={real_dir!r}:"$PATH" MKSWAP_BIN={posix(Path(MKSWAP))!r} '
        f'LOG_FILE={posix(log)!r}; '
        + agent_sources("restore")
        + f'make_swap {posix(target)!r} "{manifest_uuid}"; echo "rc=$?"; '
        f'cat {posix(log)!r}')
    return out, target


@_ASH
def test_the_swap_gets_the_manifest_uuid_under_busybox_sh(tmp_path):
    """The restore that failed in #1979's check: rc 1 and no swap under the
    applet; rc 0 and the manifest's UUID in the header under util-linux."""
    out, target = make_swap(tmp_path, SWAP_UUID)
    assert "rc=0" in out, out
    assert "invalid option" not in out, out
    signature, written = swap_header(target)
    assert signature == b"SWAPSPACE2", out
    assert written == SWAP_UUID, out


@_ASH
def test_an_old_manifest_without_a_uuid_still_gets_a_swap(tmp_path):
    """Before #48 there was no UUID in the manifest: a plain mkswap, any UUID
    (CLAUDE.md principle 1). Passes before and after the fix -- the guard
    against a fix that breaks the plain path."""
    out, target = make_swap(tmp_path, "")
    assert "rc=0" in out, out
    signature, written = swap_header(target)
    assert signature == b"SWAPSPACE2", out
    assert written != SWAP_UUID


def test_the_path_make_swap_calls_is_where_the_builder_packs_mkswap():
    """make_swap's default is /usr/bin/mkswap; copy_bin puts every BINARIES
    entry in $ROOT/usr/bin and stops the build when one is missing."""
    restore = (AGENT / "lib" / "restore" / "restore.sh").read_text(encoding="utf-8")
    assert '"${MKSWAP_BIN:-/usr/bin/mkswap}" $_u "$1"' in restore
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    binaries = re.search(r"BINARIES=\((.*?)\)", builder, flags=re.DOTALL).group(1).split()
    assert "mkswap" in binaries, binaries
    assert 'dst="$ROOT/usr/bin/$(basename "$src")"' in builder
    assert 'src=$(command -v "$1") || { echo "missing binary: $1" >&2; exit 1; }' in builder
