"""CPU frequency setup in the agent initramfs (#1441)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from native import requires_native


LIB = Path(__file__).resolve().parent.parent / "agent" / "lib" / "core" / "cpufreq.sh"
pytestmark = requires_native("sh", why="the production helper is POSIX sh")


def _run(root: Path, modprobe: str = "return 0") -> subprocess.CompletedProcess[str]:
    script = (
        f"SYSROOT={root.as_posix()!r}; export SYSROOT; "
        f"modprobe() {{ {modprobe}; }}; "
        f". {LIB.as_posix()!r}; cpufreq_performance"
    )
    return subprocess.run(
        ["sh", "-c", script], text=True, capture_output=True, check=False, stdin=subprocess.DEVNULL,
        env={**os.environ, "LC_ALL": "C"},
    )


def _cpu(root: Path, number: int, governors: str, current: str = "powersave") -> Path:
    cpufreq = root / "sys/devices/system/cpu" / f"cpu{number}" / "cpufreq"
    cpufreq.mkdir(parents=True)
    (cpufreq / "scaling_driver").write_text("acpi-cpufreq\n", encoding="utf-8")
    (cpufreq / "scaling_available_governors").write_text(governors + "\n", encoding="utf-8")
    (cpufreq / "scaling_governor").write_text(current + "\n", encoding="utf-8")
    return cpufreq


def test_missing_driver_and_failed_modprobe_is_nonfatal(tmp_path: Path):
    result = _run(tmp_path, "return 1")
    assert result.returncode == 0
    assert result.stdout.strip() == "cpufreq: no driver (CPU stays at firmware clock)"


def test_performance_is_set_on_every_cpu_and_read_back(tmp_path: Path):
    cpu0 = _cpu(tmp_path, 0, "performance powersave")
    cpu1 = _cpu(tmp_path, 1, "powersave performance")
    result = _run(tmp_path)
    assert result.returncode == 0
    assert (cpu0 / "scaling_governor").read_text(encoding="utf-8") == "performance\n"
    assert (cpu1 / "scaling_governor").read_text(encoding="utf-8") == "performance\n"
    assert result.stdout.strip() == "cpufreq: driver=acpi-cpufreq governor=performance"


def test_missing_performance_governor_is_left_unchanged_and_reported(tmp_path: Path):
    cpu0 = _cpu(tmp_path, 0, "powersave ondemand")
    result = _run(tmp_path)
    assert result.returncode == 0
    assert (cpu0 / "scaling_governor").read_text(encoding="utf-8") == "powersave\n"
    assert result.stdout.strip() == (
        "cpufreq: driver=acpi-cpufreq "
        "governor=performance unavailable; governor=powersave"
    )
