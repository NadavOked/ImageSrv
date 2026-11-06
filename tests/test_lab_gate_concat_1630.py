"""#1630 -- the lab gate reads every concatenated archive, and the station payload passes it.

Two holes left after #2087 (the Issue's items 3 and 4):

* **Concatenated archives.** ``cpio -t`` stops at the first ``TRAILER!!!``; the
  kernel (init/initramfs.c) does not -- after NUL padding it unpacks the next
  header too. An overlay appended to the image (a second archive, in the same
  gzip member, in a second gzip member, or in a second zstd frame) was not in the
  listing, so dropbear in it passed the gate. ``tools/iso/initrd-list.py`` now
  lists to the end of the stream, and anything it cannot read is a failed
  inspection -- never "clean".
* **The station payload.** ``tools/boot-payload-build.sh`` (firstboot, the update
  button, the ISO's prebuilt payload) replaced ``/srv/imagesrv/boot`` without the
  gate. It now runs it on every ``.new`` before anything is replaced; lab access
  without ``--lab`` stops the build, leaves the previous payload byte for byte and
  no ``.new`` behind.

The archives here are written in Python (newc, the format the kernel reads), so
the gate runs without cpio. GNU-cpio-made archives go through the same lister in
``test_iso_lab_gate_1412.py`` and ``test_iso_lab_overlay_1412.py``.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from native import requires_native
from test_boot_payload_1230 import OLD_KVER, _host, _host_env
from test_installer_1131 import BASH, REPO, posix, run_bash

ISO_DIR = REPO / "tools" / "iso"
LAB_GATE = ISO_DIR / "initrd-lab-gate.sh"
LISTER = ISO_DIR / "initrd-list.py"
CLEAN = (".", "init", "lib/modules/6.12.0/modules.dep")
LAB = ("usr/sbin/dropbear",)

pytestmark = requires_native(("bash", BASH), "gzip", "python3", why="the gate is bash + gzip + python3")


def entry(name: bytes, data: bytes = b"x\n", namesize: int | None = None) -> bytes:
    """One newc entry; ``namesize`` larger than the name hides bytes after its NUL."""
    raw = name + b"\0"
    if namesize is not None:
        raw = raw.ljust(namesize, b"z")
    mode = 0o040755 if name == b"." else 0o100644
    fields = (0, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(raw), 0)
    blob = b"070701" + b"".join(b"%08X" % f for f in fields) + raw
    blob += b"\0" * (-len(blob) % 4)
    return blob + data + b"\0" * (-len(data) % 4)


def archive(*names: str, extra: bytes = b"") -> bytes:
    """An archive as cpio -o writes it: the entries, TRAILER!!!, NULs to 512."""
    body = b"".join(entry(n.encode()) for n in names) + extra + entry(b"TRAILER!!!", b"")
    return body + b"\0" * (-len(body) % 512)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def gate(img: Path, *flag: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, posix(LAB_GATE), posix(img), *flag], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


def assert_refused(img: Path) -> None:
    refused = gate(img)
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    assert "--lab" in refused.stderr, refused.stderr
    allowed = gate(img, "--lab")
    assert allowed.returncode == 0 and allowed.stdout.strip() == "true", (allowed.stdout, allowed.stderr)


def assert_not_inspected(img: Path) -> None:
    """A stream the gate could not read to its end is neither clean nor lab: it stops."""
    for flag in ((), ("--lab",)):
        run = gate(img, *flag)
        assert run.returncode != 0, (flag, run.stdout, run.stderr)
        assert run.stdout.strip() not in ("false", "true"), (flag, run.stdout)
        assert "cannot inspect" in run.stderr, run.stderr


# --- item 3: every archive in the stream ------------------------------------------------

LAYOUTS = {
    # the overlay archive after the first one's TRAILER, inside one gzip member
    "one_member": lambda: gz(archive(*CLEAN) + archive(*LAB)),
    # a second gzip member appended to the file -- `cat initrd overlay.gz`
    "two_members": lambda: gz(archive(*CLEAN)) + gz(archive(*LAB)),
}


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_lab_access_in_an_appended_archive_is_refused(tmp_path, layout):
    assert_refused(write(tmp_path, "initrd.img", LAYOUTS[layout]()))


@requires_native("zstd")
def test_lab_access_in_an_appended_zstd_frame_is_refused(tmp_path):
    def zst(data: bytes) -> bytes:
        return subprocess.run(["zstd", "-q", "-c"], input=data, capture_output=True, check=True).stdout
    assert_refused(write(tmp_path, "initrd.img", zst(archive(*CLEAN)) + zst(archive(*LAB))))


def test_two_clean_archives_pass_unmarked(tmp_path):
    """Guard: reading on past the first TRAILER is not by itself a refusal."""
    img = write(tmp_path, "initrd.img", gz(archive(*CLEAN) + archive("etc/imagesrv/version")))
    clean = gate(img)
    assert clean.returncode == 0 and clean.stdout.strip() == "false", (clean.stdout, clean.stderr)


def test_the_name_is_read_up_to_its_nul_as_the_kernel_reads_it(tmp_path):
    """namesize may run past the NUL; the kernel creates the path before it."""
    hidden = entry(b"usr/sbin/dropbear", namesize=40)
    assert_refused(write(tmp_path, "initrd.img", gz(archive(*CLEAN, extra=hidden))))


def test_junk_after_the_trailer_is_a_failed_inspection(tmp_path):
    assert_not_inspected(write(tmp_path, "initrd.img", gz(archive(*CLEAN) + b"junk" * 64)))


def test_a_truncated_second_archive_is_a_failed_inspection(tmp_path):
    second = archive(*LAB)
    assert_not_inspected(write(tmp_path, "initrd.img", gz(archive(*CLEAN) + second[:200])))


def test_the_lister_names_every_entry_of_every_archive_and_nothing_else():
    run = subprocess.run([sys.executable, str(LISTER)], input=archive(*CLEAN) + archive(*LAB),
                         capture_output=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert run.stdout.decode().splitlines() == [*CLEAN, *LAB]


@pytest.mark.parametrize("stream", [b"", b"\0" * 512])
def test_a_stream_with_no_entry_is_not_an_empty_clean_listing(stream):
    run = subprocess.run([sys.executable, str(LISTER)], input=stream, capture_output=True, timeout=60)
    assert run.returncode == 1 and not run.stdout, (run.stdout, run.stderr)
    assert b"no cpio entries" in run.stderr, run.stderr


# --- item 4: boot-payload-build.sh runs the gate before the swap --------------------------

STUB_BUILD = r"""#!/usr/bin/env bash
out=""
for a in "$@"; do case "$prev" in --output) out="$a" ;; esac; prev="$a"; done
cp "$STUB_IMAGES/$(basename "$out" .new)" "$out"
"""
OUTPUTS = ("initrd.img", "initrd.img.gui")


def _payload(tmp_path: Path, images: dict[str, bytes], *flags: str, gate_installed: bool = True):
    """The real boot-payload-build.sh, gate, lister and initramfs-format.sh; a stub
    build_initramfs.sh that "builds" the given bytes for each output."""
    host = _host(tmp_path)
    tree = tmp_path / "app"
    (tree / "tools" / "iso").mkdir(parents=True)
    for rel in ("tools/boot-payload-build.sh", "tools/initramfs-format.sh"):
        shutil.copy(REPO / rel, tree / rel)
    if gate_installed:
        # what the repo has: a gate that cannot run its lister fails closed, and the
        # clean-payload guard below goes red -- so a missing file is never silent
        for rel in ("tools/iso/initrd-lab-gate.sh", "tools/iso/initrd-list.py"):
            if (REPO / rel).exists():
                shutil.copy(REPO / rel, tree / rel)
    (tree / "tools" / "build_initramfs.sh").write_text(STUB_BUILD, encoding="utf-8", newline="\n")
    stub_images = tmp_path / "images"
    stub_images.mkdir()
    for name, data in images.items():
        (stub_images / name).write_bytes(data)
    boot = host / "boot"
    for name in (*OUTPUTS, "vmlinuz"):
        (boot / name).write_bytes(f"old {name}\n".encode())
    flag_file = tmp_path / "initrd.flags"
    flag_file.write_text(f"--skip-apt --output {posix(boot)}/initrd.img\n"
                         f"--skip-apt --with-gui --output {posix(boot)}/initrd.img.gui\n",
                         encoding="utf-8", newline="\n")
    proc = run_bash(
        f'bash "{posix(tree / "tools" / "boot-payload-build.sh")}" --http-root "{posix(boot)}"'
        f' --flags-file "{posix(flag_file)}" --manifest "{posix(host / "etc" / "iso-release.json")}"'
        f' --build-log "{posix(tmp_path / "build.log")}" {" ".join(flags)}',
        tmp_path, tmp_path, env={**_host_env(host), "STUB_IMAGES": posix(stub_images)})
    return proc, boot


def _untouched(boot: Path) -> None:
    for name in (*OUTPUTS, "vmlinuz"):
        assert (boot / name).read_bytes() == f"old {name}\n".encode(), name
    assert not sorted(p.name for p in boot.glob("*.new")), "a .new is left in the HTTP root"


LAB_GUI = gz(archive(*CLEAN) + archive(*LAB))   # an overlay appended to the GUI initrd
CLEAN_IMG = gz(archive(*CLEAN))


def test_a_lab_initrd_does_not_replace_the_station_payload_without_lab(tmp_path):
    proc, boot = _payload(tmp_path, {"initrd.img": CLEAN_IMG, "initrd.img.gui": LAB_GUI})
    assert proc.returncode != 0, proc.stderr
    last = proc.stderr.strip().splitlines()[-1]          # what firstboot shows on the screen
    assert "initrd.img.gui" in last and "without --lab" in last, proc.stderr
    _untouched(boot)


def test_with_lab_the_lab_initrd_is_installed(tmp_path):
    proc, boot = _payload(tmp_path, {"initrd.img": CLEAN_IMG, "initrd.img.gui": LAB_GUI}, "--lab")
    assert proc.returncode == 0, proc.stderr
    assert (boot / "initrd.img.gui").read_bytes() == LAB_GUI
    assert (boot / "vmlinuz").read_text(encoding="utf-8") == f"kernel {OLD_KVER}\n"


def test_a_clean_payload_is_installed_without_lab(tmp_path):
    """Guard: the gate in the payload build does not refuse a product initrd."""
    proc, boot = _payload(tmp_path, {"initrd.img": CLEAN_IMG, "initrd.img.gui": CLEAN_IMG})
    assert proc.returncode == 0, proc.stderr
    assert all((boot / name).read_bytes() == CLEAN_IMG for name in OUTPUTS)


def test_an_initrd_the_gate_cannot_read_does_not_replace_the_payload(tmp_path):
    proc, boot = _payload(tmp_path, {"initrd.img": CLEAN_IMG, "initrd.img.gui": b"not an initramfs\n"})
    assert proc.returncode != 0, proc.stderr
    _untouched(boot)


def test_a_gate_that_is_missing_stops_the_build(tmp_path):
    """Principle 5: a gate that did not run is not a gate that passed."""
    proc, boot = _payload(tmp_path, {"initrd.img": CLEAN_IMG, "initrd.img.gui": CLEAN_IMG},
                          gate_installed=False)
    assert proc.returncode != 0, proc.stderr
    _untouched(boot)


def test_build_iso_hands_its_lab_flag_to_the_prebuilt_payload():
    """A --lab ISO built with an overlay builds its prebuilt payload with it; a product
    ISO's payload goes through the gate without --lab (static: the ISO build needs root)."""
    build = (ISO_DIR / "build-iso.sh").read_text(encoding="utf-8")
    call = build.split('bash "$REPO/tools/boot-payload-build.sh"', 1)[1].split("|| die", 1)[0]
    assert '"${lab_flag[@]}"' in call, call
