"""Static distribution-license checks for issue #1707.

The real initramfs and ISO builds require Debian.  These checks pin the order
and failure semantics here so a Windows development run still catches a
license pass that moves before the final payload or becomes best-effort.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INITRAMFS = ROOT / "tools" / "build_initramfs.sh"
BUILD_ISO = ROOT / "tools" / "iso" / "build-iso.sh"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"


def test_initramfs_copies_licenses_for_the_final_packed_file_list() -> None:
    text = INITRAMFS.read_text(encoding="utf-8")
    overlay = text.index("# --- site/lab overlay")
    final_files = text.index("mapfile -d '' -t PACKED_FILES")
    pack = text.index("# --- pack", final_files)

    assert overlay < final_files < pack
    assert text.index('cp -a "/lib/firmware/$fw/."') < final_files
    assert text.index('cp -L "$src" "$ROOT$fontdir/"') < final_files
    assert text.index('cp -a --no-preserve=ownership "$EXTRA_OVERLAY/."') < final_files
    block = text[final_files:pack]
    assert 'find "$ROOT" -type f -print0' in block
    assert 'for _packed in "${PACKED_FILES[@]}"' in block
    assert 'dpkg-query -S "$_host_file"' in block
    assert '"$ROOT/usr/share/doc/licenses/$_pkg.copyright"' in block


def test_initramfs_fails_and_names_package_when_copyright_is_missing() -> None:
    text = INITRAMFS.read_text(encoding="utf-8")
    block = text[text.index("# --- third-party licenses"):text.index("# --- pack")]

    assert '[ -f "$_copyright" ] || {' in block
    assert 'package $_pkg' in block
    assert 'has no $_copyright' in block
    assert "exit 1" in block


def test_third_party_notices_cover_main_gpl_components() -> None:
    assert NOTICES.is_file()
    notices = NOTICES.read_text(encoding="utf-8").lower()
    for component in (
        "partclone", "udpcast", "busybox", "ntfs-3g", "hivex",
        "libvncserver", "zstd", "curl", "dropbear",
    ):
        assert component in notices, f"THIRD_PARTY_NOTICES.md does not mention {component}"


def test_iso_places_third_party_notices_at_its_root() -> None:
    text = BUILD_ISO.read_text(encoding="utf-8")
    assert '[[ -f "$ISO_TREE/imagesrv-src/THIRD_PARTY_NOTICES.md" ]]' in text
    assert '"$ISO_TREE/THIRD_PARTY_NOTICES.md"' in text
