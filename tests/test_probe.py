"""‏#1049 שלב א' — בדיקת מכונה ב-hello: כל שדה נמדד · חסר→null · פגום→error.

רץ על ה-sh האמיתי מול sysfs מזויף (SYSFS_ROOT/PROC_ROOT דרך SYSROOT).
ה-JSON נטען בפייתון — לא "נראה תקין". בינארים אופציונליים (dmidecode,
nvme, arping, smartctl, blkid) מזויפים ב-PATH + PROBE_STUBS=1; בלי זה
הכלי של המארח אינו "ארוז ב-initramfs".
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import sizelimit
from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 — fixture

pytestmark = requires_native(("bash", BASH))

REPO = Path(__file__).resolve().parent.parent


def _chmod_x(path: Path) -> None:
    # stdin+stdout+stderr all detached: pytest capture on Windows otherwise
    # raises WinError 50 DuplicateHandle (#573 / test_agent.sh).
    subprocess.run(
        [BASH, "-c", f"chmod +x {posix(path)!r}"],
        check=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _stub(dir_: Path, name: str, body: str) -> None:
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    _chmod_x(p)


def _pci(root: Path, addr: str, vendor: str, device: str, klass: str, driver: bool) -> None:
    d = root / "sys/bus/pci/devices" / addr.replace(":", "-")
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "device").write_text(device + "\n")
    (d / "class").write_text(klass + "\n")
    if driver:
        (d / "driver").write_text("bound\n")  # [ -e driver ] — קובץ מספיק, בלי symlink


def probe_of(sysroot: Path, extra: str = "", stubs: Path | None = None,
             devroot: Path | None = None) -> dict:
    env = f"export SYSROOT={posix(sysroot)!r}; "
    if devroot is not None:
        env += f"export DEVROOT={posix(devroot)!r}; "
    if stubs is not None:
        env += "export PROBE_STUBS=1; "
        for name, var in (("dmidecode", "DMIDECODE"), ("nvme", "NVME"),
                          ("arping", "ARPING"), ("hwclock", "HWCLOCK"),
                          ("blkid", "BLKID"), ("smartctl", "SMARTCTL")):
            p = stubs / name
            if p.exists():
                env += f"export {var}={posix(p)!r}; "
    out = sh(
        env + extra
        + f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/probe.sh; "   # #1367: capped_run
        r'printf "{\"x\":0%s}" "$(probe_json)"'
    )
    return json.loads(out)["probe"]


def _agent_hello(fake_machine, libs: str) -> dict:
    return json.loads(sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'{libs} build_hello'
    ))


# --- גודל ------------------------------------------------------------------


def test_probe_sh_stays_under_the_lamp():
    n = sizelimit.assert_within_limit(AGENT / "lib" / "probe.sh")
    assert n <= 280
    assert sizelimit.count_lines(AGENT / "imagectl-agent") <= 300


def test_the_agent_loads_probe_sh():
    text = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/probe.sh"' in text


# --- שלושת המצבים, שדה-שדה -------------------------------------------------


def test_a_bare_sysroot_is_null_or_empty_never_invented(fake_machine):
    p = probe_of(fake_machine["sysroot"])
    json.dumps(p)  # תקין
    assert isinstance(p["probe_seconds"], int)
    assert p["power"] is None
    assert p["rtc"] is None
    assert p["cpu"] is None
    assert p["memory"]["total_bytes"] == 8388608 * 1024
    assert p["memory"]["dimms"] is None          # dmidecode לא ארוז
    assert p["memory"]["ecc"] is None
    assert p["thermal"] is None
    assert p["nic"] == []                        # יש net/, אין carrier=1
    assert p["pci_without_driver"] is None
    assert p["kernel"] is None
    assert p["pstore"] is None
    assert p["oem_key"] is None
    assert p["disks"] == [{"name": "sda", "nvme_smart": None, "smart_errors": None}]
    assert p["encryption"] is None               # blkid לא ארוז


def test_power_measured_on_mains_and_on_battery(fake_machine):
    sysroot = fake_machine["sysroot"]
    ac = sysroot / "sys/class/power_supply/AC"
    bat = sysroot / "sys/class/power_supply/BAT0"
    ac.mkdir(parents=True); bat.mkdir(parents=True)
    (ac / "type").write_text("Mains\n"); (ac / "online").write_text("1\n")
    (bat / "type").write_text("Battery\n"); (bat / "status").write_text("Charging\n")
    p = probe_of(sysroot)["power"]
    assert p == {"on_battery": False, "supply": "AC"}
    (ac / "online").write_text("0\n")
    (bat / "status").write_text("Discharging\n")
    p = probe_of(sysroot)["power"]
    assert p == {"on_battery": True, "supply": "BAT0"}


def test_power_error_when_supplies_exist_but_none_are_readable(fake_machine):
    d = fake_machine["sysroot"] / "sys/class/power_supply/HID"
    d.mkdir(parents=True)
    (d / "type").write_text("\n")   # ריק אחרי trim — לא קריא
    err = probe_of(fake_machine["sysroot"])["power"]
    assert err == {"error": "אין ספק חשמל קריא"}


def test_rtc_measured_and_garbage_is_error(fake_machine):
    rtc = fake_machine["sysroot"] / "sys/class/rtc/rtc0"
    rtc.mkdir(parents=True)
    (rtc / "since_epoch").write_text("1758124800\n")
    r = probe_of(fake_machine["sysroot"])["rtc"]
    assert r["hwclock_epoch"] == 1758124800
    assert isinstance(r["system_epoch"], int) and r["system_epoch"] > 0
    assert r["skew_seconds"] == 1758124800 - r["system_epoch"]
    (rtc / "since_epoch").write_text("not-a-clock\n")
    assert probe_of(fake_machine["sysroot"])["rtc"]["error"]


def test_cpu_measured_missing_vuln_dir_is_null(fake_machine):
    cpuinfo = fake_machine["sysroot"] / "proc/cpuinfo"
    cpuinfo.write_text(
        "processor\t: 0\nmodel name\t: Intel Core i5-8500\nmicrocode\t: 0xf4\n"
        "processor\t: 1\nmodel name\t: Intel Core i5-8500\nmicrocode\t: 0xf4\n",
        encoding="utf-8", newline="\n")
    c = probe_of(fake_machine["sysroot"])["cpu"]
    assert c["model"] == "Intel Core i5-8500"
    assert c["cores"] == 2
    assert c["microcode"] == "0xf4"
    assert c["vulnerabilities"] is None
    vdir = fake_machine["sysroot"] / "sys/devices/system/cpu/vulnerabilities"
    vdir.mkdir(parents=True)
    (vdir / "meltdown").write_text("Mitigation: PTI\n")
    (vdir / "spectre_v1").write_text("Not affected\n")
    c = probe_of(fake_machine["sysroot"])["cpu"]
    assert c["vulnerabilities"] == {"meltdown": "Mitigation: PTI", "spectre_v1": "Not affected"}


def test_cpuinfo_present_but_empty_is_error(fake_machine):
    (fake_machine["sysroot"] / "proc/cpuinfo").write_text("\n")
    assert probe_of(fake_machine["sysroot"])["cpu"] == {"error": "לא ניתן לקרוא את cpuinfo"}


def test_memory_ecc_and_dimms_stub(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    mc = sysroot / "sys/devices/system/edac/mc/mc0"
    mc.mkdir(parents=True)
    (mc / "ce_count").write_text("4\n"); (mc / "ue_count").write_text("1\n")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    _stub(stubs, "dmidecode", r"""
