"""#3008 — שליפה מכוונת מסווגת "כבל" במקום "נשלף".

ברזל 12/11 (משכפל 2, `disk_failures` #1): נדב שלף את הדיסק ביד ב-74%, הסוכן כתב
"הכונן נעלם מהמערכת (נשלף או נותק)" וב-`ata_log` `connection status changed`,
`PHYRdyChg … DevExch`, `SATA link down`, `detaching` — והשרת סיווג `cable`, כי
`classify` בדק קו לפני שליפה, ושליפה באמצע פקודה מייצרת גם `10B8B`/`Handshk`/
`ATA bus error`. המשמעות: "כבל" נדבק לחריץ ולדיסק הבא שייכנס אליו (#874).

הכלל: ראיה חיובית לשליפה (`DevExch`, או הסוכן שראה את הכונן נעלם) גוברת על קו
**חלש**. ראיה לכבל **מלפני** הנפילה (`ICRC`, `limiting SATA link speed`) נשארת כבל.
"""

from __future__ import annotations

import pytest

from server import db
from server.disks import ata_cause, disk_failures

#: שליפה באמצע כתיבה: אותו חלון, עם שגיאות הקידוד שהקו מייצר כשהוא נקטע.
PULL_MID_WRITE = [
    "[  900.100001] ata1: exception Emask 0x10 SAct 0x1ff0 SErr 0x4050002 action 0xe frozen",
    "[  900.100002] ata1: irq_stat 0x00400040, connection status changed",
    "[  900.100003] ata1: SError: { RecovComm PHYRdyChg 10B8B Handshk DevExch }",
    "[  900.100004] ata1.00: failed command: WRITE FPDMA QUEUED",
    "[  900.100005]          res 40/00:00:00:00:00/00:00:00:00:00/40 Emask 0x10 (ATA bus error)",
    "[  905.200001] ata1: SATA link down (SStatus 0 SControl 300)",
    "[  905.300001] ata1.00: disable device",
    "[  905.400001] ata1.00: detaching (SCSI 0:0:0:0)",
]
AGENT_SAID_PULLED = "מחיצה 3: הכונן נעלם מהמערכת (נשלף או נותק)"

#: קו שנפל אחרי שהקרנל הוריד את מהירות הקישור: כבל, גם אם הכונן נעלם בסוף.
CABLE_THEN_GONE = [
    "[  300.0] ata1: SError: { PHYRdyChg 10B8B Handshk }",
    "[  310.0] ata1: limiting SATA link speed to 1.5 Gbps",
    "[  320.0] ata1: SATA link down (SStatus 0 SControl 300)",
]


@pytest.mark.parametrize("lines, error", [
    (PULL_MID_WRITE, AGENT_SAID_PULLED),   # המקרה מהברזל: שני הסימנים
    (PULL_MID_WRITE, ""),                  # רק DevExch: ה-AHCI אמר שההתקן הוחלף
    (["[ 1.0] ata1: SError: { 10B8B Handshk }"], AGENT_SAID_PULLED),   # רק מה שהסוכן ראה
])
def test_a_pull_with_line_errors_is_removed_not_cable(lines, error):
    assert ata_cause.classify(lines, error) == "removed"


@pytest.mark.parametrize("lines", [
    PULL_MID_WRITE + ["[  899.0] ata1: SError: { ICRC }"],
    PULL_MID_WRITE + ["[  899.0] ata1: limiting SATA link speed to 1.5 Gbps"],
    CABLE_THEN_GONE,
])
def test_a_cable_that_failed_before_the_drop_stays_a_cable(lines):
    """שומר נגד תיקון-יתר: ראיה מלפני הנפילה אינה נבלעת ב"נשלף"."""
    assert ata_cause.classify(lines, AGENT_SAID_PULLED) == "cable"


def test_line_errors_without_any_sign_of_a_pull_are_still_a_cable():
    no_pull = [line for line in PULL_MID_WRITE if "DevExch" not in line]
    assert ata_cause.classify(no_pull) == "cable"


def test_a_pull_does_not_colour_the_disk_nor_the_next_one_in_the_slot(tmp_path):
    """הנזק של #3008: רשומת כבל נדבקת לחריץ. כ-`removed` הרשומה נשמרת ביומן
    ואינה חוזרת ב-hello — לא לדיסק שנשלף ולא לדיסק אחר באותו חריץ."""
    conn = db.connect(tmp_path / "t.db")
    mac = "b4:2e:99:07:1a:c4"
    row = disk_failures.record(conn, "ses1", mac, {
        "dev": "sda", "state": "failed", "error": AGENT_SAID_PULLED, "serial": "S-PULLED",
        "port": 1, "ata_port": 1, "ata_log": PULL_MID_WRITE})
    assert row["cause"] == "removed"
    assert disk_failures.open_for(conn, mac, [{"serial": "S-PULLED"}, {"serial": "S-OTHER"}]) == []
    assert disk_failures.list_open(conn) == []
