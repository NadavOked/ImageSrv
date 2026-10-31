"""כלים — תחום hw (#649): הכלי היחיד שנדב סימן בקטלוג (#1050) — `tpm-clear`.

‏14 כלי המלאי של המודול המקורי נמחקו, והטסטים שלהם איתם. האריזה —
`tests/test_tools_packing.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sizelimit import assert_within_limit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent/lib/tools/tools_hw.sh"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(not SH, reason="POSIX sh is unavailable")

REMOVED = ["dmi", "pci", "usb", "thermal", "cpu", "memory", "tpm", "secureboot",
           "cmos", "diskhash", "hwerrors", "lshw", "cpuid", "sensors"]


def fake_tools(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    p = bindir / "tpm2_clear"
    p.write_text("#!/bin/sh\nprintf 'fake %s output\\n' \"$0\"\n", encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return bindir


def run_tool(tmp_path: Path, tool_id: str, *, path: Path | None = None,
             arg: str = "", confirm: str = "LAB-PC") -> subprocess.CompletedProcess[str]:
    run = tmp_path / "run"
    env = os.environ.copy()
    env.update(RUN_DIR=str(run), MACHINE_NAME="LAB-PC")
    env["PATH"] = f"{path or fake_tools(tmp_path)}{os.pathsep}{env.get('PATH', '')}"
    command = f'. "{SCRIPT.as_posix()}"; tools_hw_run "$1" "$2" "$3"'
    return subprocess.run([SH, "-c", command, "sh", tool_id, arg, confirm], env=env,
                          text=True, encoding="utf-8", errors="replace",
                          capture_output=True, timeout=10, stdin=subprocess.DEVNULL)


def test_tpm_clear_with_the_machine_name_runs_and_writes_a_verdict(tmp_path: Path):
    done = run_tool(tmp_path, "tpm-clear")
    assert done.returncode == 0, (done.stdout, done.stderr)
    out = (tmp_path / "run/tool-tpm-clear.out").read_text(encoding="utf-8")
    assert "fake" in out and "מה זה אומר:" in out


def test_missing_binary_is_could_not_check_never_ok(tmp_path: Path):
    bindir = fake_tools(tmp_path)
    (bindir / "tpm2_clear").unlink()
    done = run_tool(tmp_path, "tpm-clear", path=bindir)
    assert done.returncode == 2
    out = (tmp_path / "run/tool-tpm-clear.out").read_text(encoding="utf-8")
    assert "לא ניתן לבדוק" in out
    assert "תקין" not in out or "אין להסיק שהחומרה תקינה" in out


def test_tpm_clear_refuses_without_machine_name_and_executes_nothing(tmp_path: Path):
    bindir = fake_tools(tmp_path)
    marker = tmp_path / "called"
    tool = bindir / "tpm2_clear"
    tool.write_text(f"#!/bin/sh\ntouch '{marker.as_posix()}'\n", encoding="utf-8", newline="\n")
    tool.chmod(0o755)
    done = run_tool(tmp_path, "tpm-clear", path=bindir, confirm="wrong-name")
    assert done.returncode == 3
    assert not marker.exists()
    out = (tmp_path / "run/tool-tpm-clear.out").read_text(encoding="utf-8")
    assert "יימחקו" in out and "דבר לא נמחק" in out and "מה זה אומר:" in out


@pytest.mark.parametrize("tool_id", REMOVED)
def test_removed_inventory_tool_is_unknown(tmp_path: Path, tool_id: str):
    """הקטלוג הוא רשימת-היתר: כלי שנדב לא בחר אינו קיים במודול (#1050)."""
    done = run_tool(tmp_path, tool_id, arg="/dev/fake0")
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert not (tmp_path / "run" / f"tool-{tool_id}.out").exists()


def test_list_is_exactly_tpm_clear():
    command = f'. "{SCRIPT.as_posix()}"; tools_hw_list'
    done = subprocess.run([SH, "-c", command], text=True, encoding="utf-8", errors="replace",
                          capture_output=True, check=True, stdin=subprocess.DEVNULL)
    rows = done.stdout.splitlines()
    assert [row.split("|", 1)[0] for row in rows] == ["tpm-clear"]
    assert rows[0].split("|")[3] == "destroy"


def test_hw_shell_stays_within_repository_limit():
    assert assert_within_limit(SCRIPT) < 300


def test_builder_has_no_unconditional_hw_block_but_keeps_the_tcti_plugin():
    """‏#1050: הבנאי אורז לפי הבחירה. ה-TCTI של tpm2 נטען ב-dlopen ו-ldd אינו
    רואה אותו — לכן הוא נארז במפורש כש-tpm2_clear נבחר, ורק אז."""
    text = (ROOT / "tools/build_initramfs.sh").read_text(encoding="utf-8")
    assert "REQUIRED_HW_TOOL_BINARIES" not in text
    assert "/etc/sensors3.conf" not in text
    assert "libtss2-tcti-device" in text
    assert '[ "$_tb" = tpm2_clear ]' in text
