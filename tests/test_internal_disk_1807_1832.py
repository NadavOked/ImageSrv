"""#1807 + #1832 -- a USB enclosure or an SD card is never "the internal disk".

``/sys/block/<n>/removable`` is a bit of the MEDIA, not of the bus (the kernel's
sysfs-block ABI): many USB-SATA enclosures say 0, and so does an ``mmcblk`` of an
sdhci reader. Four places took ``removable=0`` for "internal":

* ``pick_internal_disk`` (the restore to this disk -- a destructive write): the
  FIRST such disk in /sys/block order, and ``mmcblk0`` sorts before ``nvme0n1``;
* ``list_drawers`` (a cloner's round): an enclosure became a drawer -- a target;
* the hello's ``removable`` field, which ``direct-open`` (the source of a direct
  send, ``.[0]``), ``gui_capture``, guistate.sh, the native GUI and the server
  all read as "internal".

Now one test, ``disk_internal`` (sysinfo.sh): removable 0 AND a device path that
reads AND is on no USB/MMC bus. The hello says ``removable:true`` for anything
else, so every reader of the field gets it at once; ``pick_internal_disk`` and
``direct-open`` refuse when more than one disk is left -- never a silent pick.

The device paths are symlinks in a real sysfs. Windows cannot make them without
a privilege, so a ``readlink`` shim reads them from a table; the last test makes
real symlinks where it can.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

PCI = "/sys/devices/pci0000:00"
#: where each kind of disk's `device` link points on a real kernel
PATHS = {
    "nvme": f"{PCI}/0000:00:1d.0/0000:3d:00.0/nvme/nvme0",
    "sata": f"{PCI}/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0",
    "usb": f"{PCI}/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0",
    "mmc": f"{PCI}/0000:00:1e.6/mmc_host/mmc0/mmc0:aaaa",
}

#: readlink -f from the box's table ($LINKS: path|target); a path not in it that
#: exists is itself, one that does not exist fails -- as busybox's realpath does.
READLINK = r'''#!/bin/sh
[ "$1" = -f ] || exit 2
t=$(awk -F'|' -v p="$2" '$1 == p { print $2; exit }' "$LINKS")
[ -n "$t" ] && { echo "$t"; exit 0; }
[ -e "$2" ] && { echo "$2"; exit 0; }
exit 1
'''


def _machine(tmp_path: Path, disks: dict[str, tuple[str, str | None]]) -> Path:
    """disks[name] = (removable, kind); kind None = no `device` in sysfs."""
    box = tmp_path / "box"
    stubs = box / "stubs"
    stubs.mkdir(parents=True)
    (stubs / "readlink").write_text(READLINK, encoding="utf-8", newline="\n")
    links = []
    for name, (rm, kind) in disks.items():
        blk = box / "sys" / "block" / name
        blk.mkdir(parents=True)
        (blk / "removable").write_text(rm + "\n", newline="\n")
        (blk / "size").write_text("1000\n", newline="\n")
        if kind is not None:
            (blk / "device").mkdir()
            links.append(f"{posix(blk / 'device')}|{PATHS[kind]}")
    (box / "links").write_text("\n".join(links) + "\n", encoding="utf-8", newline="\n")
    (box / "dev").mkdir()
    return box


def _sh(box: Path, call: str, libs: str = "") -> str:
    return sh(f'chmod 0755 {posix(box)}/stubs/readlink; export PATH="$(cd {posix(box)!r}/stubs && pwd):$PATH" '
              f'LINKS={posix(box / "links")!r} SYSROOT={posix(box)!r} DEVROOT={posix(box / "dev")!r} '
              f'RUN_DIR={posix(box)!r} LOG_FILE={posix(box)}/log; log() {{ :; }}; '
              f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; {libs}{call}')


PICK = f'. {posix(AGENT)}/lib/restore/single_restore.sh; '


def _pick(box: Path) -> str:
    return _sh(box, 'pick_internal_disk; echo "rc=$?"', PICK).strip()


# --- pick_internal_disk (#1807) ------------------------------------------------------


def test_an_sd_card_that_sorts_first_is_not_the_restore_target(tmp_path):
    box = _machine(tmp_path, {"mmcblk0": ("0", "mmc"), "nvme0n1": ("0", "nvme")})
    assert _pick(box).splitlines() == ["nvme0n1", "rc=0"]


def test_a_usb_enclosure_that_says_removable_0_is_not_the_restore_target(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "usb"), "sdb": ("0", "sata")})
    assert _pick(box).splitlines() == ["sdb", "rc=0"]


def test_two_internal_disks_are_a_refusal_never_a_guess(tmp_path):
    box = _machine(tmp_path, {"nvme0n1": ("0", "nvme"), "sda": ("0", "sata")})
    assert _pick(box).splitlines()[-1] == "rc=2"


def test_no_internal_disk_is_a_refusal(tmp_path):
    box = _machine(tmp_path, {"sda": ("0", "usb"), "sdb": ("1", "sata")})
    assert _pick(box).splitlines()[-1] == "rc=1"


def test_a_disk_whose_device_path_does_not_read_is_not_internal(tmp_path):
    """Principle 5: "could not read where it hangs" is not "internal"."""
    box = _machine(tmp_path, {"sda": ("0", None), "nvme0n1": ("0", "nvme")})
    assert _pick(box).splitlines() == ["nvme0n1", "rc=0"]


def test_the_one_internal_disk_is_still_picked(tmp_path):
    """The guard: the classroom's one SATA disk, beside a USB stick (removable 1)."""
    box = _machine(tmp_path, {"sda": ("0", "sata"), "sdb": ("1", "usb")})
    assert _pick(box).splitlines() == ["sda", "rc=0"]


