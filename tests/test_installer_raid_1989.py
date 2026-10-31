"""#1989: RAID in the live installer -- the engine's dry run, its refusals, the
virtual-server rule, and the bridge's inventory/answers.

Nothing here touches a disk: the engine runs with --dry-run (must_run prints
the command instead of running it) against a fake sysfs/devroot, exactly as
tests/test_installer_engine.py does.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_installer_engine import (LINUX_ONLY, SH, _answers, _command_shims,
                                   _machine, _normal, _run)

REPO = Path(__file__).resolve().parent.parent
RAID_SH = REPO / "installer" / "lib" / "raid.sh"
pytestmark = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")


def _add_disks(box, *names: str, removable: str = "0", bus: str = "sata") -> None:
    for name in names:
        disk = box["root"] / "sys" / "block" / name
        (disk / "device").mkdir(parents=True)
        (disk / "size").write_text("125829120\n", newline="\n")
        (disk / "removable").write_text(removable + "\n", newline="\n")
        (disk / "device" / "model").write_text("WDC WD10EZEX\n", newline="\n")
        (disk / "device" / "bus").write_text(bus + "\n", newline="\n")
        for node in (name, name + "1", name + "2"):
            (box["dev"] / node).touch()
    (box["dev"] / "md0").touch()


def _metal(box) -> None:
    """A physical server: CPU flags without `hypervisor`, a vendor's DMI."""
    proc = box["root"] / "proc"
    (proc / "cpuinfo").write_text("processor\t: 0\nflags\t\t: fpu vme sse sse2 lm\n", newline="\n")
    dmi = box["root"] / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True, exist_ok=True)
    (dmi / "sys_vendor").write_text("HP\n", newline="\n")
    (dmi / "product_name").write_text("ProLiant DL20 Gen10\n", newline="\n")


def _raid_answers(tmp_path: Path, level: str, disks: str, spares: str = "0", **changes: str) -> Path:
    path = _answers(tmp_path / "answers", **changes)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"raid_level={level}\nraid_disks={disks}\nraid_spares={spares}\n")
    return path


def _box(tmp_path: Path, *extra: str):
    box = _machine(tmp_path)
    _add_disks(box, *extra)
    _metal(box)
    _command_shims(tmp_path, box["env"])
    return box


@LINUX_ONLY
@pytest.mark.parametrize(("level", "disks", "spares", "mdadm_tail"), [
    ("raid1", "/dev/sda,/dev/sdb", "0", "--level=1 --raid-devices=2 <dev>/sda2 <dev>/sdb2"),
    ("raid5", "/dev/sda,/dev/sdb,/dev/sdc", "0", "--level=5 --raid-devices=3 <dev>/sda2 <dev>/sdb2 <dev>/sdc2"),
    ("raid10", "/dev/sda,/dev/sdb,/dev/sdc,/dev/sdd", "0",
     "--level=10 --raid-devices=4 <dev>/sda2 <dev>/sdb2 <dev>/sdc2 <dev>/sdd2"),
    ("raid6", "/dev/sda,/dev/sdb,/dev/sdc,/dev/sdd", "0",
     "--level=6 --raid-devices=4 <dev>/sda2 <dev>/sdb2 <dev>/sdc2 <dev>/sdd2"),
    ("raid1", "/dev/sda,/dev/sdb,/dev/sdc", "1",
     "--level=1 --raid-devices=2 --spare-devices=1 <dev>/sda2 <dev>/sdb2 <dev>/sdc2"),
])
def test_dry_run_builds_one_array_over_every_member_and_boots_from_each(tmp_path, level, disks, spares, mdadm_tail):
    box = _box(tmp_path, "sdb", "sdc", "sdd")
    got = _run(box, _raid_answers(tmp_path, level, disks, spares), "--dry-run")
    assert got.returncode == 0, got.stderr
    lines = _normal(got.stdout, box)
    members = [d.removeprefix("/dev/") for d in disks.split(",")]
    for name in members:
        assert f"+ sgdisk --zap-all <dev>/{name}" in lines
        assert ("+ sgdisk --new=1:1MiB:+512MiB --typecode=1:EF00 --change-name=1:EFI "
                "--new=3:0:+1MiB --typecode=3:EF02 --change-name=3:bios_grub "
                f"--new=2:0:0 --typecode=2:FD00 --change-name=2:raid <dev>/{name}") in lines
        assert f"+ mkfs.vfat -F 32 -n EFI <dev>/{name}1" in lines
        assert f"+ chroot <target> grub-install --target=i386-pc /dev/{name}" in lines
    create = [line for line in lines if line.startswith("+ mdadm --create")]
    assert create == ["+ mdadm --create <dev>/md0 --run --metadata=1.2 --homehost=imagesrv-ta " + mdadm_tail]
    # the root filesystem is on the array, never on a member
    assert "+ mkfs.ext4 -F -L root <dev>/md0" in lines
    assert not any(line.startswith("+ mkfs.ext4") and "md0" not in line for line in lines)
    assert "+ mount <dev>/md0 <target>" in lines and "+ mount <dev>/sda1 <target>/boot/efi" in lines
    # every other member's ESP gets the UEFI loader too, without an NVRAM entry
    for index, name in enumerate(members[1:], start=2):
        assert (f"+ chroot <target> grub-install --target=x86_64-efi --efi-directory=/boot/efi{index} "
                "--bootloader-id=debian --force-extra-removable --no-nvram") in lines
        assert f"+ mount <dev>/{name}1 <target>/boot/efi{index}" in lines
    # mdadm.conf, then an initramfs that assembles the array, after the packages
    scan = lines.index("+ mdadm --detail --scan")
    assert lines[scan + 1] == "+ write-target mode=0644 <target>/etc/mdadm/mdadm.conf"
    assert lines[scan + 2] == "+ chroot <target> update-initramfs -u -k all"
    assert lines[scan + 3] == "+ write-target mode=0644 <target>/etc/imagesrv/raid"
    assert lines.index("+ chroot <target> apt-get install -y --no-install-recommends busybox-static "
                       "linux-image-amd64 grub-efi-amd64-signed shim-signed grub-pc-bin") < scan
    assert scan < lines.index("+ chroot <target> update-grub")


