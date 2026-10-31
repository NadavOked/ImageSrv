"""טסטי תחום boot (#649) — כלים מזויפים ב-PATH, בלי חומרה אמיתית.

‏#1050: נשאר רק `esp-fsck-repair`, הכלי היחיד של התחום שנדב סימן
ב-`server/tools_catalog.json`; שמונת האחרים נמחקו, והטסטים שלהם איתם.
האריזה — `tests/test_tools_packing.py`.

אין gcc/Pango/efibootmgr/mokutil בווינדוס: הקומפילציה ו-initrd אמיתי
אצל המתאם במעבדה. כאן בודקים את `tools_boot.sh` מול stubs ב-PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import sizelimit
from native import requires_native

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "agent"
BOOT_SH = AGENT / "lib" / "tools" / "tools_boot.sh"
BUILDER = ROOT / "tools" / "build_initramfs.sh"

BASH = shutil.which("bash") or shutil.which("sh")
pytestmark = requires_native(("bash", BASH), why="tools_boot.sh רץ ב-POSIX sh")

REMOVED = ["efi-boot-entries", "secure-boot-state", "sb-keys", "efi-clean-orphans",
           "grub-shim-versions", "memtest-reboot", "esp-check", "efi-boot-next"]


def posix(p: Path) -> str:
    return p.as_posix()


def write_stub(bin_dir: Path, name: str, body: str, rc: int = 0) -> None:
    p = bin_dir / name
    p.write_text(
        "#!/bin/sh\n" + body + f"\nexit {rc}\n",
        encoding="utf-8",
        newline="\n",
    )


def run_boot(
    tmp_path: Path,
    tool_id: str,
    arg: str = "",
    confirm: str = "",
    *,
    stubs: dict[str, tuple[str, int]] | None = None,
) -> subprocess.CompletedProcess:
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)

    defaults = {
        "fsck.vfat": ('echo "$1: clean"', 0),
        "hostname": ('echo "lab-pc-01"', 0),
    }
    if stubs:
        defaults.update(stubs)
    for name, (body, rc) in defaults.items():
        write_stub(bin_dir, name, body, rc)

    # One bash process: chmod, resolve Unix PATH, source, run (#14 handle leak).
    script = f"""
set -e
cd {posix(bin_dir)!r}
chmod +x *
BIN_PWD=$(pwd)
export PATH="$BIN_PWD:$PATH"
export RUN_DIR={posix(run_dir)!r}
export MACHINE_NAME=lab-pc-01
export IMAGESRV_TEST=1
. {posix(BOOT_SH)!r}
set +e
tools_boot_run {tool_id!r} {arg!r} {confirm!r}
exit $?
"""
    return subprocess.run(
        [BASH, "-c", script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def out_text(tmp_path: Path, tool_id: str) -> str:
    return (tmp_path / "run" / f"tool-{tool_id}.out").read_text(
        encoding="utf-8", errors="replace"
    )


def test_sizelimit_tools_boot():
    n = sizelimit.assert_within_limit(BOOT_SH)
    assert n <= 300


def test_list_is_exactly_esp_fsck_repair():
    script = f". {posix(BOOT_SH)!r}; tools_boot_list"
    proc = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert [ln.split("|", 1)[0] for ln in lines] == ["esp-fsck-repair"], lines
    parts = lines[0].split("|")
    assert len(parts) == 5, lines[0]
    assert parts[1] == "boot"
    assert parts[3] == "rw"


@pytest.mark.parametrize("tool_id", REMOVED)
def test_removed_boot_tool_is_unknown(tmp_path: Path, tool_id: str):
    """הקטלוג הוא רשימת-היתר: כלי שנדב לא בחר אינו קיים במודול (#1050)."""
    proc = run_boot(tmp_path, tool_id, arg="Boot0003", confirm="lab-pc-01")
    assert proc.returncode == 2, (proc.returncode, proc.stderr)
    assert "לא מוכר" in out_text(tmp_path, tool_id)


def test_missing_fsck_vfat_is_rc2_not_ok(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_stub(bin_dir, "hostname", "echo lab-pc-01")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # PATH keeps bash's own /usr/bin:/bin (mkdir, mv) but no fsck.vfat stub --
    # and the test proves the absence rather than assuming it (principle 5).
    script = f"""
cd {posix(bin_dir)!r}
chmod +x *
export PATH="$(pwd):/usr/bin:/bin"
command -v fsck.vfat >/dev/null 2>&1 && {{ echo "fsck.vfat is on PATH; the test proves nothing" >&2; exit 99; }}
export RUN_DIR={posix(run_dir)!r}
export MACHINE_NAME=lab-pc-01
. {posix(BOOT_SH)!r}
tools_boot_run esp-fsck-repair /dev/sda1 lab-pc-01
exit $?
"""
    proc = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    text = out_text(tmp_path, "esp-fsck-repair")
    assert "חסר" in text
    assert "מה זה אומר" in text
    assert "הכל תקין" not in text
    assert "OK" not in text


def test_esp_fsck_repair_refuses_without_confirm(tmp_path: Path):
    ran = tmp_path / "fsck.ran"
    stubs = {
        "fsck.vfat": (f'echo ran > {posix(ran)!r}; echo fsck', 0),
    }
    proc = run_boot(
        tmp_path,
        "esp-fsck-repair",
        arg="/dev/sda1",
        confirm="",
        stubs=stubs,
    )
    assert proc.returncode == 3
    assert not ran.exists()
    assert "מה זה אומר" in out_text(tmp_path, "esp-fsck-repair")


def test_esp_fsck_repair_with_confirm_runs(tmp_path: Path):
    ran = tmp_path / "fsck.ran"
    stubs = {
        "fsck.vfat": (f'echo "$*" > {posix(ran)!r}; echo fsck', 0),
    }
    proc = run_boot(tmp_path, "esp-fsck-repair", arg="/dev/sda1",
                    confirm="lab-pc-01", stubs=stubs)
    text = out_text(tmp_path, "esp-fsck-repair")
    assert proc.returncode == 0, (proc.returncode, text, proc.stderr)
    assert ran.read_text(encoding="utf-8").split() == ["-a", "/dev/sda1"]
    assert "מה זה אומר" in text


def test_esp_fsck_repair_without_a_device_is_rc1(tmp_path: Path):
    proc = run_boot(tmp_path, "esp-fsck-repair", arg="sda1", confirm="lab-pc-01")
    assert proc.returncode == 1
    assert "/dev/" in out_text(tmp_path, "esp-fsck-repair")


def test_build_initramfs_has_no_unconditional_boot_block():
    """‏#1050: הבנאי אורז לפי הבחירה, לא בלוק קבוע לכל תחום."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "REQUIRED_TOOLS_BOOT=" not in text
    assert "memtest86+x64.efi" not in text
    assert "--tools-selection" in text


def test_unknown_id_rc2(tmp_path: Path):
    proc = run_boot(tmp_path, "not-a-real-tool")
    assert proc.returncode == 2
    assert "מה זה אומר" in out_text(tmp_path, "not-a-real-tool")
