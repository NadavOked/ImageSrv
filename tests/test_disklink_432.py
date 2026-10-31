"""‏#432 — מהירות קישור ה-SATA ומונה ה-CRC של כל מגירה, ב-hello.

הסוכן קורא מ-sysfs מזויף (`SYSROOT`) על ה-sh האמיתי, וה-JSON נטען
בפייתון. שלושה מצבים ולא שניים (עיקרון 5): קישור שנקרא, קישור שנקרא
מתחת למקסימום של הפורט, וקישור **שלא נקרא** — שהוא `null`, לא 6.0 ולא 0.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 — fixture
from test_cloner_gui import sh as proc_sh   # CompletedProcess, לא stdout
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

#: CAP של בקר AHCI אמיתי (Intel, ISS=3 בסיביות 23:20 = ‏6 Gbps).
CAP_GEN3 = "c734ff45"
CAP_GEN2 = "c724ff45"        # ISS=2 = ‏3 Gbps


def _sata(sysroot: Path, ata: int = 2, host: int = 1, spd: str | None = "6.0 Gbps",
          caps: str | None = CAP_GEN3, hw_limit: str | None = "<unknown>") -> None:
    """sda מאחורי ataN/hostM, כמו שהקרנל בונה את הקישור."""
    target = sysroot / f"sys/devices/pci0000_00/ata{ata}/host{host}/target{host}_0_0/{host}_0_0_0"
    target.mkdir(parents=True)
    (target / "model").write_text("Samsung SSD 870 EVO 250GB   \n")
    device = sysroot / "sys/block/sda/device"
    shutil.rmtree(device)
    try:
        device.symlink_to(target, target_is_directory=True)
    except OSError as exc:                       # ווינדוס בלי הרשאת symlink
        pytest.skip(f"symlinks are not available here: {exc}")
    link = sysroot / f"sys/class/ata_link/link{ata}"
    link.mkdir(parents=True)
    if spd is not None:
        (link / "sata_spd").write_text(spd + "\n")
    if hw_limit is not None:
        (link / "hw_sata_spd_limit").write_text(hw_limit + "\n")
    if caps is not None:
        shost = sysroot / f"sys/class/scsi_host/host{host}"
        shost.mkdir(parents=True)
        (shost / "ahci_host_caps").write_text(caps + "\n")


def _disk(fake_machine, libs: str = "") -> dict:
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'. {posix(AGENT)}/lib/hw/disklink.sh; {libs} build_hello'
    )
    (disk,) = json.loads(out)["disks"]
    return disk


def test_a_drawer_at_full_speed_reports_speed_and_port_max(fake_machine):
    _sata(fake_machine["sysroot"])
    disk = _disk(fake_machine)
    assert (disk["link_gbps"], disk["link_max_gbps"]) == (6.0, 6.0)
    assert disk["port"] == 2                     # השדות הקיימים לא זזו


@pytest.mark.parametrize("spd, expected", [("1.5 Gbps", 1.5), ("3.0 Gbps", 3.0)])
def test_a_drawer_below_the_port_max_reports_both_numbers(fake_machine, spd, expected):
    """05/09: ‏`ata1: SATA link up 1.5 Gbps` על פורט של 6 — זה הנתון שחסר."""
    _sata(fake_machine["sysroot"], spd=spd)
    disk = _disk(fake_machine)
    assert (disk["link_gbps"], disk["link_max_gbps"]) == (expected, 6.0)


@pytest.mark.parametrize("spd", ["<unknown>", None, "garbage"])
def test_an_unreadable_link_is_null_never_six(fake_machine, spd):
    """הבקרה השלילית שה-Issue דורש: מגירה ש-`sata_spd` שלה לא נקרא **אינה**
    מדווחת 6.0 — היא null, "לא ידוע"."""
    _sata(fake_machine["sysroot"], spd=spd)
    disk = _disk(fake_machine)
    assert disk["link_gbps"] is None
    assert disk["link_max_gbps"] == 6.0


def test_the_port_max_comes_from_the_controller_caps(fake_machine):
    _sata(fake_machine["sysroot"], spd="3.0 Gbps", caps=CAP_GEN2)
    disk = _disk(fake_machine)
    assert (disk["link_gbps"], disk["link_max_gbps"]) == (3.0, 3.0)


def test_a_firmware_limit_below_the_controller_is_the_ceiling(fake_machine):
    _sata(fake_machine["sysroot"], spd="3.0 Gbps", caps=CAP_GEN3, hw_limit="3.0 Gbps")
    assert _disk(fake_machine)["link_max_gbps"] == 3.0


def test_no_caps_and_no_firmware_limit_is_an_unknown_max(fake_machine):
    _sata(fake_machine["sysroot"], spd="3.0 Gbps", caps=None)
    disk = _disk(fake_machine)
    assert (disk["link_gbps"], disk["link_max_gbps"]) == (3.0, None)


def test_a_firmware_limit_alone_is_the_max(fake_machine):
    _sata(fake_machine["sysroot"], spd="1.5 Gbps", caps="zz", hw_limit="6.0 Gbps")
    assert _disk(fake_machine)["link_max_gbps"] == 6.0


def test_a_disk_not_behind_libata_has_no_link(fake_machine):
    """NVMe / VM SCSI / USB: אין ataN בנתיב — שני השדות null, בלי לנחש."""
    disk = _disk(fake_machine)
    assert disk["link_gbps"] is None and disk["link_max_gbps"] is None


def _smart_cache(fake_machine, line: str) -> None:
    d = fake_machine["run"] / "smart"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sda.v").write_text(line + "\n", newline="\n")


def test_the_crc_count_rides_the_hello_when_smartctl_read_it(fake_machine):
    _smart_cache(fake_machine, "ok ok 0 0 0 137")
    assert _disk(fake_machine, f'. {posix(AGENT)}/lib/smart/smart.sh;')["crc"] == 137


@pytest.mark.parametrize("line", ["unchecked open_failed 0 0 0 0",
                                  "unchecked no_smartctl 0 0 0 0",
                                  "unchecked no_smart 0 0 0 0"])
def test_a_crc_smartctl_did_not_read_is_null_not_zero(fake_machine, line):
    _smart_cache(fake_machine, line)
    assert _disk(fake_machine, f'. {posix(AGENT)}/lib/smart/smart.sh;')["crc"] is None


def test_no_smart_probe_yet_is_a_null_crc(fake_machine):
    assert _disk(fake_machine, f'. {posix(AGENT)}/lib/smart/smart.sh;')["crc"] is None


def test_hello_without_the_lib_carries_none_of_the_fields(fake_machine):
    """סוכן בלי disklink.sh: השדות נעדרים (השרת: "לא ידוע"), שאר ה-hello כשהיה."""
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; build_hello'
    )
    (disk,) = json.loads(out)["disks"]
    assert not {"link_gbps", "link_max_gbps", "crc"} & set(disk)


def test_the_agent_and_the_kiosk_load_the_lib():
    assert '"$LIB_DIR/disklink.sh"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '"$LIB_DIR/disklink.sh"' in (AGENT / "lib/gui/guibridge.sh").read_text(encoding="utf-8")


def test_the_cloner_standby_record_carries_the_link(tmp_path):
    """מסך ההמתנה של המשכפל (#1304) קורא את המגירות מ-`cdisk=`: שדות 7/8 הם
    הקישור והמקסימום של הפורט, בשרשרת הקיוסק עצמה (`gui_libs`)."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    sysroot = tmp_path / "sysroot"
    (sysroot / "sys/block/sda/device").mkdir(parents=True)
    (sysroot / "sys/block/sda/size").write_text("1000\n", newline="\n")
    _sata(sysroot, spd="1.5 Gbps")
    out = proc_sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; cloner_gui_state')
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    (line,) = [l for l in (gui / "state").read_text().splitlines() if l.startswith("cdisk=")]
    assert line.split("|")[6:8] == ["1.5", "6.0"], line
    assert line.split("|")[8] == "", line     # #1366: no threshold in this hello -- no CRC colour