@LINUX_ONLY
def test_no_raid_answers_keep_the_single_disk_sequence(tmp_path):
    """Absent keys and an explicit none are the same single-disk install."""
    box = _box(tmp_path, "sdb")
    for answers in (_answers(tmp_path / "plain"), _raid_answers(tmp_path, "none", "")):
        got = _run(box, answers, "--dry-run")
        assert got.returncode == 0, got.stderr
        out = got.stdout
        assert "mdadm" not in out and "sdb" not in out and "efi2" not in out
        assert "+ sgdisk --zap-all " in out and "--typecode=2:8300" in out


@LINUX_ONLY
@pytest.mark.parametrize(("level", "disks", "spares", "needle"), [
    ("raid5", "/dev/sda,/dev/sdb", "0", "raid5 needs at least 3 disks, got 2"),
    ("raid6", "/dev/sda,/dev/sdb,/dev/sdc", "0", "raid6 needs at least 4 disks, got 3"),
    ("raid10", "/dev/sda,/dev/sdb,/dev/sdc", "0", "raid10 needs at least 4 disks, got 3"),
    ("raid1", "/dev/sda", "0", "raid1 needs at least 2 disks, got 1"),
    ("raid1", "/dev/sda,/dev/sdb", "1", "raid1 needs at least 3 disks, got 2"),
    ("raid5", "/dev/sda,/dev/sdb,/dev/sdc", "1", "a spare is offered only with RAID1"),
    ("raid0", "/dev/sda,/dev/sdb", "0", "unknown RAID level: raid0"),
    ("raid1", "/dev/sdb,/dev/sda", "0", "the first RAID disk must be the chosen disk"),
    ("raid1", "/dev/sda,/dev/sda", "0", "RAID disk listed twice"),
    ("raid1", "/dev/sda,/dev/sdz", "0", "RAID disk does not exist"),
    ("raid1", "/dev/sda,/dev/sdb1", "0", "RAID disk does not exist"),
    ("none", "/dev/sda,/dev/sdb", "0", "raid_disks given without a RAID level"),
    ("raid1", "/dev/sda,/dev/sdb", "2", "raid_spares must be 0 or 1"),
])
def test_a_wrong_raid_answer_is_named_before_any_disk_command(tmp_path, level, disks, spares, needle):
    box = _box(tmp_path, "sdb", "sdc")
    calls = Path(box["env"]["PATH"].split(os.pathsep)[0]).parent / "calls"
    got = _run(box, _raid_answers(tmp_path, level, disks, spares), "--dry-run")
    assert got.returncode == 2 and "[invalid-raid]" in got.stderr and needle in got.stderr, got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8")


