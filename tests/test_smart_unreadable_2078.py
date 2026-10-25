"""#2078 -- a disk whose SMART status smartctl could not read is red, never written.

Metal, 24/10 06:00, cloner 2 (HP Compaq 8100), TA lab-r5 (43721a26): a Samsung SSD
870 EVO 500GB on SATA1. The BIOS: "1720-SMART Hard Drive detects imminent failure,
Failing Attribute #213", and it stops on F1. smartctl, from the agent: "Read SMART
Data failed: scsi error aborted command", "SMART Status command failed",
"overall-health ... UNKNOWN!", "Attributes and Thresholds cannot be read". The agent
showed the disk ORANGE (``warn reported_uncorrectable``): ``smart_classify`` called the
unread health ``unchecked no_health``, and the knowledge table's orange (the devstat
"Number of Reported Uncorrectable Errors" -- the one counter the disk still gave)
then overrode that unchecked with ``warn``. Since #2042 orange is written without
asking: the disk the BIOS refuses to boot past would have been written in a round.

Nadav (22/10): F1 = red -- allowed as a source, never a target. Principle 5: "we
could not read SMART" is not "a warning". Now: smartctl's own "UNKNOWN!" verdict
(the status command failed AND the attributes/thresholds were not read) is
``fail health_unread`` -- red, skipped by the gate, refused by the local clone and the
restore (smart_red). Narrow on purpose: a disk with no SMART, a bridge that does not
open, a status computed from attributes (#1921 s2) stay ``unchecked`` as before.

The output below is not a raw capture (none was kept): it is smartctl 7.4's ``-x``
layout around the four lines read off the machine, with an assumed devstat count.
"""

from __future__ import annotations

import pytest

from test_smart import BASH, preflight
from test_smart_tables_1921 import probe

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש לסוכן ה-POSIX")

EVO_870_UNREAD = """\
smartctl 7.4 2023-08-01 r5530 [x86_64-linux-6.12.113+deb13-amd64] (local build)
Copyright (C) 2002-23, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Model Family:     Samsung based SSDs
Device Model:     Samsung SSD 870 EVO 500GB
Serial Number:    FIXTURE2078
LU WWN Device Id: 5 002538 000000000
Firmware Version: SVT02B6Q
User Capacity:    500,107,862,016 bytes [500 GB]
Sector Size:      512 bytes logical/physical
Rotation Rate:    Solid State Device
Form Factor:      2.5 inches
TRIM Command:     Available, deterministic, zeroed
Device is:        In smartctl database 7.3/5528
ATA Version is:   ACS-4 T13/BSR INCITS 529 revision 5
SATA Version is:  SATA 3.3, 6.0 Gb/s (current: 1.5 Gb/s)
Local Time is:    Sat Oct 24 06:02:11 2026 UTC
SMART support is: Available - device has SMART capability.
SMART support is: Enabled
AAM feature is:   Unavailable
APM feature is:   Unavailable
Rd look-ahead is: Enabled
Write cache is:   Enabled
DSN feature is:   Unavailable
ATA Security is:  Disabled, frozen [SEC2]
Wt Cache Reorder: Enabled
Power mode is:    ACTIVE or IDLE

Read SMART Data failed: scsi error aborted command

=== START OF READ SMART DATA SECTION ===
SMART Status command failed: scsi error aborted command
SMART overall-health self-assessment test result: UNKNOWN!
SMART Status, Attributes and Thresholds cannot be read.

Read SMART Log Directory failed: scsi error aborted command

General Purpose Log Directory Version 1
Address    Access  R/W   Size  Description
0x00       GPL     R/O      1  Log Directory
0x04       GPL     R/O      8  Device Statistics log

Device Statistics (GP Log 0x04)
Page  Offset Size        Value Flags Description
0x01  =====  =               =  ===  == General Statistics (rev 1) ==
0x01  0x008  4             412  ---  Lifetime Power-On Resets
0x01  0x010  4            9871  ---  Power-on Hours
0x04  =====  =               =  ===  == General Errors Statistics (rev 1) ==
0x04  0x008  4              37  ---  Number of Reported Uncorrectable Errors
0x04  0x010  4               0  ---  Resets Between Cmd Acceptance and Completion
                                |||_ C monitored condition met
                                ||__ D supports DSN
                                |___ N normalized value
"""


def test_the_870_evo_whose_smart_could_not_be_read_is_red(tmp_path):
    """The bug itself: this was ``warn reported_uncorrectable`` -- orange, written."""
    verdict, record, _ = probe(tmp_path, EVO_870_UNREAD, rc=4)   # bit 2: a SMART command failed
    assert verdict == "fail", record
    assert record[:2] == ["fail", "health_unread"], record


def test_the_gate_never_writes_it_and_says_why_in_hebrew(tmp_path):
    written, run, err = preflight(tmp_path, EVO_870_UNREAD)
    assert written == "", (written, err)
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    line = (run / "targets/sda/error").read_text(encoding="utf-8")
    assert "health_unread" not in line and "ה-SMART לא נקרא" in line, line


def test_the_local_clone_and_the_restore_see_it_red(tmp_path):
    """localclone.sh and single_restore.sh refuse a target by smart_red, the same probe."""
    from test_smart import PRELUDE, env, make_smartctl, sh
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, EVO_870_UNREAD, 4)
    out = sh(env(run, dev, smartctl=sc) + PRELUDE + 'smart_red sda; echo "rc=$?"')
    assert out.stdout.strip().endswith("rc=0"), (out.stdout, out.stderr)


# --- the guards: what stays "not checked" (written) stays so -------------------------


def test_a_status_computed_from_the_attributes_stays_unchecked(tmp_path):
    """#1921 s2: the disk does not answer RETURN STATUS, smartctl judged its table --
    not the F1, and not "unread" either."""
    body = EVO_870_UNREAD.replace(
        "SMART overall-health self-assessment test result: UNKNOWN!\n"
        "SMART Status, Attributes and Thresholds cannot be read.\n",
        "SMART overall-health self-assessment test result: PASSED\n"
        "Warning: This result is based on an Attribute check.\n")
    verdict, record, _ = probe(tmp_path, body, rc=4)
    assert verdict != "fail", record


def test_no_health_line_at_all_stays_unchecked(tmp_path):
    """No verdict printed (a bridge that passes nothing through): "not checked", as before."""
    body = EVO_870_UNREAD.replace("SMART overall-health self-assessment test result: UNKNOWN!\n", "")
    body = body.replace("0x04  0x008  4              37", "0x04  0x008  4               0")
    verdict, record, _ = probe(tmp_path, body, rc=4)
    assert verdict == "unchecked", record
