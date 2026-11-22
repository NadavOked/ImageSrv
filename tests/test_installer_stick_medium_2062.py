"""#2062 -- the installer knows the clone stick as its medium: the ISO on the stick's partition 3.

On the combined stick the server's ISO is the third partition, and the agent mounts it on /cdrom
(agent/lib/usb/usbinstall.sh). The engine names the medium by the device /proc/mounts shows on
/cdrom (installer/lib/disks.sh iso_source_device, disk_is_iso) -- here /dev/sdb3, a partition
number the ISO on a CD (/dev/sr0) or dd'ed onto a whole stick (/dev/sdb, /dev/sdb1) never had.

Two things hang on that answer:
  * the inventory line of the stick says iso=1 -- what the disk screen reads to leave the medium out
    of the list (#2968 hides every iso=1 disk; that is its change, not tested again here);
  * the engine refuses the stick as a target before any disk command, whatever the screen sent --
    "never install onto the stick itself" (#2062), and the drawers stay installable.

The medium is the disk the mount is on, not a disk that looks like a stick: a second USB disk on the
machine is an ordinary target.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_installer_engine import LINUX_ONLY, _answers, _command_shims, _machine, _run, _shim

pytestmark = LINUX_ONLY


def _disk(box, name: str, *, model: str, bus: str, removable: int, parts: int) -> None:
    d = box["root"] / "sys" / "block" / name
    (d / "device").mkdir(parents=True, exist_ok=True)
    (d / "size").write_text("3463168\n", newline="\n")            # the 1.77 GB stick of the first real build
    (d / "removable").write_text(f"{removable}\n", newline="\n")
    (d / "device" / "model").write_text(model + "\n", newline="\n")
    (d / "device" / "bus").write_text(bus + "\n", newline="\n")
    (box["dev"] / name).touch()
    for n in range(1, parts + 1):
        (d / f"{name}{n}").mkdir()
        (d / f"{name}{n}" / "partition").write_text(f"{n}\n", newline="\n")
        (box["dev"] / f"{name}{n}").touch()


def stick_machine(tmp_path: Path, mounted: str = "/dev/sdb3"):
    """sda: the server's disk. sdb: the clone stick (boot, data, the ISO). sdc: another USB disk."""
    box = _machine(tmp_path)
    _disk(box, "sdb", model="Flash Disk", bus="usb", removable=1, parts=3)
    _disk(box, "sdc", model="Other USB", bus="usb", removable=1, parts=1)
    box["mounts"].write_text(f"{mounted} /cdrom iso9660 ro 0 0\n", newline="\n")
    calls = _command_shims(tmp_path, box["env"])
    dev = box["dev"]
    _shim(tmp_path / "bin" / "blkid", f'''case "$*" in
*"PTTYPE"*"{dev}/sda") echo gpt ;;
*"PTTYPE"*"{dev}/sdb") echo dos ;;
*"PTTYPE"*"{dev}/sdc") echo dos ;;
*"TYPE"*"{dev}/sdb1") echo vfat ;;
*"TYPE"*"{dev}/sdb2") echo vfat ;;
*"TYPE"*"{dev}/sdb3") echo iso9660 ;;
*"TYPE"*"{dev}/sdc1") echo ntfs ;;
*) exit 2 ;;
esac
''')
    return box, calls


def inventory(box) -> dict[str, str]:
    got = _run(box, None, "--inventory")
    assert got.returncode == 0, got.stderr
    # the engine appends |smart=<verdict> to every disk line (not what these tests are about)
    return {line.split("|", 1)[0].removeprefix("disk="): re.sub(r"\|smart=[^|]*$", "", line)
            for line in got.stdout.splitlines()}


def test_the_stick_with_the_iso_on_partition_three_is_the_medium_in_the_inventory(tmp_path):
    box, _calls = stick_machine(tmp_path)
    lines = inventory(box)
    assert set(lines) == {"/dev/sda", "/dev/sdb", "/dev/sdc"}, lines
    assert lines["/dev/sdb"] == ("disk=/dev/sdb|model=Flash Disk|size=1773142016|bus=usb|removable=1|"
                                 "has=mbr:3 partitions (vfat, vfat, iso9660)|iso=1"), lines["/dev/sdb"]
    assert lines["/dev/sda"].endswith("|iso=0")
    assert lines["/dev/sdc"].endswith("|iso=0"), "a USB disk that is not the medium was marked as it"


def test_the_stick_is_refused_as_a_target_before_any_disk_command(tmp_path):
    box, calls = stick_machine(tmp_path)
    got = _run(box, _answers(tmp_path / "answers", disk="/dev/sdb"), "--dry-run")
    assert got.returncode == 2 and "[iso-target]" in got.stderr, got.stdout + got.stderr
    assert "refusing the disk that contains /cdrom: /dev/sdb" in got.stderr
    assert not calls.exists(), calls.read_text(encoding="utf-8")


@pytest.mark.parametrize("target", ["/dev/sda", "/dev/sdc"])
def test_the_other_disks_stay_installable_with_the_stick_as_the_medium(tmp_path, target):
    box, _calls = stick_machine(tmp_path)
    got = _run(box, _answers(tmp_path / "answers", disk=target), "--dry-run")
    assert "[iso-target]" not in got.stderr, got.stderr
    assert got.returncode == 0, got.stdout + got.stderr


def test_a_medium_mounted_from_a_drawer_would_leave_the_stick_a_target(tmp_path):
    """Why usbinstall.sh mounts the stick's own partition and never "a disk with the label":
    with /cdrom on a drawer, the engine protects the drawer -- and takes the stick."""
    box, _calls = stick_machine(tmp_path, mounted="/dev/sdc1")
    lines = inventory(box)
    assert lines["/dev/sdc"].endswith("|iso=1") and lines["/dev/sdb"].endswith("|iso=0"), lines
