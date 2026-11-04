"""#893: the `-c` guard on the harness itself, and the measurement it rests on.

Git for Windows' bash keeps 8,186 characters of a `-c` script, runs the rest
of nothing, and exits 0. These tests pin three things: where the guard is
installed (Windows only, and there it is the limit that is real), that it reads
every spelling of `-c` the suite uses, and that the file road keeps a script
whole on both platforms.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import msys_guard
from native import requires_native

BASH = shutil.which("bash")

pytestmark = requires_native(("bash", BASH), why="the guard is about bash")


def ends_with(n: int, filler: str = "a") -> str:
    """A script of exactly `n` characters whose last command prints END."""
    tail = "\necho END"
    return ": " + filler * (n - 2 - len(tail)) + tail


def test_ends_with_is_exactly_as_long_as_asked():
    assert len(ends_with(8186)) == 8186
    assert ends_with(8186).endswith("echo END")


@pytest.mark.parametrize("argv", [
    ["bash", "-c", "S"],
    [r"C:\Program Files\Git\usr\bin\bash.EXE", "-c", "S", "name", "arg"],
    ["/bin/sh", "-euc", "S"],
    ["bash", "-o", "pipefail", "-c", "S"],
    ["bash", "--norc", "-e", "-c", "S"],
    ["busybox", "ash", "-c", "S"],
    [Path("/usr/bin/dash"), "-c", "S"],
])
def test_every_spelling_of_dash_c_is_read(argv):
    assert msys_guard.c_script(argv) == "S"


@pytest.mark.parametrize("argv", [
    ["bash", "script.sh", "-c"],          # a file; -c is the script's own argument
    ["python", "-c", "S"],
    ["bash", "-c"],                       # no script at all
    "bash -c S",                          # shell=True: one string, not argv
    ["busybox", "sha256sum", "-c", "S"],
    [],
])
def test_what_is_not_a_shell_c_script_is_left_alone(argv):
    assert msys_guard.c_script(argv) is None


def test_the_check_fails_past_the_limit_on_windows_and_nowhere_else():
    msys_guard.check([BASH, "-c", ends_with(msys_guard.MSYS_C_LIMIT)], platform="nt")
    with pytest.raises(pytest.fail.Exception, match="#893"):
        msys_guard.check([BASH, "-c", ends_with(msys_guard.MSYS_C_LIMIT + 1)], platform="nt")
    msys_guard.check([BASH, "-c", ends_with(50_000)], platform="posix")


def _unguarded(script: str) -> str:
    """bash -c on the real Popen, around the guard -- what the bash here does."""
    proc = subprocess.Popen.__new__(subprocess.Popen)
    msys_guard._original_init(proc, [BASH, "-c", script], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", errors="replace")
    out, _ = proc.communicate(timeout=60)
    return out.strip()


def test_the_guard_is_installed_exactly_where_bash_cuts_the_script():
    """On Windows: installed, and the limit is the measured one -- the last
    character that survives is the 8,186th. On Linux: not installed, and a
    12,000-character `-c` script runs to its end (the Issue's unverified
    'Linux is unaffected', measured by this test on every Linux run)."""
    limit = msys_guard.MSYS_C_LIMIT
    if os.name == "nt":
        assert msys_guard.installed(), "conftest did not install the #893 guard"
        assert _unguarded(ends_with(limit)) == "END"
        assert _unguarded(ends_with(limit + 1)) != "END"
        before = len(msys_guard.blocked)
        with pytest.raises(pytest.fail.Exception, match="#893"):
            subprocess.run([BASH, "-c", ends_with(12_000)], stdin=subprocess.DEVNULL,
                           capture_output=True)
        assert len(msys_guard.blocked) == before + 1
    else:
        assert not msys_guard.installed()
        done = subprocess.run([BASH, "-c", ends_with(12_000)], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True)
        assert done.returncode == 0 and done.stdout.strip() == "END", done


@pytest.mark.parametrize("filler", ["a", "\u05d0"])
def test_a_script_file_runs_whole_at_any_length(tmp_path, filler):
    done = msys_guard.run_script_file(BASH, ends_with(40_000, filler), tmp_path / "s.sh",
                                      capture_output=True, encoding="utf-8",
                                      errors="replace", timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "END"