echo 'Memory Device
	Size: 8 GB
	Speed: 2666 MT/s
	Manufacturer: Samsung
	Configured Memory Speed: 2400 MT/s
	Module Manufacturer ID: Bank 1, Hex 0xCE
	Memory Subsystem Controller Manufacturer ID: Unknown
	Volatile Size: None
	Logical Size: None
Memory Device
	Size: No Module Installed
	Manufacturer: Not Specified'
""")  # השורות הנוספות הן מה שה-dmidecode האמיתי מדפיס — הן שברו את ה-JSON במעבדה (18/09)
    m = probe_of(sysroot, stubs=stubs)["memory"]
    assert m["total_bytes"] == 8388608 * 1024
    assert m["ecc"] == {"ce_count": 4, "ue_count": 1}
    assert m["dimms"] == [{"size_bytes": 8 * 1073741824, "speed_mts": 2666,
                           "manufacturer": "Samsung"}]
    (mc / "ce_count").write_text("nope\n")
    assert probe_of(sysroot)["memory"]["ecc"]["error"]


def test_thermal_measured(fake_machine):
    z = fake_machine["sysroot"] / "sys/class/thermal/thermal_zone0"
    z.mkdir(parents=True)
    (z / "type").write_text("x86_pkg_temp\n")
    (z / "temp").write_text("42000\n")
    assert probe_of(fake_machine["sysroot"])["thermal"] == [
        {"type": "x86_pkg_temp", "temp_c": 42}]


def test_nic_with_link_and_arping_duplicate(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    eth0 = sysroot / "sys/class/net/eth0"
    (eth0 / "carrier").write_text("1\n")
    (eth0 / "speed").write_text("1000\n")
    (eth0 / "duplex").write_text("full\n")
    st = eth0 / "statistics"; st.mkdir()
    (st / "rx_crc_errors").write_text("2\n")
    (st / "rx_dropped").write_text("0\n")
    (st / "tx_errors").write_text("1\n")
    (st / "collisions").write_text("0\n")
    (sysroot / "sys/class/net/eth1" / "carrier").write_text("0\n")
    nic = probe_of(sysroot)["nic"]
    assert len(nic) == 1 and nic[0]["name"] == "eth0"
    assert nic[0]["speed_mbps"] == 1000 and nic[0]["duplex"] == "full"
    assert nic[0]["stats"] == {"rx_crc_errors": 2, "rx_dropped": 0,
                               "tx_errors": 1, "collisions": 0}
    assert nic[0]["ip_conflict"] is None          # arping לא ארוז
    stubs = tmp_path / "stubs"; stubs.mkdir()
    _stub(stubs, "arping", "exit 1\n")
    nic = probe_of(sysroot, extra="export IFACE=eth0 IP=10.44.12.187; ", stubs=stubs)["nic"]
    assert nic[0]["ip_conflict"] == {"checked": True, "duplicate": True}
    _stub(stubs, "arping", "exit 0\n")
    nic = probe_of(sysroot, extra="export IFACE=eth0 IP=10.44.12.187; ", stubs=stubs)["nic"]
    assert nic[0]["ip_conflict"] == {"checked": True, "duplicate": False}
    _stub(stubs, "arping", "exit 4\n")
    nic = probe_of(sysroot, extra="export IFACE=eth0 IP=10.44.12.187; ", stubs=stubs)["nic"]
    assert nic[0]["ip_conflict"]["error"].startswith("arping rc")


def test_pci_without_driver_skips_bound(fake_machine):
    sysroot = fake_machine["sysroot"]
    _pci(sysroot, "0000:00:1f.6", "0x8086", "0x15bc", "0x020000", driver=False)
    _pci(sysroot, "0000:00:17.0", "0x8086", "0xa352", "0x010601", driver=True)
    _pci(sysroot, "0000:00:02.0", "garbage", "0x1234", "0x030000", driver=False)
    assert probe_of(sysroot)["pci_without_driver"] == ["8086:15bc:020000"]


def test_kernel_lockdown_and_taint(fake_machine):
    sysroot = fake_machine["sysroot"]
    ld = sysroot / "sys/kernel/security"; ld.mkdir(parents=True)
    (ld / "lockdown").write_text("[none] integrity confidentiality\n")
    tn = sysroot / "proc/sys/kernel"; tn.mkdir(parents=True)
    (tn / "tainted").write_text("0\n")
    assert probe_of(sysroot)["kernel"] == {
        "lockdown": "[none] integrity confidentiality", "taint": 0}
    (tn / "tainted").write_text("nope\n")
    assert probe_of(sysroot)["kernel"]["error"]


def test_pstore_empty_and_crashed(fake_machine):
    sysroot = fake_machine["sysroot"]
    ps = sysroot / "sys/fs/pstore"; ps.mkdir(parents=True)
    assert probe_of(sysroot)["pstore"] == {
        "crashed": False, "files": [], "excerpt": None}
    (ps / "dmesg-ramoops-0").write_text("panic: boom " + ("x" * 500), newline="\n")
    got = probe_of(sysroot)["pstore"]
    assert got["crashed"] is True
    assert got["files"] == ["dmesg-ramoops-0"]
    assert got["excerpt"].startswith("panic: boom")
    assert len(got["excerpt"]) == 400
    (ps / "console-ramoops-0").write_text("console\n", newline="\n")
    assert set(probe_of(sysroot)["pstore"]["files"]) == {
        "dmesg-ramoops-0", "console-ramoops-0"}


def test_oem_key_from_msdm_and_garbage_is_error(fake_machine):
    sysroot = fake_machine["sysroot"]
    key = b"XXXXX-XXXXX-XXXXX-XXXXX-XXXXX"
    assert len(key) == 29
    msdm = sysroot / "sys/firmware/acpi/tables"; msdm.mkdir(parents=True)
    (msdm / "MSDM").write_bytes(b"\x00" * 56 + key)
    assert probe_of(sysroot)["oem_key"] == key.decode("ascii")
    (msdm / "MSDM").write_bytes(b"\x00" * 80)
    assert probe_of(sysroot)["oem_key"]["error"]


def test_disks_nvme_smart_and_smartctl(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    nv = sysroot / "sys/block/nvme0n1"; nv.mkdir(parents=True)
    (nv / "size").write_text("1\n")
    stubs = tmp_path / "stubs"; stubs.mkdir()
    _stub(stubs, "nvme", """
