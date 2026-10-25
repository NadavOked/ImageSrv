"""#1903 -- PASSED with no attribute table, or with exit bit 2, is "unchecked", not ok.

``smart_probe`` read the health line and then the counters by ``_smart_int``,
which prints 0 for a row that is not there. A USB-SATA bridge that answers
``-H`` and not ``-A`` (or a ``smartctl`` whose SMART command failed -- exit bit 2,
value 4) gave PASSED and no table: the disk was ``ok`` with 197/198 = 0, and
``smart_was_read`` told crcdelta.sh the counters had been read.

Now: PASSED with bit 2 set is ``unchecked cmd_failed``; PASSED with no ATA
attribute table, no NVMe health section and no SCSI defect list is
``unchecked no_attrs``. Never FAILED (that would block a healthy disk), never
ok. FAILED stays FAILED; a PASSED disk with its table is ok as before.

On ``feat/1921-smart-tables`` (PR #1930) the same lines sit after the knowledge
tables' "based on an Attribute check" line (QA-G43, 21/10): there ``smart_classify``
says ``ok ok`` on PASSED, and ``no_table`` means the knowledge files are missing --
not the disk's own table -- so a disk whose table smart_judge never saw was ``ok``.
The file is the same one as on ``lab/int-1021``, so the merge of the two keeps one.
"""

from __future__ import annotations

from test_smart import HEALTH_FAIL, HEALTH_OK, PRELUDE, SCSI_OK, full, nvme, probe, sh

import pytest

from native import requires_native
from test_agent import BASH

pytestmark = requires_native(("bash", BASH))


def _record(tmp_path, dev="sda") -> list[str]:
    return (tmp_path / "run" / "smart" / f"{dev}.v").read_text().split()


def test_passed_without_an_attribute_table_is_unchecked(tmp_path):
    assert probe(tmp_path, HEALTH_OK + "\n") == "unchecked"
    assert _record(tmp_path)[:2] == ["unchecked", "no_attrs"]


def test_passed_with_exit_bit_2_is_unchecked(tmp_path):
    """rc=4: "some SMART command to the disk failed" -- the table may be partial."""
    assert probe(tmp_path, full(HEALTH_OK), rc=4) == "unchecked"
    assert _record(tmp_path)[:2] == ["unchecked", "cmd_failed"]


def test_an_unread_disk_is_not_read_for_crcdelta(tmp_path):
    probe(tmp_path, HEALTH_OK + "\n")
    out = sh(f'export RUN_DIR={str(tmp_path / "run").replace(chr(92), "/")!r}; ' + PRELUDE
             + 'smart_was_read sda; echo "rc=$?"')
    assert out.stdout.strip().endswith("rc=1"), out.stdout


# --- the guards: what was right stays right -----------------------------------------


def test_passed_with_its_table_is_still_ok(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK)) == "ok"


def test_failed_with_exit_bits_2_and_3_is_still_fail(tmp_path):
    assert probe(tmp_path, HEALTH_FAIL + "\n", rc=12) == "fail"


@pytest.mark.parametrize(("body", "dev"), [(SCSI_OK, "sda"), (nvme("0x00", 0), "nvme0n1")], ids=["scsi", "nvme"])
def test_scsi_and_nvme_have_their_own_tables(tmp_path, body, dev):
    assert probe(tmp_path, body, dev=dev) == "ok"
