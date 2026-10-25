"""#1904 -- a dd that failed is "unknown", never "none".

``disk_scheme`` read LBA1 and the MBR signature with ``dd ... 2>/dev/null``
and folded a failed read (I/O error, a dying disk) into ``none``: a positive
claim -- "no table here" -- that the hello turns into ``has_data:false`` and
``write_mark:"clean"``, i.e. an empty disk that may be written over (and a
capture refusal that says "found: none"). The file's own comment already said
that a read error must never become ``none`` (#126 fixed only the sector size).

Here: a ``dd`` shim in PATH that fails the way a dying disk does -- exit 1,
nothing read -- on every read, or only on the MBR read (bs=2 skip=255) after
LBA1 read zeros. The real reads on a real image file are test_disk_scheme.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_disk_scheme import blank_image, disk_box, gpt_image, mbr_image

pytestmark = requires_native(("bash", BASH))

#: dd that fails: everywhere ($DD_FAIL=all), or only on the MBR read ($DD_FAIL=mbr),
#: and otherwise runs the real dd.
DD_SHIM = r'''#!/bin/sh
case "$DD_FAIL:$*" in
    all:*) echo "dd: error reading: Input/output error" >&2; exit 1 ;;
    mbr:*skip=255*) echo "dd: error reading: Input/output error" >&2; exit 1 ;;
esac
exec "$REAL_DD" "$@"
'''


def _shim(box: Path) -> str:
    stubs = box / "stubs"
    stubs.mkdir(exist_ok=True)
    (stubs / "dd").write_text(DD_SHIM, encoding="utf-8", newline="\n")
    return (f'REAL_DD=$(command -v dd); export REAL_DD; chmod 0755 {posix(stubs)}/dd; '
            f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; ')


def _scheme(box: Path, fail: str) -> str:
    return sh(_shim(box) + f'export DD_FAIL={fail} SYSROOT={posix(box)!r} DEVROOT={posix(box / "dev")!r}; '
              f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
              'disk_scheme "$DEVROOT/sda"').strip().splitlines()[-1]


@pytest.mark.parametrize("image", [gpt_image(512), mbr_image(512), blank_image(512)],
                         ids=["gpt", "mbr", "blank"])
def test_a_dd_that_cannot_read_the_disk_is_unknown(tmp_path, image):
    assert _scheme(disk_box(tmp_path, image), "all") == "unknown"


def test_a_dd_that_cannot_read_the_mbr_is_unknown_not_none(tmp_path):
    """LBA1 read (no GPT header), then the MBR read fails: still not "none"."""
    assert _scheme(disk_box(tmp_path, blank_image(512)), "mbr") == "unknown"


@pytest.mark.parametrize(("image", "expected"),
                         [(gpt_image(512), "gpt"), (mbr_image(512), "mbr"), (blank_image(512), "none")],
                         ids=["gpt", "mbr", "none"])
def test_a_dd_that_reads_still_answers_what_it_read(tmp_path, image, expected):
    """The guard against an over-broad fix: through the same shim, a read that
    works gives the three answers it always gave -- a truly blank disk is none."""
    assert _scheme(disk_box(tmp_path, image), "none") == expected


def _unreadable_entry(tmp_path: Path, role: str) -> dict:
    box = disk_box(tmp_path, blank_image(512))
    (box / "sys" / "block" / "sda" / "size").write_text("1000\n", newline="\n")
    stubs = box / "stubs"
    stubs.mkdir()
    (stubs / "sgdisk").write_text("#!/bin/sh\necho 'Number  Start'\n", encoding="utf-8", newline="\n")
    out = sh(_shim(box) + f'chmod 0755 {posix(stubs)}/sgdisk; export DD_FAIL=all SYSROOT={posix(box)!r} '
             f'DEVROOT={posix(box / "dev")!r} D_ROLE={role}; . {posix(AGENT)}/lib/common.sh; '
             f'. {posix(AGENT)}/lib/sysinfo.sh; '
             f'. {posix(AGENT)}/lib/writemark.sh; LOG_FILE={posix(box)}/log; build_disk_entry sda')
    return json.loads(out.strip().splitlines()[-1])


def test_the_hello_of_an_unreadable_disk_has_data_and_an_unchecked_mark(tmp_path):
    """What the build capture screen sees: not an empty disk, not "clean".
    ‏#1924 (נדב): ‏scheme/has_data נשלחים רק ממחשב בנייה — שם נבדק #1904."""
    entry = _unreadable_entry(tmp_path, "build")
    assert entry["scheme"] == "unknown", entry
    assert entry["has_data"] is True, entry
    assert entry["write_mark"] == "unchecked", entry


def test_the_hello_of_an_unreadable_drawer_is_unchecked_and_says_nothing_of_its_content(tmp_path):
    """A cloner's drawer (#1924): no scheme, no has_data -- and a dd that failed
    still never turns into a "clean" mark (#1904)."""
    entry = _unreadable_entry(tmp_path, "cloner")
    assert "scheme" not in entry and "has_data" not in entry, entry
    assert entry["write_mark"] == "unchecked", entry
