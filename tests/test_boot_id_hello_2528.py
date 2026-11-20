"""#2528 (שלב 1) — ה-hello של הסוכן נושא את מזהה האתחול ואת ה-wwid של כל דיסק.

מזהה האתחול הוא הראיה שהשרת משווה כדי לדעת שמכונה כבתה או אותחלה באמצע סבב
(`server/rounds/interrupt.py`); ה-wwid הוא חלק מזהות הדיסק ביומן הכתיבה. שניהם
**חסרים** כשאי אפשר לקרוא אותם — "לא ידוע", לעולם לא ערך מומצא או ריק (עיקרון 5).
"""

from __future__ import annotations

import json

import pytest

from test_agent import AGENT, BASH, fake_machine, posix, sh   # noqa: F401 — fixture

pytestmark = pytest.mark.skipif(BASH is None, reason="צריך bash להרצת הסוכן")

BOOT = "5b1f6f0e-3c0a-4b52-9d4e-0d6a2f1c7e88"


def build_hello(machine) -> dict:
    return json.loads(sh(
        f'export SYSROOT={posix(machine["sysroot"])!r} DEVROOT={posix(machine["dev"])!r} '
        f'RUN_DIR={posix(machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'. {posix(AGENT)}/lib/hw/diskinfo.sh; build_hello'))


def write_boot_id(machine, text: str) -> None:
    path = machine["sysroot"] / "proc/sys/kernel/random"
    path.mkdir(parents=True, exist_ok=True)
    (path / "boot_id").write_text(text)


def test_hello_carries_the_kernels_boot_id(fake_machine):
    write_boot_id(fake_machine, BOOT + "\n")
    assert build_hello(fake_machine).get("boot_id") == BOOT


def test_hello_without_a_readable_boot_id_omits_the_field(fake_machine):
    """אין קובץ: השדה חסר, וה-hello עדיין JSON תקין — לא `""` ולא מזהה מומצא."""
    assert "boot_id" not in build_hello(fake_machine)


@pytest.mark.parametrize("text", ["", "\n", "not a boot id\n", BOOT[:-1] + "\n",
                                  BOOT.upper() + "\n", BOOT + '","x":"y\n'])
def test_a_boot_id_that_is_not_in_the_kernels_form_is_omitted(fake_machine, text):
    write_boot_id(fake_machine, text)
    assert "boot_id" not in build_hello(fake_machine)


def test_a_sata_disk_reports_the_wwid_of_its_device(fake_machine):
    (fake_machine["sysroot"] / "sys/block/sda/device/wwid").write_text("naa.5002538e40a1b2c3\n")
    (disk,) = build_hello(fake_machine)["disks"]
    assert disk.get("wwid") == "naa.5002538e40a1b2c3"


def test_an_nvme_disk_reports_the_wwid_of_its_block_device(fake_machine):
    (fake_machine["sysroot"] / "sys/block/sda/wwid").write_text("eui.0025385b21404f1c\n")
    (fake_machine["sysroot"] / "sys/block/sda/device/wwid").write_text("naa.ffff\n")
    (disk,) = build_hello(fake_machine)["disks"]
    assert disk.get("wwid") == "eui.0025385b21404f1c"


def test_a_disk_without_a_wwid_omits_the_field(fake_machine):
    (disk,) = build_hello(fake_machine)["disks"]
    assert "wwid" not in disk