# --- list_drawers (#1832) ------------------------------------------------------------


def test_a_usb_enclosure_or_an_sd_card_is_never_a_drawer(tmp_path):
    box = _machine(tmp_path, {"mmcblk0": ("0", "mmc"), "sda": ("0", "sata"), "sdb": ("0", "usb"),
                              "sdc": ("0", "sata")})
    out = _sh(box, "list_drawers", f'. {posix(AGENT)}/lib/restore/drawers.sh; ').split()
    assert out == ["sda", "sdc"], out


# --- the hello's removable field: direct-open, gui_capture, guistate, GUI, server (#1832)


def _entry(box: Path, name: str) -> dict:
    out = _sh(box, f"build_disk_entry {name}")
    return json.loads(out.strip().splitlines()[-1])


@pytest.mark.parametrize(("kind", "removable"), [("usb", True), ("mmc", True), ("sata", False), ("nvme", False),
                                                 (None, True)], ids=["usb", "mmc", "sata", "nvme", "no-device"])
def test_the_hello_says_removable_for_anything_not_internal(tmp_path, kind, removable):
    box = _machine(tmp_path, {"sda": ("0", kind)})
    assert _entry(box, "sda")["removable"] is removable


# --- direct-open: one source, never .[0] of several (#1832) ---------------------------


def _jq_source(tmp_path: Path, disks: list[dict]) -> str:
    """The guibridge.sh filter itself, read from the file, on a fresh inventory."""
    src = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if "_gd=$(jq -er" in l)
    flt = line[line.index("'") + 1:line.rindex("'")]
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"disks": disks}), encoding="utf-8")
    return sh(f'jq -er {flt!r} {posix(inv)!r}; echo "rc=$?"').strip()


jq_needed = pytest.mark.skipif(not __import__("shutil").which("jq"), reason="jq -- runs on the Testrunner")


@jq_needed
def test_direct_open_takes_the_one_internal_disk(tmp_path):
    out = _jq_source(tmp_path, [{"dev": "sda", "removable": True}, {"dev": "nvme0n1", "removable": False}])
    assert out.splitlines() == ["nvme0n1", "rc=0"]


@jq_needed
def test_direct_open_refuses_when_two_disks_could_be_the_source(tmp_path):
    out = _jq_source(tmp_path, [{"dev": "sda", "removable": False}, {"dev": "nvme0n1", "removable": False}])
    assert out.splitlines()[-1] != "rc=0", out


# --- real symlinks, where the system can make them -----------------------------------


def _can_symlink(tmp_path: Path) -> bool:
    try:
        os.symlink(tmp_path, tmp_path / "probe-link", target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_the_real_readlink_on_real_symlinks(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("no symlinks here (Windows without the privilege) -- runs on the Testrunner")
    box = tmp_path / "box"
    for name, kind in (("mmcblk0", "mmc"), ("nvme0n1", "nvme")):
        blk = box / "sys" / "block" / name
        blk.mkdir(parents=True)
        (blk / "removable").write_text("0\n")
        target = box / PATHS[kind].lstrip("/")
        target.mkdir(parents=True)
        os.symlink(target, blk / "device", target_is_directory=True)
    out = sh(f'export SYSROOT={posix(box)!r}; log() {{ :; }}; . {posix(AGENT)}/lib/core/common.sh; '
             f'. {posix(AGENT)}/lib/hw/sysinfo.sh; {PICK}pick_internal_disk; echo "rc=$?"')
    assert out.strip().splitlines() == ["nvme0n1", "rc=0"]
