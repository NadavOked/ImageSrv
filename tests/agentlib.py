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
#: must load that one too -- as the agent does. drawers.sh (restore_pipe)
#: starts `writeback_watch &` (writeback.sh, #1841).
#: Without it the job is "not found" and exits at once; its SIGCHLD lands while
#: busybox ash is blocked opening the drawer's fifo, ash does not retry that open
#: on EINTR (dash and bash do), and the drawer never starts (#2248, #2272).
#: failmark.sh and progress.sh call disk_serial (diskinfo.sh, loaded alongside sysinfo.sh) -- on the failure path
#: (target_ident_keep, wipe_written_gpt, target_write_started), so a room without it
#: shows "disk_serial: not found" exactly where the evidence is written (#2281).
#: progress.sh's target_init calls target_ident_keep (failmark.sh): without it the
#: drawer's `seen` (serial|port|ata, #1602) is never written (#2289). The agent
#: loads failmark after progress; the call is at run time, so presence is the rule.
NEEDS = {"drawers": ("writeback",), "neighborfill": ("waits",),   # wait_pid, the fill's ceiling
         "chunkfeed": ("waits",),   # B5: wait_pid, for its curl
         "peerfill": ("neighborfill", "waits"),   # B29: its list, piece, ceiling and re-read helpers
         "peerwire": ("peerfill", "neighborfill", "waits"),   # B29: what the answers say, in the flow (#3064)
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
    # sysinfo and its disk collector are loaded together by every caller (#2385).
    if "sysinfo" in names and "diskinfo" not in names:
        names = (*names, "diskinfo")
    # #3098: failmark's GPT wipe runs under capped_run (waits.sh) -- loaded with it, the agent's way, in
    # every room that loads failmark (some 60 of them load it without waits: the failure path's own rooms).
    if "failmark" in names and "waits" not in names:
        names = (*names, "waits")
    # #2991: lc_inventory moved from lcinv to lcscan, and runs each drawer under capped_run (waits.sh) --
    # loaded with lcinv, the agent's way, in every room that loads lcinv (some 20 of them).
    # An agent without lcscan (before #2991, the negative control) is loaded as it was.
    if "lcinv" in names and "lcscan" in agent_order():
        names = (*names, *(n for n in ("lcscan", "waits") if n not in names))
    order = agent_order()
    # #2529: assert_target and target_bind (failmark.sh) ask targetwwn.sh for the WWN -- loaded with failmark, the
    # agent's way. An agent without targetwwn (before #2529, the negative control) is loaded as it was.
    if "failmark" in names and "targetwwn" in order and "targetwwn" not in names:
        names = (*names, "targetwwn")
    # #3632: netprobe_json's one background job and its whole-JSON check live in netcache.sh -- loaded with netprobe,
    # the agent's way (netprobe.sh stays under 120 lines). Only where the agent sources it.
    if "netprobe" in names and "netcache" in order and "netcache" not in names:
        names = (*names, "netcache")
    # #3064: lc_run, lc_gui_state and lc_prepare_target ask lchdd.sh (the local clone's HDD rule), the
    # agent's way; a room that loads any of them loads it. Only where the agent sources it.
    if {"lccopy", "localclone", "lcgui"} & set(names) and "lchdd" in order and "lchdd" not in names:
        names = (*names, "lchdd")
    # #3548: every background job of these starts with bg_detach_fds (bgfds.sh) -- loaded with them, the agent's way.
    # An agent without bgfds (before #3548, the negative control) is loaded as it was.
    if {"attended", "hold", "monitor", "roundbeep", "netcache", "probe_bg"} & set(names) and "bgfds" in order and "bgfds" not in names:
        names = (*names, "bgfds")
    # #2547: shrink_plan and the shrink measure ext4/btrfs with shrinkext4.sh/shrinkbtrfs.sh -- loaded with them, the
    # agent's way. Only where the agent sources them (before #2547, the negative control, they are not there).
    if {"shrinkplan", "shrink"} & set(names):
        names = (*names, *(n for n in ("shrinkext4", "shrinkbtrfs") if n in order and n not in names))
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