cat <<'EOF'
{"critical_warning": 0, "percentage_used": 5, "media_errors": 1, "unsafe_shutdowns": 2}
EOF
""")
    _stub(stubs, "smartctl", """
cat <<'EOF'
{"ata_smart_error_log": {"summary": {"count": 3}}}
EOF
exit 0
""")
    disks = {d["name"]: d for d in probe_of(sysroot, stubs=stubs,
                                            devroot=fake_machine["dev"])["disks"]}
    assert disks["sda"]["nvme_smart"] is None
    assert disks["sda"]["smart_errors"] == {"count": 3}
    assert disks["nvme0n1"]["nvme_smart"] == {
        "critical_warning": 0, "percentage_used": 5,
        "media_errors": 1, "unsafe_shutdowns": 2}
    _stub(stubs, "nvme", "echo not-json; exit 0\n")
    disks = {d["name"]: d for d in probe_of(sysroot, stubs=stubs,
                                            devroot=fake_machine["dev"])["disks"]}
    assert disks["nvme0n1"]["nvme_smart"]["error"]
    _stub(stubs, "smartctl", "echo '{}'; exit 2\n")   # bit 1 = open failed
    disks = {d["name"]: d for d in probe_of(sysroot, stubs=stubs,
                                            devroot=fake_machine["dev"])["disks"]}
    assert "לא הצליח לפתוח" in disks["sda"]["smart_errors"]["error"]


def test_nvme_smart_log_is_capped(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/block/nvme0n1").mkdir(parents=True)
    stubs = tmp_path / "stubs"; stubs.mkdir()
    _stub(stubs, "nvme", "sleep 20\n")
    disk = next(d for d in probe_of(
        sysroot, extra="NVME_MAX_S=1; ", stubs=stubs,
        devroot=fake_machine["dev"])["disks"] if d["name"] == "nvme0n1")
    assert disk["nvme_smart"] == {"error": "nvme smart-log לא ענה"}


def test_capture_files_in_tmpdir_are_removed_after_the_answer(fake_machine, tmp_path):
    """#2125: בלי RUN_DIR הפלט של nvme/smartctl נכתב ל-``$TMPDIR/probe.<pid>.*``
    ונשאר שם אחרי שהתשובה כבר נקראה. הראיה שהבדיקה רצה היא ה-JSON עצמו;
    תיקייה ריקה בלי התשובה אינה ניקוי."""
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/block/nvme0n1").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _stub(stubs, "nvme", """
cat <<'EOF'
{"critical_warning": 0, "percentage_used": 5, "media_errors": 1, "unsafe_shutdowns": 2}
EOF
""")
    _stub(stubs, "smartctl", """
