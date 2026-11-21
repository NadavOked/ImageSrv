import json

import pytest

from test_agent import AGENT, posix, sh


@pytest.mark.parametrize("kind", ["pcie", "pci", "unknown"])
def test_active_nic_bus(tmp_path, kind):
    nic = tmp_path / "sys/class/net/eth0"
    nic.mkdir(parents=True)
    if kind != "unknown":
        bridge = tmp_path / "sys/devices/pci0000:00/0000:00:1e.0"
        card = bridge / "0000:0d:00.0"
        card.mkdir(parents=True)
        pci = tmp_path / "sys/bus/pci/devices"
        pci.mkdir(parents=True)
        (pci / card.name).symlink_to(card, target_is_directory=True)
        (nic / "device").symlink_to(card, target_is_directory=True)
        header = bytearray(64)
        header[14] = 1
        (bridge / "config").write_bytes(header)
        header[6] = 32
        (card / "config").write_bytes(header)
        if kind == "pcie":
            for name, value in (("current_link_speed", "2.5 GT/s PCIe"), ("max_link_speed", "8.0 GT/s PCIe"),
                                ("current_link_width", "1"), ("max_link_width", "4")):
                (card / name).write_text(value + "\n")
    result = json.loads(sh(f'export SYSROOT={posix(tmp_path)!r} IFACE=eth0; '
                           f'. {posix(AGENT)}/lib/hw/sysinfo.sh; nic_bus_record'))
    assert result["bus"] == kind
    if kind == "pcie":
        assert result == dict(bus=kind, current_link_speed=2.5, current_link_width=1,
                              max_link_speed=8, max_link_width=4)
    elif kind == "pci":
        assert result == dict(bus=kind, capable_66mhz=True, bridge_capable_66mhz=False)
