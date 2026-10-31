"""#1905 -- a blkid that failed is "unreadable", never "unknown".

``_fs_of`` was ``blkid -o value -s TYPE "$1" 2>/dev/null || echo unknown``:
rc 2 ("no TYPE" -- an answer) and rc 4/8 (the check itself failed) were the
same word. An NTFS partition that did not read was captured as ``partclone.dd``
and the hibernation/Fast Startup gate, which runs on ``ntfs`` only, was skipped.

Now three answers: the filesystem; ``unknown`` = rc 2 AND the head of the
partition reads back; ``unreadable`` = anything else. The capture refuses an
``unreadable`` partition by name, and the local clone does not offer its disk
as a source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import CURL_SINK, ONE_PARTITION, capture_run, refusal_reason

pytestmark = requires_native(("bash", BASH))


def _fs(tmp_path: Path, blkid: str, node_exists: bool = True) -> str:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "blkid").write_text("#!/bin/sh\n" + blkid + "\n", encoding="utf-8", newline="\n")
    node = tmp_path / "sda1"
    if node_exists:
        node.write_bytes(bytes(4096))
    out = sh(f'chmod 0755 {posix(stubs)}/blkid; export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
             f'. {posix(AGENT)}/lib/capture/manifest.sh; _fs_of {posix(node)!r}')
    return out.strip().splitlines()[-1]


def test_a_filesystem_blkid_names_is_that_filesystem(tmp_path):
    assert _fs(tmp_path, "echo ntfs; exit 0") == "ntfs"


def test_no_type_on_a_partition_that_reads_is_unknown(tmp_path):
    """rc 2 is an answer (an MSR, a raw partition) -- the guard against
    refusing every partition blkid has no name for."""
    assert _fs(tmp_path, "exit 2") == "unknown"


@pytest.mark.parametrize("rc", [4, 8, 1])
def test_a_blkid_that_failed_is_unreadable(tmp_path, rc):
    assert _fs(tmp_path, f"exit {rc}") == "unreadable"


def test_no_type_on_a_partition_that_does_not_read_is_unreadable(tmp_path):
    """blkid may say rc 2 on an I/O error too: the head is read separately."""
    assert _fs(tmp_path, "exit 2", node_exists=False) == "unreadable"


def test_a_capture_refuses_a_partition_whose_filesystem_did_not_read(tmp_path):
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK,
                                                  "blkid": "#!/bin/sh\nexit 8\n"})
    reason = refusal_reason(box, run, out)
    assert "מחיצה 1: לא הצלחנו לקרוא את מערכת הקבצים שלה (blkid)" in reason, reason
    assert "לא נקלט" in reason, reason


LCINV = r'''
disk_port() { echo 1; }; smart_probe() { echo ok; }; smart_field() { :; }; disk_failure_cause() { return 1; }
logical_block_size() { echo 512; }; disk_scheme() { echo gpt; }; writemark_state() { echo clean; }
lc_parts() { printf '1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|11111111-1111-1111-1111-111111111111|2048|204800\n' > "$2"; : > "$2.fail"; }
partition_node() { echo "$DEVROOT/$1$2"; }; _partition_role() { echo esp; }
partclone_for_fs() { echo partclone.fat; }; _used_bytes() { echo 1000; }; unique_guid_gate() { :; }
lc_min_free() { echo ok; }
'''


@pytest.mark.parametrize(("blkid", "usable", "why"),
                         [("echo vfat; exit 0", "1", ""),
                          ("exit 4", "0", "לא הצלחנו לקרוא את מערכת הקבצים במחיצה 1")],
                         ids=["readable", "unreadable"])
def test_the_local_clone_never_offers_a_source_whose_filesystem_did_not_read(tmp_path, blkid, usable, why):
    box = tmp_path / "box"
    (box / "stubs").mkdir(parents=True)
    (box / "dev").mkdir()
    (box / "dev" / "sda1").write_bytes(bytes(4096))
    (box / "stubs" / "blkid").write_text("#!/bin/sh\n" + blkid + "\n", encoding="utf-8", newline="\n")
    lc = tmp_path / "lc"
    out = sh(f'chmod 0755 {posix(box)}/stubs/blkid; export PATH="$(cd {posix(box)!r}/stubs && pwd):$PATH" '
             f'RUN_DIR={posix(box)!r} DEVROOT={posix(box)}/dev SYSROOT={posix(box)} LOG_FILE={posix(box)}/log '
             f'LC={posix(lc)}; mkdir -p "$LC/inv"; log() {{ :; }}; . {posix(AGENT)}/lib/core/common.sh; '
             f'. {posix(AGENT)}/lib/capture/manifest.sh; . {posix(AGENT)}/lib/localclone/lcinv.sh; ' + LCINV
             + 'lc_inv_disk sda')
    fields = out.strip().splitlines()[-1].split("|")
    assert (fields[11], fields[12]) == (usable, why), fields
