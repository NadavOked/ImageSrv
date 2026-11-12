"""#2316 -- two disks on one IDE channel (master/slave) were drawn as the same drawer.

Found on the clone stick of v0.80.4 in QEMU/SeaBIOS (03/11): `-drive if=ide` twice
puts both disks on ata1, as master and slave. `port_from_path` reads the N of
ataN only, so the local-clone screen drew two identical rows, "SATA 0" -- before
an erase the operator could not tell which disk is which (principle 7).

The coordinator's decision (03/11, option 1): display only. The port stays the
N of ataN on the wire (hello, target_ports, the key sata:N, the drawer); the
screens add " · ראשי" / " · משני" from the SCSI target of H:C:T:L, and only
when another device shares the port -- a disk alone on its channel keeps its
name exactly as before.

Where it runs: bash only (Windows too). The links are stubbed, as in
test_drawer_map_gui_1495: an H:C:T:L path holds colons, which a Windows test
tree cannot hold -- the link is stubbed, the parse is not.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, posix
from test_slot_names_704_gui import sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))

LIBS = "".join(f". {posix(flat_lib())}/{n}.sh; "
               for n in ("common", "sysinfo", "diskinfo", "disklink", "slotnames", "usbmap"))   # diskinfo: port_from_path moved out of sysinfo.sh (#2385)
PCI = "/sys/devices/pci0000:00/0000:00:01.1"
# sda = ata1 master, sdb = ata1 slave, sdc = alone on ata2, sr0 = ata2 slave
# (a CD on the channel of sdc), sdz = the stick (usb, no ataN).
LINKS = {
    "sda": f"{PCI}/ata1/host0/target0:0:0/0:0:0:0",
    "sdb": f"{PCI}/ata1/host0/target0:0:1/0:0:1:0",
    "sdc": f"{PCI}/ata2/host1/target1:0:0/1:0:0:0",
    "sr0": f"{PCI}/ata2/host1/target1:0:1/1:0:1:0",
    "sdz": "/sys/devices/pci0000:00/0000:00:04.0/usb1/1-1/1-1:1.0/host2/target2:0:0/2:0:0:0",
}


def _readlink(devs: tuple[str, ...]) -> str:
    cases = "".join(f'*/{d}/*) echo {LINKS[d]} ;; ' for d in devs)
    return f'readlink() {{ case "$2" in {cases}*) echo /sys/devices/virtual/block/x ;; esac; }}; '


def _run(tmp_path: Path, devs: tuple[str, ...], script: str, names: str | None = None) -> str:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    root = tmp_path / "root"
    for d in devs:
        (root / "sys/block" / d / "device").mkdir(parents=True, exist_ok=True)
    (root / "sys/block").mkdir(parents=True, exist_ok=True)
    if names is not None:
        (run / "drawer_names").write_text(names, encoding="utf-8", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r}; {LIBS}{_readlink(devs)}{script}')
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_the_port_on_the_wire_stays_the_ataN_of_both(tmp_path):
    """Display only: master and slave are both port 1 -- the identity the
    server, target_ports and drawerselect.sh read stays what it was."""
    out = _run(tmp_path, ("sda", "sdb"), 'printf "%s;" "$(disk_port sda)" "$(disk_port sdb)"')
    assert out == "1;1;"


def test_master_and_slave_on_one_channel_get_two_different_names(tmp_path):
    out = _run(tmp_path, ("sda", "sdb", "sdc"),
               'for d in sda sdb; do disk_drawer "$d" 1; echo; done')
    assert out.splitlines() == ["1|SATA 0 · ראשי", "1|SATA 0 · משני"]


def test_a_disk_alone_on_its_channel_keeps_its_name_exactly(tmp_path):
    """No slave: "SATA 0", as before #2316 -- not "SATA 0 · ראשי"."""
    out = _run(tmp_path, ("sda",), 'disk_drawer sda 1; echo')
    assert out.splitlines() == ["1|SATA 0"]


def test_another_port_is_not_a_partner(tmp_path):
    """sdc is alone on ata2: a disk on ata1 does not make it "ראשי"."""
    out = _run(tmp_path, ("sda", "sdb", "sdc"), 'disk_drawer sdc 2; echo')
    assert out.splitlines() == ["2|SATA 1"]


def test_a_cd_drive_on_the_slave_names_the_disk_master(tmp_path):
    """A DVD drive as the slave of sdc's channel (an HP in IDE mode): the two
    devices of the channel are told apart, the disk as "ראשי"."""
    out = _run(tmp_path, ("sdc", "sr0"), 'disk_drawer sdc 2; echo; disk_drawer sr0 2; echo')
    assert out.splitlines() == ["2|SATA 1 · ראשי", "2|SATA 1 · משני"]


def test_the_stick_name_of_the_connection_is_told_apart_too(tmp_path):
    """#2001: a name on the stick replaces the connection on the card -- the
    same name for both disks of sata:1 would be the same bug again."""
    out = _run(tmp_path, ("sda", "sdb", "sdc"),
               'printf "%s;" "$(usb_drawer_name sda 1)" "$(usb_drawer_name sdb 1)" "$(usb_drawer_name sdc 2)"',
               names="sata:1|למעלה\nsata:2|למטה\n")
    assert out == "למעלה · ראשי;למעלה · משני;למטה;"


def test_no_name_stays_no_name(tmp_path):
    """A connection with no name is still nothing: the screen falls back to the
    connection (which carries the role) -- never a bare " · ראשי"."""
    out = _run(tmp_path, ("sda", "sdb"), 'printf "[%s]" "$(usb_drawer_name sda 1)"',
               names="sata:2|למטה\n")
    assert out == "[]"
