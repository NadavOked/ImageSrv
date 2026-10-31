"""כלים — תחום windows (#649): שני הכלים שנדב סימן בקטלוג (#1050).

‏`win-users` ו-`win-blank-password` בלבד; ששת כלי האבחון של המודול המקורי
נמחקו, והטסטים שלהם איתם. האריזה — `tests/test_tools_packing.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import sizelimit


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "agent/lib/tools/tools_windows.sh"
BUILDER = ROOT / "tools/build_initramfs.sh"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(not SH, reason="POSIX sh is required")

TOOLS = {
    "win-users": "SAM",
    "win-blank-password": "SAM:alice",
}

REMOVED = ["win-registry", "win-bcd", "win-hibernation", "win-bitlocker",
           "win-system-files", "win-disk-usage"]


def fake(path: Path, name: str, body: str = "printf 'fake output\\n'") -> None:
    target = path / name
    target.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    target.chmod(0o755)


def fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake(bindir, "chntpw")
    (tmp_path / "SAM").write_bytes(b"sam")
    env = os.environ.copy()
    env.update(RUN_DIR=str(tmp_path / "run"), MACHINE_NAME="lab-pc")
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return tmp_path, env


def run(tmp_path: Path, tool: str, arg: str, confirm: str = "lab-pc",
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command = (
        f'. "{SCRIPT.as_posix()}"; '
        f'cd "{tmp_path.as_posix()}"; tools_windows_run "$1" "$2" "$3"'
    )
    return subprocess.run(
        [SH, "-c", command, "tools-windows", tool, arg, confirm],
        env=env, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=20, stdin=subprocess.DEVNULL,
    )


@pytest.mark.parametrize("tool,arg", TOOLS.items())
def test_every_tool_writes_it_readable_output(tmp_path: Path, tool: str, arg: str):
    base, env = fixture(tmp_path)
    done = run(base, tool, arg, env=env)
    assert done.returncode == 0, done.stderr
    output = (base / "run" / f"tool-{tool}.out").read_text(encoding="utf-8")
    assert "מה זה אומר:" in output
    assert len(output.splitlines()) >= 2


@pytest.mark.parametrize("tool", REMOVED)
def test_removed_diagnostic_is_unknown(tmp_path: Path, tool: str):
    """הקטלוג הוא רשימת-היתר: כלי שנדב לא בחר אינו קיים במודול (#1050)."""
    base, env = fixture(tmp_path)
    done = run(base, tool, "x", env=env)
    assert done.returncode == 2, done.stderr
    output = (base / "run" / f"tool-{tool}.out").read_text(encoding="utf-8")
    assert "לא מוכר" in output


def test_missing_binary_is_could_not_check_not_ok(tmp_path: Path):
    base, env = fixture(tmp_path)
    (base / "bin/chntpw").unlink()
    done = run(base, "win-users", "SAM", env=env)
    output = (base / "run/tool-win-users.out").read_text(encoding="utf-8")
    assert done.returncode == 2
    assert "לא ניתן לבדוק" in output
    assert "תקין" not in output


def test_destroy_refuses_before_running_binary(tmp_path: Path):
    base, env = fixture(tmp_path)
    marker = base / "chntpw-ran"
    fake(base / "bin", "chntpw", f'touch "{marker.as_posix()}"; exit 0')
    done = run(base, "win-blank-password", "SAM:alice", confirm="wrong", env=env)
    output = (base / "run/tool-win-blank-password.out").read_text(encoding="utf-8")
    assert done.returncode == 3
    assert not marker.exists()
    assert "דבר לא נמח" in output


def test_lists_contract_fields_and_valid_risks():
    done = subprocess.run(
        [SH, "-c", f'. "{SCRIPT.as_posix()}"; tools_windows_list'],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
        timeout=10, check=True, stdin=subprocess.DEVNULL,
    )
    rows = done.stdout.splitlines()
    assert [row.split("|")[0] for row in rows] == list(TOOLS)
    assert all(len(row.split("|")) == 5 for row in rows)
    assert all(row.split("|")[3] in {"ro", "rw", "destroy"} for row in rows)


def test_builder_has_no_unconditional_windows_block():
    """‏#1050: הבנאי אורז לפי הבחירה, לא בלוק קבוע לכל תחום."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "REQUIRED_WINDOWS_BINARIES" not in text
    assert "--tools-selection" in text


def test_agent_file_stays_below_limit():
    sizelimit.assert_within_limit(SCRIPT)
