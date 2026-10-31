"""Recursive source discovery with floors measured before the #2239 moves."""

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_LIB = REPO_ROOT / "agent" / "lib"
MIN_AGENT_LIB_FILES = 121
MIN_INSTALLER_LIB_FILES = 7
MIN_SERVER_FILES = 155


def checked_files(directory: Path, pattern: str, minimum: int) -> list[Path]:
    files = sorted(p for p in directory.rglob(pattern) if p.is_file())
    assert len(files) >= minimum, (
        f"{directory}: found {len(files)} {pattern} files; expected >= {minimum}"
    )
    return files


def lib_files(directory: Path = AGENT_LIB) -> list[Path]:
    return checked_files(directory, "*.sh", MIN_AGENT_LIB_FILES)


def server_files(directory: Path = REPO_ROOT / "server") -> list[Path]:
    return checked_files(directory, "*.py", MIN_SERVER_FILES)


_BY_NAME: dict[str, Path] = {}
_FLAT: list[Path] = []


def lib_path(name: str) -> Path:
    """The repo path of one library by file name (#2239: topic folders)."""
    if not _BY_NAME:
        for path in lib_files():
            assert path.name not in _BY_NAME, f"duplicate library name: {path.name}"
            _BY_NAME[path.name] = path
    if name not in _BY_NAME:
        raise FileNotFoundError(f"{name}: no such library under {AGENT_LIB}")
    return _BY_NAME[name]


def flat_lib() -> Path:
    """The flat `$LIB_DIR` layout the agent runs with (and `data/`), built once per
    process by tools/agent-lib-flatten.sh -- the same script the initramfs build uses."""
    if not _FLAT:
        base = Path(tempfile.mkdtemp(prefix="agentlib-flat-"))
        atexit.register(shutil.rmtree, base, ignore_errors=True)
        dst = base / "lib"
        proc = subprocess.run(
            ["bash", (REPO_ROOT / "tools" / "agent-lib-flatten.sh").as_posix(),
             AGENT_LIB.as_posix(), dst.as_posix()],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"agent-lib-flatten failed: {proc.stderr}"
        shutil.copytree(AGENT_LIB / "data", dst / "data")
        _FLAT.append(dst)
    return _FLAT[0]
