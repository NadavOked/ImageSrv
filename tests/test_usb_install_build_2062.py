"""#2062 -- the stick's third partition: tools/build_usb_clone.sh --server-iso.

Nadav 12/11: the packages are on the stick, the install needs no internet. The partition
is the server's install ISO, byte for byte (label IMAGESRV_INSTALL, MBR type 83), written
after the two partitions every boot reads -- which do not move.

These build real sticks from a fake kernel, initramfs, shim and GRUB and a small real ISO
(xorriso), and read them back: the partition table, the bytes of partition 3, the manifest.
And the build refuses every combination the button could not install from (principle 5):
an ISO that is not ours, a lab ISO, another release's ISO, an initramfs without the installer.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_usb_clone import REPO

SCRIPT = REPO / "tools" / "build_usb_clone.sh"
KVER = "6.0.0-test2062"
INSTALLER_FILES = ("usr/lib/imagesrv/installer/imagesrv-installer", "usr/lib/imagesrv/installer/imagesrv-install",
                   "usr/lib/imagesrv/installer/gui-bridge.sh", "usr/lib/imagesrv/usbinstall.sh",
                   "usr/bin/imagesrv-station-gui")

pytestmark = requires_native(("bash", BASH), "mformat", "mcopy", "mmd", "mdir", "mtype", "sfdisk", "cpio", "zstd",
                             "git", "od", "xorriso", "python3", posix=True)


def stick_version() -> str:
    """The version build_usb_clone.sh gives the stick -- the ISO must carry the same one."""
    out = subprocess.run(["git", "-C", str(REPO), "describe", "--tags", "--always", "--dirty"], capture_output=True,
                         text=True, timeout=60, stdin=subprocess.DEVNULL, check=True)
    return out.stdout.strip()


def make_iso(tmp: Path, *, label: str = "IMAGESRV_INSTALL", manifest: dict | None | str = "default",
             name: str = "imagesrv-test-amd64.iso") -> Path:
    tree = tmp / "iso-tree"; tree.mkdir(exist_ok=True)
    (tree / "pool").mkdir(exist_ok=True)
    (tree / "pool" / "some.deb").write_bytes(os.urandom(300_000))   # not a whole MiB: the partition is rounded up
    if manifest == "default":
        manifest = {"imagesrv_tag": stick_version(), "lab_overlay": False}
    if manifest is not None:
        (tree / "imagesrv-iso.json").write_text(json.dumps(manifest), encoding="utf-8")
    iso = tmp / name
    made = subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-V", label, "-o", str(iso), str(tree)],
                          capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert made.returncode == 0 and iso.stat().st_size > 0, made.stderr
    return iso


def build(tmp: Path, *args: str, installer: bool = True) -> tuple[subprocess.CompletedProcess, Path | None]:
    """build_usb_clone.sh --initrd --no-bios on a fake host (its kernel, modules, shim and GRUB)."""
    host = tmp / "host"
    (host / "modules" / KVER).mkdir(parents=True, exist_ok=True)
    (host / "boot").mkdir(exist_ok=True)
    (host / "boot" / f"vmlinuz-{KVER}").write_bytes(b"fake kernel #2062\n" * 64)
    for name in ("shim", "grub", "mok"):
        (host / name).write_bytes(f"fake {name}\n".encode() * 32)
    tree = host / ("initrd-tree" if installer else "initrd-tree-plain")
    (tree / "lib" / "modules" / KVER).mkdir(parents=True, exist_ok=True)
    if installer:
        for rel in INSTALLER_FILES:
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text("#!/bin/sh\n", newline="\n")
    initrd = host / f"{tree.name}.img"
    subprocess.run([BASH, "-o", "pipefail", "-c",
                    f"cd {posix(tree)!r} && find . -mindepth 1 | sed 's|^\\./||' | cpio -o -H newc --quiet | zstd -q -f -o {posix(initrd)!r}"],
                   check=True, timeout=60, stdin=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items() if not k.startswith("USB_")}
    env.update(IMAGESRV_MODULES_DIR=posix(host / "modules"), IMAGESRV_KERNEL_DIR=posix(host / "boot"),
               USB_SHIM=posix(host / "shim"), USB_GRUB=posix(host / "grub"), USB_MOKMGR=posix(host / "mok"))
    out = tmp / f"out{len(list(tmp.glob('out*')))}"
    result = subprocess.run([BASH, posix(SCRIPT), "--out", posix(out), "--initrd", posix(initrd), "--no-bios", *args],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                            stdin=subprocess.DEVNULL)
    imgs = sorted(out.glob("imagesrv-usb-*.img"))
    return result, (imgs[0] if imgs else None)


def partitions(img: Path) -> list[tuple[int, int, int, int]]:
    """(active, type, first sector, sectors) of the four MBR entries."""
    mbr = img.read_bytes()[:512]
    assert mbr[510:512] == b"\x55\xaa"
    return [(mbr[446 + 16 * i], mbr[446 + 16 * i + 4], *struct.unpack_from("<II", mbr, 446 + 16 * i + 8))
            for i in range(4)]


@pytest.fixture(scope="module")
def sticks(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("stick2062")
    iso = make_iso(tmp)
    plain = build(tmp)
    both = build(tmp, "--server-iso", posix(iso))
    return iso, plain, both


def built(pair: tuple[subprocess.CompletedProcess, Path | None]) -> Path:
    result, img = pair
    assert result.returncode == 0 and img is not None, result.stdout + result.stderr
    return img


def test_the_third_partition_is_the_iso_byte_for_byte(sticks):
    iso, _plain, both = sticks
    img = built(both)
    p1, p2, p3, p4 = partitions(img)
    assert p3[:2] == (0x00, 0x83) and p3[2] == p2[2] + p2[3], (p2, p3)   # right after the data partition, type Linux
    assert p4 == (0, 0, 0, 0)
    size = iso.stat().st_size
    assert p3[3] * 512 >= size and p3[3] % 2048 == 0 and p3[3] * 512 - size < 1 << 20, (p3, size)   # rounded up to a MiB
    with img.open("rb") as f:
        f.seek(p3[2] * 512)
        assert hashlib.sha256(f.read(size)).hexdigest() == hashlib.sha256(iso.read_bytes()).hexdigest()
        assert f.read(p3[3] * 512 - size) == bytes(p3[3] * 512 - size)   # the padding is zeros
        f.seek(p3[2] * 512 + 32768 + 40)                              # the label where blkid reads it
        assert f.read(32).decode().strip() == "IMAGESRV_INSTALL"
    assert img.stat().st_size == (p3[2] + p3[3] + 2048) * 512


def test_the_two_partitions_every_boot_reads_do_not_move(sticks):
    _iso, plain, both = sticks
    a, b = partitions(built(plain)), partitions(built(both))
    assert a[:2] == b[:2], (a, b)                                         # same start, size, type, active flag
    assert a[0][:3] == (0x80, 0x0C, 2048) and a[1][1] == 0x0C
    assert a[2] == (0, 0, 0, 0), "a stick built without --server-iso has a third partition"


def test_the_manifest_names_the_iso_or_says_there_is_none(sticks):
    iso, plain, both = sticks
    meta = json.loads((built(both).parent / "imagesrv-usb.json").read_text(encoding="utf-8"))
    assert meta["install"] == {"iso": iso.name, "iso_sha256": hashlib.sha256(iso.read_bytes()).hexdigest(),
                               "tag": stick_version(), "label": "IMAGESRV_INSTALL", "bytes": iso.stat().st_size,
                               "release": True}
    assert meta["size"] == built(both).stat().st_size
    assert json.loads((built(plain).parent / "imagesrv-usb.json").read_text(encoding="utf-8"))["install"] is None
    inner = subprocess.run(["mtype", "-i", f"{posix(built(both))}@@{2048 * 512}", "::/imagesrv-usb.json"],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert json.loads(inner.stdout)["install"]["tag"] == stick_version(), inner.stderr   # on the stick itself too


def refused(tmp_path: Path, why: str, *args: str, installer: bool = True) -> None:
    result, img = build(tmp_path, *args, installer=installer)
    assert result.returncode != 0 and img is None, result.stdout + result.stderr
    assert why in result.stderr, result.stderr
    assert not list(tmp_path.rglob("imagesrv-usb-*.img")), "an image was left behind by a refused build"


def test_an_iso_that_is_not_ours_is_refused(tmp_path):
    refused(tmp_path, "not IMAGESRV_INSTALL", "--server-iso", posix(make_iso(tmp_path, label="Debian 13.7.0 amd64 n")))


def test_an_iso_without_the_manifest_is_refused(tmp_path):
    refused(tmp_path, "has no imagesrv-iso.json", "--server-iso", posix(make_iso(tmp_path, manifest=None)))


@pytest.mark.parametrize("lab", [True, None, "false"])
def test_a_lab_iso_or_one_that_does_not_say_is_refused(tmp_path, lab):
    manifest = {"imagesrv_tag": stick_version()} | ({} if lab is None else {"lab_overlay": lab})
    refused(tmp_path, "is a lab ISO", "--server-iso", posix(make_iso(tmp_path, manifest=manifest)))


def test_another_releases_iso_is_refused(tmp_path):
    iso = make_iso(tmp_path, manifest={"imagesrv_tag": "v0.0.1-other", "lab_overlay": False})
    refused(tmp_path, f"the ISO is v0.0.1-other and the stick is {stick_version()}", "--server-iso", posix(iso))


def test_lab_iso_builds_another_releases_iso_and_says_it_is_not_a_release(tmp_path):
    iso = make_iso(tmp_path, manifest={"imagesrv_tag": "v0.0.1-other", "lab_overlay": False})
    result, img = build(tmp_path, "--server-iso", posix(iso), "--lab-iso")
    assert result.returncode == 0 and img is not None, result.stdout + result.stderr
    assert "WARNING: --lab-iso" in result.stderr
    install = json.loads((img.parent / "imagesrv-usb.json").read_text(encoding="utf-8"))["install"]
    assert (install["tag"], install["release"]) == ("v0.0.1-other", False)


def test_an_initramfs_without_the_installer_is_refused(tmp_path):
    refused(tmp_path, "must be built with --installer --with-gui", "--server-iso", posix(make_iso(tmp_path)),
            installer=False)


@pytest.mark.parametrize("args,why", [
    (["--survey"], "--survey takes no --server-iso"),
    (["--lab-iso", "--publish", "DATA"], "--lab-iso is never published"),
])
def test_combinations_that_make_no_stick(tmp_path, args, why):
    args = [posix(tmp_path / "data") if a == "DATA" else a for a in args]
    refused(tmp_path, why, "--server-iso", posix(make_iso(tmp_path)), *args)


def test_lab_iso_alone_is_refused(tmp_path):
    refused(tmp_path, "--lab-iso needs --server-iso", "--lab-iso")


def test_the_initramfs_is_built_with_the_installer_only_for_a_stick_that_carries_the_iso():
    text = SCRIPT.read_text(encoding="utf-8")
    before, start, branch = text.partition('if [[ -n "$SERVER_ISO" ]]; then')
    assert start, "build_usb_clone.sh has no --server-iso branch"
    assert "INSTALLER=(--installer)" in branch.partition("\nfi\n")[0]
    assert 'INSTALLER=()' in before                                        # empty unless the branch fills it
    assert '"${SKIP_APT[@]}" "${INSTALLER[@]}" --with-gui --radeon-legacy-firmware' in text
