"""#2239 שלב 0: השומרים שמונעים שבירה שקטה כשמזיזים קבצים לתתי-תיקיות.

כל אחד מהם נבדק כאן גם בצורה השבורה שלו (בקרה שלילית): סריקה שמצאה פחות
מהרצפה נופלת, ושיטוח ש"הצליח" בלי להעתיק הכול נופל — עיקרון 5.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from agentlib import MIN_SERVER_FILES, REPO_ROOT, checked_files, lib_files, server_files
from native import requires_native
from test_agent import BASH, posix

FLATTEN = REPO_ROOT / "tools" / "agent-lib-flatten.sh"
needs_bash = requires_native(("bash", BASH), posix=True, why="tools/agent-lib-flatten.sh")


# --- הרצפות --------------------------------------------------------------------

def test_a_scan_below_the_floor_fails_instead_of_passing_on_nothing(tmp_path):
    (tmp_path / "one.sh").write_text("true\n")
    with pytest.raises(AssertionError, match="expected >= 2"):
        checked_files(tmp_path, "*.sh", 2)
    with pytest.raises(AssertionError, match="found 0"):
        checked_files(tmp_path / "missing", "*.sh", 1)


def test_the_scans_are_recursive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.sh").write_text("true\n")
    (tmp_path / "sub" / "nested.sh").write_text("true\n")
    found = [p.name for p in checked_files(tmp_path, "*.sh", 2)]
    assert found == ["nested.sh", "top.sh"]


def test_the_real_tree_meets_the_floors():
    assert len(lib_files()) >= 121
    assert len(server_files()) >= MIN_SERVER_FILES


# --- server/paths.py הוא המקום היחיד שמחשב נתיבים מחוץ לתיקייה של הקובץ ------

ESCAPES = re.compile(r"__file__.*(parents\s*\[|\.parent\s*\.parent)")


def _code_lines(path: Path):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if ESCAPES.search(code):
            yield number, line.strip()


def test_only_server_paths_computes_paths_outside_its_own_directory():
    offenders = [f"{p.relative_to(REPO_ROOT).as_posix()}:{n}: {text}"
                 for p in sorted((REPO_ROOT / "server").rglob("*.py"))
                 if p.name != "paths.py" or p.parent.name != "server"
                 for n, text in _code_lines(p)]
    assert offenders == [], "\n".join(offenders)


def test_the_paths_guard_catches_an_escape(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "# Path(__file__).parents[2] in a comment does not count\n", encoding="utf-8")
    assert [n for n, _ in _code_lines(bad)] == [2]


# --- tools/agent-lib-flatten.sh ------------------------------------------------

def _flatten(src: Path, dst: Path):
    return subprocess.run([BASH, posix(FLATTEN), posix(src), posix(dst)], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60)


@needs_bash
def test_flatten_copies_nested_files_flat_and_byte_identical(tmp_path):
    src, dst = tmp_path / "lib", tmp_path / "out"
    (src / "a").mkdir(parents=True)
    (src / "top.sh").write_bytes(b"echo top\n")
    (src / "a" / "nested.sh").write_bytes(b"echo nested\n\xd7\xa9\n")
    r = _flatten(src, dst)
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in dst.iterdir()) == ["nested.sh", "top.sh"]
    assert (dst / "nested.sh").read_bytes() == (src / "a" / "nested.sh").read_bytes()


@needs_bash
def test_flatten_refuses_a_duplicate_basename_before_copying_anything(tmp_path):
    src, dst = tmp_path / "lib", tmp_path / "out"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir()
    (src / "a" / "same.sh").write_text("echo a\n")
    (src / "b" / "same.sh").write_text("echo b\n")
    (src / "other.sh").write_text("echo other\n")
    r = _flatten(src, dst)
    assert r.returncode != 0 and "duplicate basename: same.sh" in r.stderr, r.stderr
    assert not dst.exists() or list(dst.iterdir()) == []


@needs_bash
def test_flatten_refuses_a_destination_that_already_has_scripts(tmp_path):
    src, dst = tmp_path / "lib", tmp_path / "out"
    src.mkdir()
    dst.mkdir()
    (src / "x.sh").write_text("echo x\n")
    (dst / "stale.sh").write_text("echo stale\n")
    r = _flatten(src, dst)
    assert r.returncode != 0 and "already contains shell files" in r.stderr, r.stderr


@needs_bash
def test_flatten_refuses_an_empty_source(tmp_path):
    src = tmp_path / "lib"
    src.mkdir()
    r = _flatten(src, tmp_path / "out")
    assert r.returncode != 0 and "no shell files" in r.stderr, r.stderr