cat <<'EOF'
{"ata_smart_error_log": {"summary": {"count": 3}}}
EOF
exit 0
""")
    extra = f"export TMPDIR={posix(scratch)!r}; unset RUN_DIR; "
    disks = {d["name"]: d for d in probe_of(
        sysroot, extra=extra, stubs=stubs, devroot=fake_machine["dev"])["disks"]}
    assert disks["sda"]["smart_errors"] == {"count": 3}
    assert disks["nvme0n1"]["nvme_smart"]["percentage_used"] == 5
    left = sorted(p.name for p in scratch.iterdir())
    assert left == [], left
    _stub(stubs, "nvme", "sleep 20\n")
    disk = next(d for d in probe_of(
        sysroot, extra=extra + "NVME_MAX_S=1; ", stubs=stubs,
        devroot=fake_machine["dev"])["disks"] if d["name"] == "nvme0n1")
    assert disk["nvme_smart"] == {"error": "nvme smart-log לא ענה"}
    left = sorted(p.name for p in scratch.iterdir())
    assert left == [], left


def test_encryption_luks_and_bitlocker(fake_machine, tmp_path):
    stubs = tmp_path / "stubs"; stubs.mkdir()
    _stub(stubs, "blkid", r"""
echo '/dev/sda3: UUID="x" TYPE="crypto_LUKS"
/dev/sda2: TYPE="BitLocker"
/dev/sda1: TYPE="vfat"'
exit 0
""")
    enc = probe_of(fake_machine["sysroot"], stubs=stubs)["encryption"]
    assert enc == [{"node": "/dev/sda3", "type": "crypto_LUKS"},
                   {"node": "/dev/sda2", "type": "BitLocker"}]
    _stub(stubs, "blkid", "exit 4\n")
    assert probe_of(fake_machine["sysroot"], stubs=stubs)["encryption"]["error"]


def test_missing_optional_tools_are_null_not_ok(fake_machine):
    """dmidecode/nvme/arping/smartctl/blkid חסרים → null, לא מערך ריק שאומר 'בדקנו'."""
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/block/nvme0n1").mkdir(parents=True)
    eth0 = sysroot / "sys/class/net/eth0"
    (eth0 / "carrier").write_text("1\n")
    p = probe_of(sysroot, extra="export IFACE=eth0 IP=10.0.0.1; ")
    assert p["memory"]["dimms"] is None
    disks = {d["name"]: d for d in p["disks"]}
    assert disks["nvme0n1"]["nvme_smart"] is None
    assert disks["sda"]["smart_errors"] is None
    assert p["nic"][0]["ip_conflict"] is None
    assert p["encryption"] is None


# --- hello ------------------------------------------------------------------


def test_hello_schema_2_carries_probe(fake_machine):
    hello = _agent_hello(fake_machine, f'. {posix(AGENT)}/lib/probe.sh;')
    assert hello["schema"] == 2
    assert "probe" in hello
    assert isinstance(hello["probe"]["probe_seconds"], int)
    assert hello["probe"]["memory"]["total_bytes"] == 8388608 * 1024
    assert hello["disks"], "שאר ה-hello נשאר כפי שהיה"


def test_hello_without_the_probe_lib_has_no_probe_field(fake_machine):
    assert "probe" not in _agent_hello(fake_machine, "")


# --- מטמון: hello הוא ה-poll ונשאר זול ---------------------------------------


def test_probe_is_cached_in_run_dir_between_hellos(fake_machine):
    """‏arping ו-smartctl אינם רצים בכל poll: התשובה נשמרת ב-$RUN_DIR/probe.json
    ומוגשת משם עד PROBE_TTL. שינוי ב-sysfs בתוך החלון אינו נראה; אחרי פקיעה — כן."""
    sysroot = fake_machine["sysroot"]
    ac = sysroot / "sys/class/power_supply/AC"
    bat = sysroot / "sys/class/power_supply/BAT0"
    ac.mkdir(parents=True); bat.mkdir(parents=True)
    (ac / "type").write_text("Mains\n"); (ac / "online").write_text("1\n")
    (bat / "type").write_text("Battery\n"); (bat / "status").write_text("Charging\n")
    run = f'export RUN_DIR={posix(fake_machine["run"])!r}; '
    assert probe_of(sysroot, extra=run)["power"] == {"on_battery": False, "supply": "AC"}
    assert (fake_machine["run"] / "probe.json").exists()
    (ac / "online").write_text("0\n"); (bat / "status").write_text("Discharging\n")   # נותק מהחשמל
    assert probe_of(sysroot, extra=run)["power"]["on_battery"] is False   # עדיין מהמטמון
    (fake_machine["run"] / "probe.json.at").write_text("0")  # המטמון פקע
    assert probe_of(sysroot, extra=run)["power"] == {"on_battery": True, "supply": "BAT0"}


def test_probe_without_a_writable_run_dir_still_answers(fake_machine):
    p = probe_of(fake_machine["sysroot"], extra="export RUN_DIR=/nonexistent/dir; ")
    assert "probe_seconds" in p
