"""Recursive source discovery with floors measured before the #2239 moves."""

import atexit
import re
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


AGENT_MAIN = REPO_ROOT / "agent" / "imagesrv-agent"
#: A library whose functions call another's at run time, so a test that loads it
#: must load that one too -- as the agent does. drawers.sh (restore_pipe) and
#: drawerfeed.sh (_drawer_pipeline) start `writeback_watch &` (writeback.sh, #1841).
#: Without it the job is "not found" and exits at once; its SIGCHLD lands while
#: busybox ash is blocked opening the drawer's fifo, ash does not retry that open
#: on EINTR (dash and bash do), and the drawer never starts (#2248, #2272).
#: failmark.sh and progress.sh call disk_serial (sysinfo.sh) -- on the failure path
#: (target_ident_keep, wipe_written_gpt, target_write_started), so a room without it
#: shows "disk_serial: not found" exactly where the evidence is written (#2281).
#: progress.sh's target_init calls target_ident_keep (failmark.sh): without it the
#: drawer's `seen` (serial|port|ata, #1602) is never written (#2289). The agent
#: loads failmark after progress; the call is at run time, so presence is the rule.
NEEDS = {"drawers": ("writeback",), "drawerfeed": ("writeback",),
         "failmark": ("sysinfo",), "progress": ("sysinfo", "failmark")}
_ORDER: list[str] = []


def agent_order() -> list[str]:
    """The libraries imagesrv-agent sources, in its own order (#2272)."""
    if not _ORDER:
        names = re.findall(r'\.\s+"\$LIB_DIR/([\w-]+)\.sh"', AGENT_MAIN.read_text(encoding="utf-8"))
        assert len(names) >= MIN_AGENT_LIB_FILES, f"{AGENT_MAIN}: only {len(names)} libraries sourced"
        assert len(set(names)) == len(names), f"{AGENT_MAIN}: a library is sourced twice"
        _ORDER.extend(names)
    return _ORDER


def agent_libs(*names: str) -> list[str]:
    """``names`` in imagesrv-agent's order. A name the agent does not source, or a
    library without what it calls at run time (``NEEDS``), is an error -- a test
    room that is not the agent's is how #2248 hid for weeks."""
    order = agent_order()
    unknown = sorted(set(names) - set(order))
    if unknown:
        raise ValueError(f"not sourced by {AGENT_MAIN.name}: {unknown}")
    missing = sorted({f"{n} needs {d}" for n in names for d in NEEDS.get(n, ()) if d not in names})
    if missing:
        raise ValueError(f"loaded without what imagesrv-agent loads with it: {missing}")
    return sorted(set(names), key=order.index)


def agent_sources(*names: str) -> str:
    """`. <lib>/<name>.sh; ` for each of ``names``, in imagesrv-agent's order, from
    the flat layout the agent runs with (``flat_lib``)."""
    lib = flat_lib().as_posix()
    return "".join(f". {lib}/{n}.sh; " for n in agent_libs(*names))
