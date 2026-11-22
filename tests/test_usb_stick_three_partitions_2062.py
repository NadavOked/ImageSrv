"""#2062 -- the server still knows the clone stick when it carries the install partition.

The combined stick has a third partition (IMAGESRV_INSTALL, iso9660). The server finds the stick by
positive evidence from one ``lsblk`` tree (#1920: USB bus, the FAT IMGSRV_DATA partition, the
IMGSRV_USB sibling, nothing mounted) -- and a new sibling must not turn the stick into "not ours",
nor become the partition the sync writes to.
"""

from __future__ import annotations

from server.usbstick import device


def tree(*, third: dict | None) -> dict:
    parts = [
        {"name": "sdb1", "pkname": "sdb", "path": "/dev/sdb1", "type": "part", "size": 155189248,
         "fstype": "vfat", "label": "IMGSRV_USB", "uuid": "1914-0001", "ro": False, "mountpoints": [None]},
        {"name": "sdb2", "pkname": "sdb", "path": "/dev/sdb2", "type": "part", "size": 33554432,
         "fstype": "vfat", "label": "IMGSRV_DATA", "uuid": "1914-0002", "ro": False, "mountpoints": [None]},
    ]
    if third:
        parts.append({"name": "sdb3", "pkname": "sdb", "path": "/dev/sdb3", "type": "part", "size": 1582301184,
                      "ro": False, "mountpoints": [None], **third})
    return {"blockdevices": [
        {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 500107862016, "tran": "sata", "ro": False,
         "model": "Server disk", "serial": "S1", "mountpoints": [None]},
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "size": 1773142016, "tran": "usb", "ro": False, "rm": True,
         "vendor": "Generic", "model": "Flash Disk", "serial": "AA00000000001234", "mountpoints": [None],
         "children": parts}]}


INSTALL = {"fstype": "iso9660", "label": "IMAGESRV_INSTALL", "uuid": "2026-11-12-09-55-00-00"}


def test_the_stick_with_the_install_partition_is_the_same_stick_to_the_server():
    plain = device.classify(tree(third=None))
    both = device.classify(tree(third=INSTALL))
    assert plain.state == both.state == device.CONNECTED, (plain.reason, both.reason)
    assert both.stick.device_id == plain.stick.device_id == "usb-AA00000000001234"
    assert both.stick.partition == "/dev/sdb2" and both.stick.fstype == "vfat"   # the sync's partition is the data one
    assert both.notes == []


def test_the_install_partition_mounted_is_said_like_any_mounted_sibling():
    """Something on the server has the stick's ISO mounted: the stick is in use -- left alone, by name."""
    scan = device.classify(tree(third={**INSTALL, "mountpoints": ["/mnt/iso"]}))
    assert scan.state == device.UNSAFE and "/mnt/iso" in scan.reason, (scan.state, scan.reason)
