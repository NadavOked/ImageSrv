"""#4162 -- a silent-server timeout must not hide a write I/O error that the kernel itself reported on the target.

`ata_cause.classify` read "השרת לא ענה במשך N שניות" as `network` whenever no cable/disk/pulled sign was found. The kernel line a failing write
leaves -- `I/O error, dev sda, sector 4096 op 0x1:(WRITE)` -- matches none of those patterns, so a disk that refused writes AND made the
receiver stall (so the agent gave up on the server) was recorded `network`: `disk_failures.quiet` hides `network`, and the disk was neither orange
for the next wave nor listed for the operator. "The kernel was read and holds no disk line" (K9's own words for `network`) was false.
Found by a static review of #3847 (Codex 26/11).

The fix is narrow: a kernel WRITE I/O error next to the silent-server words makes the cause `disk` (the kernel's evidence wins, as it does
over every phrase the agent writes). It is `disk` and not `unclassified` because `disk_failures.interrupted` also hides an `unclassified` row that
carries those words. A pulled drawer leaves the same line, and that case is decided earlier (`removed`).

**Negative control:** with `server/disks/ata_cause.py` back at the base of this PR the write-error tests fail on behaviour (`network`
instead of `disk`, and the record is hidden). The network-only and pulled-drawer tests pass in both states: the guards against a fix that
stops calling a real network drop `network`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import db
from server.brain import cause as k9
from server.disks import ata_cause, disk_failures

AGENT_SAID = "מחיצה 1: השרת לא ענה במשך 124 שניות"
WRITE_ERROR = "[  211.000001] blk_update_request: I/O error, dev sda, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2"
READ_ERROR = "[  211.000001] blk_update_request: I/O error, dev sda, sector 4096 op 0x0:(READ) flags 0x0 phys_seg 1 prio class 2"
PULLED = [WRITE_ERROR, "[  211.000002] sd 1:0:0:0: rejecting I/O to offline device",
          "[  211.000003] blk_update_request: device offline error, dev sda, sector 4096"]
MAC = "78:ac:c0:9b:11:c2"


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "net_write.db")


def _target(lines) -> dict:
    return {"dev": "sda", "state": "failed", "error": AGENT_SAID, "serial": "naa.5000c29aaa441c55", "port": 3,
            "ata_port": None, "ata_log": list(lines)}


def test_a_kernel_write_error_with_the_silent_server_words_is_not_network():
    """(neg) the kernel said the disk refused a write: the agent's "the server did not answer" does not decide."""
    assert ata_cause.classify([WRITE_ERROR], AGENT_SAID) == "disk"


def test_the_record_is_visible_and_the_disk_is_not_declared_quiet(conn):
    """(neg) `network` is hidden from the hello and the console list (`quiet`); this record must paint the disk."""
    row = disk_failures.record(conn, "ses1", MAC, _target([WRITE_ERROR]))
    assert row is not None and row["cause"] == "disk", row
    assert disk_failures.open_for(conn, MAC, [{"serial": "naa.5000c29aaa441c55"}]) != []
    assert [r["serial"] for r in disk_failures.list_open(conn)] == ["naa.5000c29aaa441c55"]


def test_k9_does_not_call_it_network_when_the_kernel_has_a_write_error():
    """(neg) K9 says "the kernel was read and holds no line of a cable or a drive" only when that is true."""
    v = k9.classify(k9.Evidence(kernel=(WRITE_ERROR,), error=AGENT_SAID))
    assert v.cause == k9.DRIVE and v.against_disk, v


def test_a_kernel_read_error_or_no_kernel_lines_stay_network():
    """Only a WRITE refusal is evidence about the write: a read error elsewhere, or silence, leaves the agent's words in charge."""
    assert ata_cause.classify([READ_ERROR], AGENT_SAID) == "network"
    assert ata_cause.classify([], AGENT_SAID) == "network"


def test_a_pulled_drawer_is_still_removed_not_disk():
    """The same WRITE line next to an offline device is the pulled-drawer evidence (#3008) and was decided before the network words."""
    assert ata_cause.classify(PULLED, AGENT_SAID) == "removed"
    assert ata_cause.classify(PULLED, "") == "removed"
    assert ata_cause.classify([WRITE_ERROR], "") == "unclassified"      # a lone write error is not newly called anything
