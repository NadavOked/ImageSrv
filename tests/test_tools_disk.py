"""כלים — תחום disk (#649): רשימה, חסר→rc2, destroy בלי אישור→rc3.

‏#1050: נשארו רק חמשת כלי המחיקה שנדב סימן ב-`server/tools_catalog.json`;
‏12 כלי הקריאה של המודול המקורי נמחקו, והטסטים שלהם איתם. האריזה
עצמה — `tests/test_tools_packing.py`.

הבינארים מזויפים ב-PATH; אין דיסקים אמיתיים ואין initrd כאן (ווינדוס).
קומפילציה ואריזה אמיתית — אצל המתאם במעבדה.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import sizelimit
from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
DISK_SH = AGENT / "lib" / "tools" / "tools_disk.sh"
BUILDER = REPO / "tools" / "build_initramfs.sh"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def write_fake(box: Path, name: str, body: str, rc: int = 0) -> str:
    p = box / name
    p.write_text(
        "#!/bin/sh\n" + body + f"\nexit {rc}\n",
        encoding="utf-8",
        newline="\n",
    )
    return name


def chmod_all(box: Path, names: list[str]) -> None:
    # One bash call: os.chmod on Windows does not set the exec bit Git Bash sees.
    quoted = " ".join(repr(n) for n in names)
    subprocess.run(
        [BASH, "-c", f"cd {posix(box)!r} && chmod +x {quoted}"],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )


def make_fakes(box: Path) -> None:
    names = [
        write_fake(
            box,
            "wipefs",
            'echo wipefs-out; echo WIPED >>"${FAKE_LOG:-/dev/null}"',
        ),
        write_fake(
            box, "blkdiscard", 'echo DISCARDED >>"$FAKE_LOG"; echo blkdiscard-ok'
        ),
        write_fake(box, "hdparm", 'echo HDPARM "$@" >>"$FAKE_LOG"; echo hdparm-ok'),
        write_fake(box, "nvme", 'echo NVME "$@" >>"$FAKE_LOG"; echo nvme-ok'),
        write_fake(box, "dd", 'echo DD "$@" >>"$FAKE_LOG"; echo dd-ok'),
        write_fake(box, "hostname", "echo lab-pc"),
    ]
    chmod_all(box, names)

def run_disk(
    tmp_path: Path,
    *,
    cmd: str,
    fakes: bool = True,
    env_extra: str = "",
) -> subprocess.CompletedProcess:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    box = tmp_path / "bin"
    box.mkdir(exist_ok=True)
    if fakes:
        make_fakes(box)
    # Git Bash needs a Unix pwd on PATH — a raw Windows path is invisible
    # to command -v (same pattern as test_smart / test_agent stubs).
    script = (
        f"export RUN_DIR=$(cd {posix(run)!r} && pwd) MACHINE_NAME=lab-pc "
        f"FAKE_LOG=$(cd {posix(tmp_path)!r} && pwd)/touched.log "
        f'PATH="$(cd {posix(box)!r} && pwd):/usr/bin:/bin"; '
        f"{env_extra}"
        f". {posix(DISK_SH)!r}; "
        + cmd
    )
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO),
        stdin=subprocess.DEVNULL,
    )


def out_text(tmp_path: Path, tool_id: str) -> str:
    return (tmp_path / "run" / f"tool-{tool_id}.out").read_text(encoding="utf-8")


# --- sizelimit / packaging markers ------------------------------------------

def test_tools_disk_sh_within_sizelimit():
    n = sizelimit.assert_within_limit(DISK_SH)
    assert n > 50


def test_build_initramfs_has_no_unconditional_disk_block():
    """‏#1050: הבנאי אורז לפי הבחירה (`--tools-selection`), לא בלוק קבוע
    לכל תחום — בלי הדגל לא נארז אף בינארי של ארגז הכלים."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "REQUIRED_DISK_TOOLS" not in text
    assert "--tools-selection" in text


# --- list -------------------------------------------------------------------

def test_tools_disk_list_is_exactly_the_catalog_wipe_tools(tmp_path: Path):
    r = run_disk(tmp_path, cmd="tools_disk_list")
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    ids = [ln.split("|")[0] for ln in lines]
    assert ids == [t for t, _ in DESTROY_TOOLS], ids
    for ln in lines:
        parts = ln.split("|")
        assert len(parts) == 5, ln
        assert parts[1] == "disk"
        assert parts[3] == "destroy"


# --- every tool writes outfile + "מה זה אומר" --------------------------------

