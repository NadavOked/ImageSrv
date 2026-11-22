"""#3982: a NIC without PCIe link attributes is "pci" only behind a PCI-to-PCI bridge.

Cloner 2 (HP 8100) reports its integrated Intel 82578DM (8086:10ef) at 0000:00:19.0, straight on root bus 00
(/sys/devices/pci0000:00/0000:00:19.0): no current_link_speed & co., capabilities PM 0x01 -> MSI 0x05 -> AF 0x13,
no PCI Express (0x10), and the kernel calls it "type 00 class 0x020000 conventional PCI endpoint". Its parent
directory is the host bridge's sysfs node, which has no config file. A real PCI card (cloner 1) sits behind a
bridge such as Intel's 00:1e.0 (type 01 class 0x060401). Without that bridge the answer is "unknown" -- and
K-NICBUS never says SHOW for "unknown".
"""
import json

from test_agent import AGENT, posix, sh


def _config(*, vendor_device, cls, header, caps=(), status=0):
    """A 256-byte PCI config space. ``caps`` = (offset, cap_id) in list order."""
    cfg = bytearray(256)
    cfg[0:4] = vendor_device
    cfg[6] = status | (0x10 if caps else 0)
    cfg[9:12] = cls
    cfg[14] = header
    if caps:
        cfg[0x34] = caps[0][0]
        for (off, cap_id), nxt in zip(caps, [c[0] for c in caps[1:]] + [0]):
            cfg[off], cfg[off + 1] = cap_id, nxt
    return bytes(cfg)


# 8086:10ef, class 0x020000 (bytes 09-0b little-endian), header type 0, PM -> MSI -> AF (no 0x10)
NIC_82578DM = _config(vendor_device=b"\x86\x80\xef\x10", cls=b"\x00\x00\x02", header=0,
                      caps=((0xC8, 0x01), (0xD0, 0x05), (0xE0, 0x13)))
# 8086:244e, class 0x060401 (subtractive-decode PCI bridge), header type 1
BRIDGE_1E = _config(vendor_device=b"\x86\x80\x4e\x24", cls=b"\x01\x04\x06", header=1)


def _nic(tmp_path, device):
    nic = tmp_path / "sys/class/net/eth0"
    nic.mkdir(parents=True)
    device.mkdir(parents=True)
    pci = tmp_path / "sys/bus/pci/devices"
    pci.mkdir(parents=True)
    (pci / device.name).symlink_to(device, target_is_directory=True)
    (nic / "device").symlink_to(device, target_is_directory=True)
    return device


def _record(tmp_path):
    return json.loads(sh(f'export SYSROOT={posix(tmp_path)!r} IFACE=eth0; '
                         f'. {posix(AGENT)}/lib/hw/sysinfo.sh; nic_bus_record'))


def test_integrated_82578dm_on_root_bus_is_not_pci(tmp_path):
    card = _nic(tmp_path, tmp_path / "sys/devices/pci0000:00/0000:00:19.0")
    (card / "config").write_bytes(NIC_82578DM)
    # the host bridge node pci0000:00 is a plain directory: no config, so no header type to read
    assert _record(tmp_path) == {"bus": "unknown"}


def test_card_behind_a_pci_to_pci_bridge_is_still_pci(tmp_path):
    bridge = tmp_path / "sys/devices/pci0000:00/0000:00:1e.0"
    card = _nic(tmp_path, bridge / "0000:0d:00.0")
    (bridge / "config").write_bytes(BRIDGE_1E)
    (card / "config").write_bytes(_config(vendor_device=b"\x86\x80\x0e\x10", cls=b"\x00\x00\x02", header=0,
                                          caps=((0xDC, 0x01),), status=0x20))
    assert _record(tmp_path) == {"bus": "pci", "capable_66mhz": True, "bridge_capable_66mhz": False}


def test_parent_that_cannot_be_read_is_unknown(tmp_path):
    # the parent is a PCI device, but its config is empty: "not read" is not "behind a PCI bridge" (principle 5)
    parent = tmp_path / "sys/devices/pci0000:00/0000:00:1e.0"
    card = _nic(tmp_path, parent / "0000:0d:00.0")
    (parent / "config").write_bytes(b"")
    (card / "config").write_bytes(NIC_82578DM)
    assert _record(tmp_path) == {"bus": "unknown"}


def test_parent_that_is_an_endpoint_is_unknown(tmp_path):
    # a header-type-0 parent is no PCI-to-PCI bridge: no positive evidence of a conventional PCI segment
    parent = tmp_path / "sys/devices/pci0000:00/0000:00:1f.0"
    card = _nic(tmp_path, parent / "0000:0d:00.0")
    (parent / "config").write_bytes(_config(vendor_device=b"\x86\x80\x00\x3b", cls=b"\x00\x01\x06", header=0x80))
    (card / "config").write_bytes(NIC_82578DM)
    assert _record(tmp_path) == {"bus": "unknown"}
