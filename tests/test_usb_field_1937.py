"""#1937 -- what the college's field round (21/10, HP 8000/8100/6005, Lenovo) found on the
USB stick, beyond the agent's own files (drawer_gone: test_smart_tables_1921.py; the
stick's serial: test_usb_clone.py; the NTFS of a target: test_lc_ntfs_target_1937.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_cloner_gui import BASH, posix

REPO = Path(__file__).resolve().parent.parent


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# --- 4. the IDE note is an orange banner (Nadav: "he did not notice it") ----------------------

def test_the_ide_note_is_the_screens_orange_warning_box():
    src = _src("native-gui/src/screens_local.c")
    fn = src[src.index("void lc_draw_source("):]
    assert "if (ide) {" in fn, "the IDE note is not a block of its own"   # an assertion, not a ValueError
    block = fn[fn.index("if (ide) {"):fn.index("if (sv)")]
    # the box the screen already draws for a warning: warning_soft, a warn line, the led, ink text
    assert "draw_fill_rrect(cr, ar, 3, t->warning_soft); draw_border_rrect(cr, ar, 3, t->warn, 1);" in block, block
    assert "sb_led(cr, ar.x + ar.w - 16, ar.y + 10 + 8, 10, t->warn, t->warning_soft, 1, 1);" in block, block
    assert "text_draw_r(cr, &pi, ar.x + ar.w - 32, ar.y + 10, t->ink);" in block, block
    # the box it reuses is that of the confirm's shrink note -- not a new design
    assert src.count("sb_led(cr, right - 16, y + 10 + 8, 10, t->warn, t->warning_soft, 1, 1);") == 1
    assert "if (ide) text_draw_r(cr, &pi, xr" not in fn   # no longer a line of orange text


# --- 5. how to write the stick: Rufus in DD mode (balenaEtcher failed in the field) -----------

def test_rufus_dd_is_the_way_and_etcher_is_not_recommended():
    js = _src("server/static/console.js")
    sheet = js[js.index("function usbCloneSheet()"):js.index("async function healthUpdateCheck()")]
    assert "צורבים ב-Rufus במצב DD" in sheet and "balenaEtcher נכשל בשטח" in sheet, sheet
    row = js[js.index('key: "usbclone"'):js.index('key: "class_deploy_enabled"')]
    assert "ב-Rufus במצב DD (balenaEtcher נכשל בשטח" in row, row
    assert "(Rufus במצב DD, balenaEtcher או dd)" not in js   # Etcher was offered as an equal
    build = _src("tools/build_usb_clone.sh")
    assert "'To write a new version: Rufus, \"DD Image\" mode (balenaEtcher failed in the field" in build
    assert "with dd, balenaEtcher or Rufus" not in build
    doc = _src("docs/local-clone.md")
    assert "**צורבים ב-Rufus במצב DD**" in doc and "balenaEtcher נכשל בשטח" in doc


# --- 7. an old ATI/AMD display (HP 6005 Pro, Radeon HD 4200 = RS880): its microcode -------------

def _radeon_block(fw: Path, root: Path) -> str:
    src = _src("tools/build_initramfs.sh")
    assert "RADEON_LEGACY_CHIPS=" in src, "no radeon firmware block in the build"
    block = src[src.index("RADEON_LEGACY_CHIPS="):src.index("# --- site/lab overlay")]
    return ('set -euo pipefail; RADEON_LEGACY_FW=1; ROOT=' + repr(posix(root)) + '\n'
            + block.replace("for _f in /lib/firmware/radeon/", f"for _f in {posix(fw)}/"))   # the builder's own, not $ROOT's


@pytest.mark.skipif(BASH is None, reason="bash")
def test_the_stick_packs_radeons_r600_to_evergreen_microcode_and_nothing_else(tmp_path):
    fw = tmp_path / "fw"; fw.mkdir(); root = tmp_path / "root"
    have = ["RS780_pfp.bin", "RS780_me.bin", "RS780_uvd.bin", "R600_rlc.bin", "R700_rlc.bin", "RV710_smc.bin",
            "CEDAR_me.bin", "SUMO2_pfp.bin", "TAHITI_ce.bin", "BONAIRE_mc.bin", "R100_cp.bin"]
    for name in have:
        (fw / name).write_bytes(b"\x00" * 64)
    out = subprocess.run([BASH, "-c", _radeon_block(fw, root)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    packed = sorted(p.name for p in (root / "lib" / "firmware" / "radeon").iterdir())
    assert packed == sorted(["RS780_pfp.bin", "RS780_me.bin", "R600_rlc.bin", "R700_rlc.bin", "RV710_smc.bin",
                             "CEDAR_me.bin", "SUMO2_pfp.bin"]), packed   # no UVD, no SI/CIK, no r100
    assert "radeon legacy firmware: 7 files" in out.stderr, out.stderr   # the size is said at build time


@pytest.mark.skipif(BASH is None, reason="bash")
def test_without_the_rs780_microcode_the_stick_is_not_built(tmp_path):
    fw = tmp_path / "fw"; fw.mkdir()
    (fw / "R600_rlc.bin").write_bytes(b"\x00")
    out = subprocess.run([BASH, "-c", _radeon_block(fw, tmp_path / "root")], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 1 and "firmware-amd-graphics" in out.stderr, (out.returncode, out.stderr)


def test_the_usb_build_asks_for_it_and_the_pxe_build_does_not_by_default():
    assert "--with-gui --radeon-legacy-firmware" in _src("tools/build_usb_clone.sh")
    assert "RADEON_LEGACY_FW=0\n" in _src("tools/build_initramfs.sh")