@LINUX_ONLY
@pytest.mark.parametrize(("removable", "bus"), [("1", "sata"), ("0", "usb")])
def test_a_removable_or_usb_disk_is_not_a_raid_member(tmp_path, removable, bus):
    box = _machine(tmp_path)
    _add_disks(box, "sdb", removable=removable, bus=bus)
    _metal(box)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _raid_answers(tmp_path, "raid1", "/dev/sda,/dev/sdb"), "--dry-run")
    assert got.returncode == 2 and "[invalid-raid]" in got.stderr, got.stderr


@LINUX_ONLY
def test_the_install_medium_is_never_a_raid_member(tmp_path):
    box = _box(tmp_path, "sdb")
    box["mounts"].write_text("/dev/sdb1 /cdrom iso9660 ro 0 0\n", newline="\n")
    got = _run(box, _raid_answers(tmp_path, "raid1", "/dev/sda,/dev/sdb"), "--dry-run")
    assert got.returncode == 2 and "[iso-target]" in got.stderr


@LINUX_ONLY
@pytest.mark.parametrize("evidence", ["cpu", "vmware", "hyperv", "qemu"])
def test_a_virtual_server_is_refused_raid_with_the_reason(tmp_path, evidence):
    box = _box(tmp_path, "sdb")
    dmi = box["root"] / "sys" / "class" / "dmi" / "id"
    if evidence == "cpu":
        (box["root"] / "proc" / "cpuinfo").write_text("flags\t\t: fpu sse2 hypervisor lm\n", newline="\n")
    elif evidence == "vmware":
        (dmi / "sys_vendor").write_text("VMware, Inc.\n", newline="\n")
    elif evidence == "hyperv":
        (dmi / "sys_vendor").write_text("Microsoft Corporation\n", newline="\n")
        (dmi / "product_name").write_text("Virtual Machine\n", newline="\n")
    else:
        (dmi / "sys_vendor").write_text("QEMU\n", newline="\n")
    got = _run(box, _raid_answers(tmp_path, "raid1", "/dev/sda,/dev/sdb"), "--dry-run")
    assert got.returncode == 2 and "[raid-virtual]" in got.stderr, got.stderr
    assert "RAID is done by the host" in got.stderr
    # ...and the same server installs on one disk without complaint
    assert _run(box, _answers(tmp_path / "plain"), "--dry-run").returncode == 0


