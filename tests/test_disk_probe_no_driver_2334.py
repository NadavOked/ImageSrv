"""‏#2334 — בקר דיסקים בלי מנהל התקן אינו "0 מגירות".

המשך של #1825/#1809. ‏#1914 טוען ואורז את מנהלי ההתקן של מחלקת PCI 01,
אבל כשבקר אחסון קיים ואף מנהל התקן לא נקשר אליו (כרטיס SATA שלא נארז,
‏RAID של יצרן), ה-hello דיווח `disks: []` עם `unchecked`/`no_disks`/
`no_ports` — והמסך אמר "לא חוברו דיסקים". המפעיל חיפש כבל, והתשובה
הייתה מנהל התקן או הגדרת BIOS. זו עיקרון 5א: "לא ראינו דיסקים" נקרא
כ"אין דיסקים".

‏`disk_probe` מקבל ערך חמישי, `no_driver`: אין דיסקים, ובעץ ה-PCI יש
התקן במחלקה `0x01*` בלי קישור `driver`. הראיה היא sysfs בלבד, ורק כשאין
דיסקים (#1702 — לא להאט אתחול). ‏sysfs שלא נקרא אינו `no_driver`.

הטסטים מריצים את `disk_probe` ו-`build_hello` האמיתיים דרך bash, על
עץ sysfs מזויף ו-`dmesg` מזויף — אותו דפוס כמו `test_drawer_probe.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH
from test_drawer_probe import NO_AHCI, NO_PORTS, PORTS_OK, _empty_sysroot, _hello, probe

native = requires_native(("bash", BASH))


def _pci(sysroot: Path, slot: str, cls: str, driver: bool) -> None:
    """התקן PCI מזויף. ‏`driver` הוא תיקייה ולא קישור — בווינדוס אין
    symlink, ו-`[ -e ]` של הסוכן מקבל את שניהם. בשם החריץ `-` במקום `:`
    (נתיב בווינדוס); הסוכן אינו קורא את השם."""
    dev = sysroot / "sys/bus/pci/devices" / slot
    dev.mkdir(parents=True)
    (dev / "class").write_text(cls + "\n")
    if driver:
        (dev / "driver").mkdir()


#: ‏SATA AHCI (‏`0x010601`) — הבקר שה-Issue נותן כדוגמה.
SATA = "0x010601"


@native
def test_storage_controller_without_driver_is_no_driver(tmp_path):
    """הלב של #2334: בקר `0x010601` בלי `driver` ‏→ `no_driver`.

    ה-`dmesg` בלי שורת ahci — בלי התיקון זה `unchecked`, וזה מה שהמפעיל
    ראה כ"0 מגירות"."""
    sysroot = _empty_sysroot(tmp_path)
    _pci(sysroot, "0000-03-00.0", SATA, driver=False)
    assert probe(sysroot, NO_AHCI, tmp_path / "b") == "no_driver"


@native
@pytest.mark.parametrize("dmesg, expected", [(NO_AHCI, "unchecked"),
                                             (PORTS_OK, "no_disks"),
                                             (NO_PORTS, "no_ports")])
def test_a_bound_controller_keeps_the_existing_answer(tmp_path, dmesg, expected):
    """אותו בקר **עם** `driver` ‏→ ההתנהגות הקיימת, ללא שינוי."""
    sysroot = _empty_sysroot(tmp_path)
    _pci(sysroot, "0000-00-1f.2", SATA, driver=True)
    assert probe(sysroot, dmesg, tmp_path / "b") == expected


@native
def test_no_driver_wins_over_no_ports(tmp_path):
    """הכרעה (נשארה פתוחה ב-Issue): ahci קשור עם 0 פורטים **וגם** בקר בלי
    מנהל התקן ‏→ `no_driver`. ‏`no_ports` אומר "אף פורט אינו מופעל" — וזה
    לא נכון על בקר שאיננו רואים דרכו; הדיסקים יכולים להיות עליו."""
    sysroot = _empty_sysroot(tmp_path)
    _pci(sysroot, "0000-00-1f.2", SATA, driver=True)
    _pci(sysroot, "0000-04-00.0", "0x010400", driver=False)   # RAID של יצרן
    assert probe(sysroot, NO_PORTS, tmp_path / "b") == "no_driver"


@native
def test_a_driverless_device_outside_class_01_is_not_no_driver(tmp_path):
    """רדיוס: כרטיס רשת (`0x0200`) בלי מנהל התקן אינו בקר דיסקים."""
    sysroot = _empty_sysroot(tmp_path)
    _pci(sysroot, "0000-05-00.0", "0x020000", driver=False)
    assert probe(sysroot, NO_AHCI, tmp_path / "b") == "unchecked"


@native
def test_unread_sysfs_is_not_no_driver(tmp_path):
    """עיקרון 5: אין עץ PCI שנקרא ‏→ אין ראיה, ולא `no_driver`. נשאר מה
    שהיה — `unchecked`."""
    sysroot = _empty_sysroot(tmp_path)          # אין sys/bus/pci כלל
    assert probe(sysroot, NO_AHCI, tmp_path / "b") == "unchecked"


@native
def test_a_disk_present_still_reports_drives(tmp_path):
    """הנתיב החם: יש דיסק ‏→ `drives`, גם כשיש בקר נוסף בלי מנהל התקן."""
    sysroot = _empty_sysroot(tmp_path)
    _pci(sysroot, "0000-03-00.0", SATA, driver=False)
    blk = sysroot / "sys/block/sda"
    blk.mkdir(parents=True)
    (blk / "removable").write_text("0\n")
    assert probe(sysroot, "echo BOOM >&2\nexit 3", tmp_path / "b") == "drives"


@native
def test_hello_carries_no_driver(tmp_path):
    """ממשק 2: ה-hello עצמו נושא `disk_probe: no_driver`."""
    sysroot = tmp_path / "root"
    net = sysroot / "sys/class/net/eth0"
    net.mkdir(parents=True)
    (net / "address").write_text("b4:2e:99:07:1a:c4\n")
    (sysroot / "proc").mkdir()
    (sysroot / "proc/meminfo").write_text("MemTotal:        8388608 kB\n")
    (sysroot / "sys/block").mkdir(parents=True)
    _pci(sysroot, "0000-03-00.0", SATA, driver=False)
    dev = tmp_path / "dev"
    dev.mkdir()
    hello = _hello(sysroot, dev, tmp_path / "h", NO_AHCI)
    assert hello["disks"] == []
    assert hello["disk_probe"] == "no_driver", hello.get("disk_probe")