DESTROY_TOOLS = [
    ("disk-blkdiscard", "/dev/sda"),
    ("disk-hdparm-erase", "/dev/sda"),
    ("disk-nvme-format", "/dev/nvme0n1"),
    ("disk-dd-zero", "/dev/sda"),
    ("disk-wipefs-all", "/dev/sda"),
]


@pytest.mark.parametrize("tool_id", [
    "disk-lsblk", "disk-blkid", "disk-sgdisk", "disk-type", "disk-df",
    "disk-ntfs-probe", "disk-ntfsfix", "disk-e2fsck", "disk-badblocks",
    "disk-wipefs-probe", "disk-sfdisk-dump", "disk-blockdev",
])
def test_removed_inspection_tool_is_unknown(tmp_path: Path, tool_id: str):
    """הקטלוג הוא רשימת-היתר: כלי שנדב לא בחר אינו קיים במודול (#1050)."""
    r = run_disk(tmp_path, cmd=f"tools_disk_run {tool_id} /dev/sda")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "כלי לא מוכר" in out_text(tmp_path, tool_id)


@pytest.mark.parametrize("tool_id,arg", DESTROY_TOOLS)
def test_destroy_with_confirm_runs(tmp_path: Path, tool_id: str, arg: str):
    log = tmp_path / "touched.log"
    log.write_text("", encoding="utf-8")
    r = run_disk(
        tmp_path,
        cmd=f"tools_disk_run {tool_id} {arg!r} lab-pc",
    )
    assert r.returncode == 0, (r.stdout, r.stderr)
    body = out_text(tmp_path, tool_id)
    assert "מה זה אומר" in body
    assert "פעולה הרסנית" in body
    # Fake binary was invoked (positive evidence of execution).
    assert log.read_text(encoding="utf-8").strip() != ""


@pytest.mark.parametrize("tool_id,arg", DESTROY_TOOLS)
def test_destroy_without_confirm_rc3_and_no_touch(
    tmp_path: Path, tool_id: str, arg: str
):
    log = tmp_path / "touched.log"
    log.write_text("", encoding="utf-8")
    r = run_disk(
        tmp_path,
        cmd=f"tools_disk_run {tool_id} {arg!r} wrong-name",
    )
    assert r.returncode == 3, (r.stdout, r.stderr, out_text(tmp_path, tool_id))
    body = out_text(tmp_path, tool_id)
    assert "סורב" in body
    assert "מה זה אומר" in body
    assert log.read_text(encoding="utf-8").strip() == ""


def test_destroy_empty_confirm_rc3(tmp_path: Path):
    log = tmp_path / "touched.log"
    log.write_text("", encoding="utf-8")
    r = run_disk(tmp_path, cmd="tools_disk_run disk-blkdiscard /dev/sda ''")
    assert r.returncode == 3
    assert log.read_text(encoding="utf-8").strip() == ""


# --- missing binary → rc 2, not "תקין" --------------------------------------

def test_missing_binary_rc2_not_ok(tmp_path: Path):
    # PATH with only hostname — hide real tools; blkdiscard must be "missing".
    run = tmp_path / "run"
    run.mkdir()
    empty = tmp_path / "empty"
    empty.mkdir()
    write_fake(empty, "hostname", "echo lab-pc")
    chmod_all(empty, ["hostname"])
    script = (
        f"export RUN_DIR=$(cd {posix(run)!r} && pwd) MACHINE_NAME=lab-pc "
        f'PATH="$(cd {posix(empty)!r} && pwd)"; '
        f". {posix(DISK_SH)!r}; "
        "tools_disk_run disk-blkdiscard /dev/sda lab-pc"
    )
    r = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO),
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 2, (r.stdout, r.stderr)
    body = (run / "tool-disk-blkdiscard.out").read_text(encoding="utf-8")
    assert "חסר" in body or "לא נבדק" in body
    assert "מה זה אומר" in body
    assert "תקין" not in body


def test_unknown_id_rc2(tmp_path: Path):
    r = run_disk(tmp_path, cmd="tools_disk_run disk-nope")
    assert r.returncode == 2
    assert "מה זה אומר" in out_text(tmp_path, "disk-nope")


def test_bad_device_rc2(tmp_path: Path):
    r = run_disk(tmp_path, cmd="tools_disk_run disk-wipefs-all '$(rm -rf /)' lab-pc")
    assert r.returncode == 2
    body = out_text(tmp_path, "disk-wipefs-all")
    assert "לא חוקי" in body or "לא הורצה" in body
