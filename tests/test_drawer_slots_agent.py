"""Agent-side connection identity and drawer mapping tests for #1485."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from test_cloner_gui import sh as proc_sh

pytestmark = requires_native(("bash", BASH))
JQ = requires_native(("bash", BASH), "jq", why="drawer_slots_refresh parses hello JSON with jq")


def _hello(fake_machine) -> dict:
    return json.loads(sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'. {posix(AGENT)}/lib/hw/disklink.sh; build_hello'))


def _pci(resolved: str) -> str:
    """disk_pci_json with `readlink -f` answering `resolved`: the parse alone.
    A real sysfs path for NVMe holds colons (0000:03:00.0), which a Windows
    test tree cannot hold -- so the link is stubbed, not the code under test."""
    return sh(f'readlink() {{ printf "%s\\n" "{resolved}"; }}; '
              f'. {posix(AGENT)}/lib/hw/disklink.sh; disk_pci_json nvme0n1')


def test_nvme_pci_identity_and_non_nvme_null():
    assert _pci("/sys/devices/pci0000:00/0000:00:1d.0/0000:03:00.0/nvme/nvme0") == ',"pci":"0000:03:00.0"', \
        "the controller right before /nvme/, not the bridge above it"
    assert _pci("/sys/devices/pci0000:00/0000:00:17.0/ata2/host1/target1/1:0:0:0") == ',"pci":null', "SATA has no pci"
    assert _pci("/sys/devices/pci0000:00/NOT-PCI/nvme/nvme0") == ',"pci":null', "junk is not an address"
    assert _pci("") == ',"pci":null', "an unreadable link is not an address"


def test_build_hello_carries_pci_for_every_disk(fake_machine):
    assert _hello(fake_machine)["disks"][0]["pci"] is None, "the fake SATA disk: the field is there, null"


def test_optical_inventory_and_exclusion_from_disks(fake_machine):
    root = fake_machine["sysroot"]
    target = root / "sys/devices/pci0000-00/ata3/host2/target2/2-0-0-0"   # no colons: Windows test trees
    target.mkdir(parents=True)
    (target / "model").write_text("  HL-DT-ST DVDRAM  \n", encoding="utf-8")
    (root / "sys/module/sr_mod").mkdir(parents=True)   # the driver is loaded: we can look
    sr = root / "sys/block/sr0"
    sr.mkdir()
    try:
        (sr / "device").symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks are not available here: {exc}")
    hello = _hello(fake_machine)
    assert hello["optical"] == [{"dev": "sr0", "model": "HL-DT-ST DVDRAM", "port": 3}]
    assert "sr0" not in {disk["dev"] for disk in hello["disks"]}


def test_optical_parse_without_symlinks(tmp_path):
    """The same entry, portable: `device` is a plain directory (no symlink
    privilege on Windows) and `readlink -f` is stubbed to the ATA path."""
    root = tmp_path / "root"
    (root / "sys/module/sr_mod").mkdir(parents=True)
    (root / "sys/block/sr0/device").mkdir(parents=True)
    (root / "sys/block/sr0/device/model").write_text("  HL-DT-ST DVDRAM  \n", encoding="utf-8")
    out = sh(f'export SYSROOT={posix(root)!r}; '
             'readlink() { printf "%s\\n" "/sys/devices/pci0000:00/0000:00:17.0/ata3/host2/target2/2:0:0:0"; }; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
             f'. {posix(AGENT)}/lib/hw/disklink.sh; optical_json')
    assert json.loads("{" + out.lstrip(",") + "}") == {
        "optical": [{"dev": "sr0", "model": "HL-DT-ST DVDRAM", "port": 3}]}


def test_no_optical_drive_is_reported_as_an_empty_list(fake_machine):
    (fake_machine["sysroot"] / "sys/module/sr_mod").mkdir(parents=True)
    assert _hello(fake_machine)["optical"] == []


def test_without_sr_mod_optical_is_null_not_empty(fake_machine):
    """No sr_mod = no /sys/block/sr* to look at: "could not look" (null), never
    "looked, none" ([]) -- principle 5. An sr0 that is somehow there is not read."""
    (fake_machine["sysroot"] / "sys/block/sr0").mkdir()
    assert _hello(fake_machine)["optical"] is None


def _refresh(run: Path, body: object) -> int:
    (run / "response.json").write_text(json.dumps(body), encoding="utf-8", newline="\n")
    out = proc_sh(
        f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/hw/slotnames.sh; '
        'drawer_slots_refresh')
    return out.returncode


@JQ
def test_drawer_slots_refresh_writes_valid_records_and_drops_bad_keys(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    assert _refresh(run, {"drawer_slots": [
        "sata:2", "bad", "nvme:0000:03:00.0", "sata:33"]}) == 0
    assert (run / "drawer_slots").read_text(encoding="utf-8").splitlines() == [
        "ok", "1|sata:2", "3|nvme:0000:03:00.0"]


@JQ
def test_missing_drawer_slots_keeps_previous_file_and_returns_one(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    previous = "ok\n1|sata:5\n"
    (run / "drawer_slots").write_text(previous, encoding="utf-8", newline="\n")
    assert _refresh(run, {"known": True}) == 1
    assert (run / "drawer_slots").read_text(encoding="utf-8") == previous
