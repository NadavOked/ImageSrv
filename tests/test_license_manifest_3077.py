"""#3077 -- the USB image ships firmware-amd-graphics (#1937) with no package list and no notice.

tools/license_manifest.py is the step build_initramfs.sh runs on the packages that own packed
files: it writes the list and stops on a non-free package nobody declared. It runs here as the
build runs it (stdin rows, exit code); the build itself needs Debian and root.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "license_manifest.py"
DECLARED = REPO / "tools" / "nonfree-declared.txt"
FREE = "main/utils"
NONFREE = "non-free-firmware/kernel"


def _run(tmp_path, rows, declared=None):
    copyright_file = tmp_path / "copyright"
    copyright_file.write_text("License: GPL-2+\n\nLicense: MIT\n", encoding="utf-8")
    out = tmp_path / "packages.tsv"
    stdin = "".join("\t".join([n, v, s, str(copyright_file)]) + "\n" for n, v, s in rows)
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--declared", str(declared or DECLARED), "--out", str(out)],
        input=stdin, capture_output=True, text=True,
    )
    return r, out


def test_declared_firmware_is_listed_with_version_and_license(tmp_path):
    r, out = _run(tmp_path, [("busybox-static", "1:1.37", FREE),
                             ("firmware-amd-graphics", "20250410-2", NONFREE)])
    assert r.returncode == 0, r.stderr
    rows = [l.split("\t") for l in out.read_text(encoding="utf-8").splitlines() if not l.startswith("#")]
    assert ["firmware-amd-graphics", "20250410-2", NONFREE, "GPL-2+, MIT"] in rows


def test_an_undeclared_non_free_package_stops_the_build_and_writes_nothing(tmp_path):
    r, out = _run(tmp_path, [("busybox-static", "1:1.37", FREE),
                             ("firmware-nvidia-graphics", "20250410-2", NONFREE)])
    assert r.returncode == 1
    assert "firmware-nvidia-graphics" in r.stderr and "not in" in r.stderr
    assert not out.exists()


def test_a_package_whose_section_cannot_be_read_is_not_taken_as_free(tmp_path):
    r, out = _run(tmp_path, [("mystery", "1", "")])
    assert r.returncode == 1 and "mystery" in r.stderr and not out.exists()


def test_no_packages_at_all_is_not_a_list(tmp_path):
    r, out = _run(tmp_path, [])
    assert r.returncode == 2 and not out.exists()


def test_a_firmware_copyright_file_with_hundreds_of_ids_points_at_the_file(tmp_path):
    big = tmp_path / "big"
    big.write_text("".join(f"License: id-{i}\n\n" for i in range(40)), encoding="utf-8")
    out = tmp_path / "p.tsv"
    r = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--declared", str(DECLARED), "--out", str(out)],
        input=f"firmware-realtek\t1\t{NONFREE}\t{big}\n", capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "40 license ids, see usr/share/doc/licenses/firmware-realtek.copyright" in out.read_text(encoding="utf-8")


def test_every_declared_package_has_its_notice_and_the_usb_firmware_is_declared():
    names = [l.split("#", 1)[0].strip() for l in DECLARED.read_text(encoding="utf-8").splitlines()]
    names = [n for n in names if n]
    assert "firmware-amd-graphics" in names
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for n in names:
        assert f"`{n}`" in notices, f"{n} is declared but THIRD_PARTY_NOTICES.md has no notice for it"
    assert "binary-redist-AMD-restrictive" in notices


def test_the_builds_run_the_gate_and_refuse_an_initrd_without_the_list():
    init = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    gate = init.index('license_manifest.py" --declared')
    assert init.index("mapfile -d '' -t PACKED_FILES") < gate < init.index("# --- pack")
    assert 'usr/share/doc/licenses/packages.tsv" < ' in init[gate:gate + 400]
    assert "gate failed (#3077)\" >&2; exit 1" in init[gate:gate + 600]
    usb = (REPO / "tools" / "build_usb_clone.sh").read_text(encoding="utf-8")
    assert "usr/share/doc/licenses/packages.tsv" in usb and "-packages.tsv" in usb
    assert "has no package list" in usb and "die " in usb[usb.index("has no package list") - 300:]
    iso = (REPO / "tools" / "iso" / "build-iso.sh").read_text(encoding="utf-8")
    assert "imagesrv-live-packages.tsv" in iso and "usr/share/doc/licenses/packages.tsv" in iso