def _sh(script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([SH, "-c", f'. "$1"; shift; {script}', "_", str(RAID_SH), *args],
                          text=True, capture_output=True, env=env, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize(("count", "virt", "level"), [
    (0, "metal", "none"), (1, "metal", "none"), (2, "metal", "raid1"), (3, "metal", "raid5"),
    (4, "metal", "raid10"), (7, "metal", "raid10"),
    (2, "virtual", "none"), (4, "virtual", "none"), (3, "unknown", "none"),
])
def test_the_default_level_follows_the_count_on_metal_only(count, virt, level):
    got = _sh('raid_default "$1" "$2"', str(count), virt)
    assert got.returncode == 0 and got.stdout == level


def test_suitable_disks_are_counted_without_the_medium_removable_or_usb():
    lines = "\n".join([
        "disk=/dev/sda|model=A|size=1|bus=sata|removable=0|has=empty|iso=0",
        "disk=/dev/sdb|model=B|size=1|bus=nvme|removable=0|has=empty|iso=0",
        "disk=/dev/sdc|model=C|size=1|bus=usb|removable=0|has=empty|iso=0",
        "disk=/dev/sdd|model=D|size=1|bus=sata|removable=1|has=empty|iso=0",
        "disk=/dev/sr0|model=E|size=1|bus=sata|removable=0|has=empty|iso=1",
        "nic=ens33|x",
    ])
    got = subprocess.run([SH, "-c", '. "$1"; raid_eligible_count', "_", str(RAID_SH)], input=lines + "\n",
                         text=True, capture_output=True)
    assert got.returncode == 0 and got.stdout == "2"


@pytest.mark.parametrize(("cpu", "dmi", "kind"), [
    ("fpu sse2 lm", {"sys_vendor": "Dell Inc.", "product_name": "PowerEdge R250"}, "metal"),
    ("fpu sse2 hypervisor", {"sys_vendor": "Dell Inc."}, "virtual"),
    ("fpu sse2 lm", {"sys_vendor": "innotek GmbH"}, "virtual"),
    ("fpu sse2 lm", {"sys_vendor": "Xen"}, "virtual"),
    (None, {"sys_vendor": "Dell Inc."}, "unknown"),      # cpuinfo not read
    ("fpu sse2 lm", {}, "unknown"),                        # DMI not read
])
def test_virtual_detection_needs_positive_evidence_both_ways(tmp_path, cpu, dmi, kind):
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    if cpu is not None:
        (root / "proc" / "cpuinfo").write_text(f"flags\t\t: {cpu}\n", newline="\n")
    ids = root / "sys" / "class" / "dmi" / "id"
    ids.mkdir(parents=True)
    for name, value in dmi.items():
        (ids / name).write_text(value + "\n", newline="\n")
    got = _sh("virt_kind", env={**os.environ, "SYSROOT": str(root)})
    assert got.returncode == 0 and got.stdout == kind


# --- the bridge ---------------------------------------------------------------

JQ = shutil.which("jq")
BRIDGE_ONLY = pytest.mark.skipif(SH is None or JQ is None or os.name == "nt", reason="sh + jq on Linux")


@BRIDGE_ONLY
def test_bridge_inventory_reports_virt_and_the_default_for_the_suitable_disks(tmp_path):
    from test_installer_gui_bridge import _machine as bridge_machine, bridge
    m = bridge_machine(tmp_path)
    engine = m["engine"].read_text()
    engine = engine.replace(
        "printf 'disk=/dev/sda|model=VMware Virtual disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0\\n'",
        "printf 'disk=/dev/sda|model=A|size=1|bus=sata|removable=0|has=empty|iso=0\\n"
        "disk=/dev/sdb|model=B|size=1|bus=sata|removable=0|has=empty|iso=0\\n"
        "disk=/dev/sdc|model=C|size=1|bus=sata|removable=0|has=empty|iso=0\\n'")
    m["engine"].write_text(engine)
    (m["root"] / "proc").mkdir()
    (m["root"] / "proc" / "cpuinfo").write_text("flags\t\t: fpu lm\n")
    dmi = m["root"] / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    (dmi / "sys_vendor").write_text("HP\n")
    rc, out, err = bridge(m, "inventory")
    assert rc == 0, err
    assert len(out["disk"]) == 3
    assert out["virt"] == ["metal"] and out["raid_default"] == ["raid5"]
    (dmi / "sys_vendor").write_text("VMware, Inc.\n")
    rc, out, err = bridge(m, "inventory")
    assert out["virt"] == ["virtual"] and out["raid_default"] == ["none"]


@BRIDGE_ONLY
def test_bridge_writes_the_raid_choice_and_none_when_the_form_has_none(tmp_path):
    from test_installer_gui_bridge import FORM, _machine as bridge_machine, bridge
    m = bridge_machine(tmp_path)
    rc, _out, err = bridge(m, "validate", {**FORM, "raid_level": "raid1",
                                           "raid_disks": "/dev/sda,/dev/sdb", "raid_spares": "0"})
    assert rc == 0, err
    seen = (tmp_path / "answers.seen").read_text()
    assert "raid_level=raid1\nraid_disks=/dev/sda,/dev/sdb\nraid_spares=0\n" in seen
    rc, _out, err = bridge(m, "validate", FORM)
    seen = (tmp_path / "answers.seen").read_text()
    assert "raid_level=none\nraid_disks=\nraid_spares=0\n" in seen


@BRIDGE_ONLY
@pytest.mark.parametrize(("code", "hebrew"), [("invalid-raid", "RAID"), ("raid-virtual", "שרת וירטואלי")])
def test_bridge_puts_raid_failures_under_the_disk_field_in_hebrew(tmp_path, code, hebrew):
    from test_installer_gui_bridge import FORM, _machine as bridge_machine, bridge
    m = bridge_machine(tmp_path)
    engine = m["engine"].read_text().replace(
        "if [ \"$2\" = --dry-run ]; then exit 0; fi",
        f"if [ \"$2\" = --dry-run ]; then printf 'installer: [{code}] x\\n' >&2; exit 2; fi")
    m["engine"].write_text(engine)
    rc, out, err = bridge(m, "validate", {**FORM, "step": 1})
    assert rc == 0, err
    assert hebrew in out["errors.disk"][0]
