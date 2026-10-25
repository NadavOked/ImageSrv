"""#1714 -- the one sgdisk call writes the SAME table as the separate calls did.

Real sgdisk, under busybox ash like the agent, on two sparse files the size of
a 120GiB disk. One gets ``apply_gpt`` / ``expand_last`` (the one call each);
the other gets the sequence they ran before #1714, one sgdisk process per
operation, spelled out below as the reference. ``sgdisk --backup`` of both --
protective MBR, both headers, the whole entry array, CRCs included -- must be
equal byte for byte: after the table, and again after the expansion.

Every GUID is fixed (disk, every partition), so nothing random differs. The
layout is the common one: ESP, MSR, Windows, Recovery in the tail with
attributes (#1130), on a bigger disk -- the recovery moves to the end and
Windows takes the rest (#58). Linux only (sgdisk + busybox): the Testrunner.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_json_escape import BUSYBOX, bb, source_line

SGDISK = shutil.which("sgdisk")

pytestmark = requires_native(
    ("busybox", BUSYBOX), ("sgdisk", SGDISK), posix=True,
    why="the claim is about real sgdisk's table -- a stub would check our own assumption")

SECTORS = 251658240          # 120 GiB
DISK_GUID = "047B3400-0000-0000-0000-0000003AEE00"
SHA = "a" * 64
ESP, MSR = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B", "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
BASIC, RECOVERY = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7", "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ATTRS = "8000000000000001"
#: idx, type, role, fs, start, sectors, unique GUID, attrs -- a 30GiB Windows image
PARTS = [
    (1, ESP, "esp", "vfat", 2048, 204800, "11111111-1111-1111-1111-111111111111", ""),
    (2, MSR, "msr", "unknown", 206848, 32768, "22222222-2222-2222-2222-222222222222", ""),
    (3, BASIC, "windows", "ntfs", 239616, 61210624, "33333333-3333-3333-3333-333333333333", ""),
    (4, RECOVERY, "recovery", "ntfs", 61450240, 1638400, "44444444-4444-4444-4444-444444444444", ATTRS),
]


def _rows() -> list[str]:
    return [f"{i}|{t}|{r}|{fs}|{s}|{n * 512}|p{i}.img.zst|{SHA}|false|{u}||{a}"
            for i, t, r, fs, s, n, u, a in PARTS]


def _sg(*args: str) -> None:
    done = subprocess.run([SGDISK, *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 0, (args, done.stdout + done.stderr)


def _blank(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.truncate(SECTORS * 512)
    return path


def _backup(img: Path) -> bytes:
    out = img.with_suffix(".gptbak")
    out.unlink(missing_ok=True)
    _sg("--backup", str(out), str(img))
    return out.read_bytes()


def _reference_table(img: Path) -> None:
    """apply_gpt before #1714: one sgdisk process per operation."""
    _sg("--zap-all", str(img))
    for i, t, _r, _fs, s, n, u, a in PARTS:
        _sg("-a", "1", "-n", f"{i}:{s}:{s + n - 1}", "-t", f"{i}:{t}", "-u", f"{i}:{u}",
            *(["-A", f"{i}:=:0x{a}"] if a else []), str(img))
    for i, *_rest in PARTS:   # #1798: the incomplete-write mark rides in the same table call
        _sg("-c", f"{i}:imagectl-incomplete", str(img))
    _sg("-U", DISK_GUID, str(img))
    _sg("-e", str(img))


def _reference_expand(img: Path) -> None:
    """expand_last before #1714: -d the tail, -n it at the end, -d/-n the candidate."""
    i, t, _r, _fs, _s, n, u, a = PARTS[3]
    _sg("-d", str(i), str(img))
    start = ((SECTORS - 34) - n + 1) // 2048 * 2048            # #1467: aligned start, exact size
    _sg("-a", "1", "-n", f"{i}:{start}:{start + n - 1}", "-t", f"{i}:{t}", "-u", f"{i}:{u}",
        "-A", f"{i}:=:0x{a}", str(img))
    c, ct, _r, _fs, cs, _n, cu, _a = PARTS[2]
    _sg("-a", "1", "-d", str(c), "-n", f"{c}:{cs}:0", "-t", f"{c}:{ct}", "-u", f"{c}:{cu}", str(img))
    for k in (c, i):   # #1798: -d/-n rebuilt them unnamed -- the mark again, candidate then tail
        _sg("-c", f"{k}:imagectl-incomplete", str(img))


