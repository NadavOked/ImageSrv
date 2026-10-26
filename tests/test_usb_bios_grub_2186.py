"""#2186 -- the clone stick boots legacy BIOS through GRUB (i386-pc), not syslinux.

Nadav, 25/10, at an HP in the college: the stick took over a minute, "it has to be fast".
syslinux --stupid (#2010) read one sector per BIOS call. tools/build_usb_clone.sh now writes
GRUB's boot.img into the MBR's code bytes and core.img into sectors 1..2047, both patched as
grub-bios-setup --no-rs-codes patches them, without a loop device and without root; core.img
finds IMGSRV_USB by its label and reads the grub.cfg UEFI reads (tools/usb/grub-bios.cfg).

These build a real stick from a fake kernel, initramfs, shim and GRUB, and read it back:
GRUB's MBR, the partition table sfdisk wrote still whole, core.img before partition 1, and
one kernel line for BIOS and UEFI. USB_BIOS_LOADER=syslinux still builds the old stick.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_usb_clone import REPO

SCRIPT = REPO / "tools" / "build_usb_clone.sh"
GRUB_PC = Path("/usr/lib/grub/i386-pc")
SYSLINUX_MBR = Path("/usr/lib/syslinux/mbr/mbr.bin")
KVER = "6.0.0-test2186"
P1 = 2048   # partition 1's first sector

TOOLS = (("bash", BASH), "mformat", "mcopy", "mtype", "mdir", "sfdisk", "cpio", "zstd", "git", "od")
pytestmark = requires_native(*TOOLS, "grub-mkimage", posix=True,
                             paths=(str(GRUB_PC / "boot.img"), str(GRUB_PC / "diskboot.img")))


def build(tmp: Path, **env: str) -> tuple[subprocess.CompletedProcess, Path | None]:
    """build_usb_clone.sh --initrd on a fake host (its kernel, modules, shim and GRUB)."""
    host = tmp / "host"
    (host / "modules" / KVER).mkdir(parents=True)
    (host / "boot").mkdir()
    (host / "boot" / f"vmlinuz-{KVER}").write_bytes(b"fake kernel #2186\n" * 64)
    for name in ("shim", "grub", "mok"):
        (host / name).write_bytes(f"fake {name}\n".encode() * 32)
    tree = host / "initrd-tree"
    (tree / "lib" / "modules" / KVER).mkdir(parents=True)
    initrd = host / "initrd.img"
    subprocess.run([BASH, "-c", f"cd {posix(tree)!r} && find lib | cpio -o -H newc --quiet > {posix(initrd)!r}"],
                   check=True, timeout=60, stdin=subprocess.DEVNULL)
    run_env = {k: v for k, v in os.environ.items() if not k.startswith("USB_")}
    run_env.update(IMAGECTL_MODULES_DIR=posix(host / "modules"), IMAGECTL_KERNEL_DIR=posix(host / "boot"),
                   USB_SHIM=posix(host / "shim"), USB_GRUB=posix(host / "grub"), USB_MOKMGR=posix(host / "mok"), **env)
    out = tmp / "out"
    result = subprocess.run([BASH, posix(SCRIPT), "--out", posix(out), "--initrd", posix(initrd)], env=run_env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                            stdin=subprocess.DEVNULL)
    imgs = sorted(out.glob("imagesrv-usb-*.img"))
    return result, (imgs[0] if imgs else None)


def head(img: Path, sectors: int = P1 + 1) -> bytes:
    with img.open("rb") as f:
        return f.read(sectors * 512)


def mtools(img: Path, *args: str) -> str:
    out = subprocess.run([args[0], "-i", f"{posix(img)}@@{P1 * 512}", *args[1:]], capture_output=True, text=True,
                         timeout=60, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, (args, out.stderr)
    return out.stdout


@pytest.fixture(scope="module")
def grub_stick(tmp_path_factory) -> tuple[subprocess.CompletedProcess, Path | None]:
    return build(tmp_path_factory.mktemp("grub"), USB_BIOS_LOADER="grub")


def built(stick: tuple[subprocess.CompletedProcess, Path | None]) -> Path:
    result, img = stick
    assert result.returncode == 0 and img is not None, result.stdout + result.stderr
    return img


def test_the_mbr_is_grubs_and_the_partition_table_is_whole(grub_stick):
    mbr = head(built(grub_stick), 1)
    boot = (GRUB_PC / "boot.img").read_bytes()
    assert mbr[:3] == boot[:3] and mbr[104:440] == boot[104:440], "boot.img's code is not in the MBR"
    assert struct.unpack_from("<Q", mbr, 92)[0] == 1                 # the kernel sector: core.img at LBA 1
    assert mbr[100] == 0xFF                                          # the drive the BIOS booted from (DL)
    assert mbr[102:104] == b"\x90\x90"                               # the drive check, as grub-bios-setup leaves it
    # what sfdisk wrote: the disk id, partition 1 active FAT32-LBA from 2048, partition 2 right after
    assert struct.unpack_from("<I", mbr, 440)[0] == 0x19141914
    p1, p2 = mbr[446:462], mbr[462:478]
    start1, size1 = struct.unpack_from("<II", p1, 8)
    start2, size2 = struct.unpack_from("<II", p2, 8)
    assert (p1[0], p1[4], start1) == (0x80, 0x0C, P1), p1.hex()
    assert (p2[0], p2[4], start2) == (0x00, 0x0C, P1 + size1) and size2 > 0, p2.hex()
    assert mbr[478:510] == bytes(32) and mbr[510:512] == b"\x55\xaa"


def test_core_img_sits_in_the_gap_before_partition_one(grub_stick):
    data = head(built(grub_stick))
    first = data[512:1024]
    assert first[:256] == (GRUB_PC / "diskboot.img").read_bytes()[:256], "sector 1 is not GRUB's diskboot"
    start, rest, segment = struct.unpack_from("<QHH", first, 500)    # diskboot's blocklist: the rest of core.img
    assert (start, segment) == (2, 0x820), (start, hex(segment))
    end = 2 + rest                                                   # the first sector after core.img
    assert 2 < end <= P1, end
    assert data[end * 512:P1 * 512] == bytes((P1 - end) * 512), "something after core.img, before partition 1"
    assert data[P1 * 512 + 71:P1 * 512 + 82] == b"IMGSRV_USB ", "partition 1 is not the stick's boot partition"


def test_bios_grub_reads_the_grub_cfg_uefi_reads(grub_stick):
    img = built(grub_stick)
    listing = mtools(img, "mdir", "-a", "-b", "-/", "::/")   # -a: hidden files too (ldlinux.sys is)
    assert not re.search(r"syslinux|ldlinux", listing, re.I), listing   # one BIOS loader on the stick
    for path in ("/imagesrv/vmlinuz", "/imagesrv/initrd.img", "/EFI/debian/grub.cfg"):
        assert re.search(rf"{re.escape(path)}$", listing, re.M | re.I), (path, listing)
    words = [ln.split() for ln in (REPO / "tools" / "usb" / "grub-bios.cfg").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert ["search", "--no-floppy", "--label", "IMGSRV_USB", "--set=root"] in words, words
    assert not any(w[:1] in (["linux"], ["initrd"]) for w in words), "a second kernel line, for BIOS only"
    prefix = next(w[1] for w in words if w[:1] == ["set"] and w[1].startswith("prefix=($root)/"))
    target = prefix.removeprefix("prefix=($root)") + "/grub.cfg"     # what normal mode reads
    assert target == "/EFI/debian/grub.cfg", target                 # the file the signed UEFI GRUB reads
    cfg = mtools(img, "mtype", f"::{target}")
    lines = [ln.split() for ln in cfg.splitlines()]
    assert [ln[:2] for ln in lines if ln[:1] == ["linux"]] == [["linux", "/imagesrv/vmlinuz"]], cfg
    assert [ln for ln in lines if ln[:1] == ["initrd"]] == [["initrd", "/imagesrv/initrd.img"]], cfg


def test_no_quote_marks_in_the_config_built_into_core_img():
    # QEMU, 25/10: an apostrophe in a comment of this file made GRUB read on past the comment's
    # end, and the stick stopped at the grub prompt instead of booting
    text = (REPO / "tools" / "usb" / "grub-bios.cfg").read_text(encoding="utf-8")
    assert "'" not in text and '"' not in text, [ln for ln in text.splitlines() if "'" in ln or '"' in ln]


def test_an_unknown_bios_loader_is_refused_by_name(tmp_path):
    result, img = build(tmp_path, USB_BIOS_LOADER="lilo")
    assert result.returncode == 1 and "USB_BIOS_LOADER is grub or syslinux, not 'lilo'" in result.stderr, result.stderr
    assert img is None


@requires_native("syslinux", paths=(str(SYSLINUX_MBR),))
def test_the_default_stick_boots_bios_through_fast_syslinux(tmp_path):
    img = built(build(tmp_path))   # no USB_BIOS_LOADER: the default (owner 26/10: fast)
    mbr = head(img, 1)
    assert mbr[:440] == SYSLINUX_MBR.read_bytes()[:440]
    assert struct.unpack_from("<I", mbr, 440)[0] == 0x19141914 and mbr[510:512] == b"\x55\xaa"
    listing = mtools(img, "mdir", "-a", "-b", "-/", "::/")   # -a: ldlinux.sys is hidden
    assert re.search(r"/syslinux/ldlinux\.sys$", listing, re.M | re.I), listing
