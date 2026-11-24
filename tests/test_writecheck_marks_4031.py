"""#4031 -- the CLR-02 markers of the write check do not stay on the disk.

The check (writecheck.sh, `_wc_alias`, #1805) writes a 4KiB random marker at 0, at every
power-of-two MiB and in the last 4KiB. The comment in the code assumed `apply_gpt` would wipe
them; it wipes only the partition table, and partclone writes only the used blocks -- so a marker
on a free cluster of the restored partition stayed (measured 22/11 in an ESP: blocks 7, 63, 127).

The disk here is an image file; a `dd` shim strips ``iflag/oflag=direct`` (a tmpfs refuses it).
What is proved from outside the code under test is the image's own bytes at the marker places,
read by the test itself -- not the agent's read-back.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from native import requires_native
from test_smart import BASH, posix  # noqa: F401 -- the same bash, declared skip without it
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

MIB = 1048576
BLK = 4096
DD_SHIM = r'''#!/bin/bash
args=()
for a in "$@"; do
    case "$a" in iflag=direct|oflag=direct) continue ;; conv=fsync) a=conv=fsync,notrunc ;; esac
    args+=("$a")
done
exec "$REAL_DD" "${args[@]}"
'''

DRIVER = r'''
set -u
T=$1; shift
REAL_DD=$(command -v dd); REAL_CMP=$(command -v cmp)
TP=$(cygpath -u "$T" 2>/dev/null || echo "$T")   # PATH takes the MSYS form on Windows: a C:/ entry splits at the colon
export REAL_DD REAL_CMP PATH=$TP/bin:$PATH
. %(lib)s/writecheck.sh
set +e
eval "$@"
'''


def _disk(tmp_path: Path, size_mib: int, fill: bytes) -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "w").mkdir()
    shim = tmp_path / "bin" / "dd"
    shim.write_text(DD_SHIM, newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    img = tmp_path / "disk.img"
    img.write_bytes(fill * (size_mib * MIB // len(fill)))
    return img


def _run(tmp_path: Path, script: str) -> subprocess.CompletedProcess:
    driver = DRIVER % {"lib": posix(flat_lib())}
    return subprocess.run([BASH, "-c", driver, "x", posix(tmp_path), script], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=240)


def _marker_places(size_mib: int) -> list[int]:
    """Byte offsets of the markers, computed here from the rule in the comment of `_wc_alias`:
    0, every power-of-two MiB below the end, the last 4KiB."""
    places, m = [0], 1
    while m < size_mib:
        places.append(m * MIB)
        m *= 2
    return places + [size_mib * MIB - BLK]


def _blocks(img: Path, places: list[int]) -> list[bytes]:
    data = img.read_bytes()
    return [data[p:p + BLK] for p in places]


def _alias(tmp_path: Path, size_mib: int, flag: str) -> subprocess.CompletedProcess:
    return _run(tmp_path, f'{flag}_wc_alias "$T/disk.img" "$T/w" {size_mib}; echo "RC=$?"'.replace("$T", posix(tmp_path)))


def test_in_the_round_no_marker_is_left_on_the_disk(tmp_path):
    img = _disk(tmp_path, 64, b"\0")
    out = _alias(tmp_path, 64, "WC_ZERO_MARKS=1 ")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    left = [p // MIB for p, b in zip(_marker_places(64), _blocks(img, _marker_places(64))) if b != bytes(BLK)]
    assert left == [], f"marker(s) left on the disk at MiB {left}"


def test_the_markers_were_written_first_and_the_fixture_sees_them(tmp_path):
    """Without the round's switch (the waiting screen, which puts its own copy back) they stay random:
    so the test above proves a zeroing, not a check that never wrote."""
    img = _disk(tmp_path, 64, b"\0")
    out = _alias(tmp_path, 64, "")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    assert all(b != bytes(BLK) for b in _blocks(img, _marker_places(64)))


def test_the_zeroing_touches_nothing_but_the_marker_places(tmp_path):
    """The disk held data (a stream that is written later is not touched here either)."""
    img = _disk(tmp_path, 64, b"\xa5")
    before = img.read_bytes()
    out = _alias(tmp_path, 64, "WC_ZERO_MARKS=1 ")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    after = img.read_bytes()
    assert len(after) == len(before)
    inside = {q for p in _marker_places(64) for q in range(p, p + BLK)}
    changed = [i for i in range(0, len(before), BLK) if before[i:i + BLK] != after[i:i + BLK]]
    assert changed and all(i in inside for i in changed), "a block outside the marker places changed"
    assert after[:BLK] == bytes(BLK)


CMP_SHIM = r'''#!/bin/bash
# "different" when the marker it compares is all zeros -- the disk that took the zeros and gave something else back
[ "$(tr -d '\0' < "$2" | wc -c | tr -d ' ')" != 0 ] || exit 1
exec "$REAL_CMP" "$@"
'''


def test_a_zeroing_that_is_not_read_back_as_zeros_is_a_visible_failure(tmp_path):
    """Principle 5: a disk that takes the zeros and gives something else back fails by name, it is not 'ok'.
    The random pass compares with the real cmp, so the failure is the zeroing's own."""
    _disk(tmp_path, 64, b"\0")
    shim = tmp_path / "bin" / "cmp"
    shim.write_text(CMP_SHIM, newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    t = posix(tmp_path)
    out = _run(tmp_path, f'WC_ZERO_MARKS=1 _wc_alias "{t}/disk.img" "{t}/w" 64; echo "RC=$?"')
    assert "RC=1" in out.stdout and "failed|" in out.stdout and "(איפוס)" in out.stdout, (out.stdout, out.stderr)