def _agent(tmp_path: Path, call: str) -> str:
    plan = tmp_path / "plan.txt"
    plan.write_text("\n".join(_rows()) + "\n", encoding="utf-8")
    sysblock = tmp_path / "sys" / "block" / "sda"
    sysblock.mkdir(parents=True, exist_ok=True)
    (sysblock / "size").write_text(f"{SECTORS}\n")
    (tmp_path / "run").mkdir(exist_ok=True)
    body = (
        f'RUN_DIR="{(tmp_path / "run").as_posix()}"; LOG_FILE="{(tmp_path / "log").as_posix()}"\n'
        f'DEVROOT="{(tmp_path / "dev").as_posix()}"; SYSROOT="{tmp_path.as_posix()}"\n'
        'log() { echo "LOG $*" >> "$LOG_FILE"; }\n'
        # waits.sh: apply_gpt/expand_last reread through reread_table (#1594). Without it
        # the call is "not found", and the log says the kernel refused.
        + source_line("waits.sh", "restore.sh", "expand.sh", "expandshare.sh", "writemark.sh")
        + f'manifest_plan() {{ cat "{plan.as_posix()}"; }}\n'
        f'json_get() {{ case "$2" in .sector_size) echo 512 ;; .scheme) echo gpt ;; '
        f'.disk_guid) echo {DISK_GUID} ;; *) echo null ;; esac; }}\n'
        f'blockdev() {{ case "$1" in --getss) echo 512 ;; --getsz) echo {SECTORS} ;; *) return 0 ;; esac; }}\n'
        'target_write_started() { :; }\n'
        'verify_table() { :; }   # the read-back is the byte comparison below\n'
        'share_tail() { printf "%s\\n" "$4"; }   # #1897: no shrunk tail here; test_expand_share_1897.py\n'
        f'if {call} sda manifest.json; then echo OK; else echo FAIL; fi\n'
    )
    return bb(tmp_path, body).strip()


def test_the_one_call_table_is_byte_identical_to_the_separate_calls(tmp_path):
    agent = _blank(tmp_path / "dev" / "sda")
    ref = _blank(tmp_path / "ref" / "sda")

    assert _agent(tmp_path, "apply_gpt") == "OK", (tmp_path / "log").read_text()
    _reference_table(ref)
    assert _backup(agent) == _backup(ref), "apply_gpt: the one call wrote another table"

    assert _agent(tmp_path, "expand_last") == "OK", (tmp_path / "log").read_text()
    assert (tmp_path / "run" / "targets" / "sda" / "expanded").read_text().strip() == "3|ntfs"
    _reference_expand(ref)
    assert _backup(agent) == _backup(ref), "expand_last: the one call wrote another table"

    # and the table is the one meant: Windows grown to the moved recovery, GUIDs, attrs
    info3 = subprocess.run([SGDISK, "-i", "3", str(agent)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL).stdout
    info4 = subprocess.run([SGDISK, "-i", "4", str(agent)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL).stdout
    tail = ((SECTORS - 34) - 1638400 + 1) // 2048 * 2048
    assert "First sector: 239616 " in info3 and f"Last sector: {tail - 1} " in info3, info3
    assert "33333333-3333-3333-3333-333333333333" in info3, info3
    assert f"First sector: {tail} " in info4 and f"Last sector: {tail + 1638400 - 1} " in info4, info4
    assert "44444444-4444-4444-4444-444444444444" in info4 and ATTRS in info4.upper(), info4
