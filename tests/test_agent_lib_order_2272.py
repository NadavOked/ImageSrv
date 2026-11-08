"""A test room loads the agent's libraries as the agent does (#2272).

drawers.sh and drawerfeed.sh start `writeback_watch &` (writeback.sh, #1841) in
every partition's pipe. A test that sourced them without writeback.sh ran a
pipe the agent never runs: the job was "not found", exited at once, and its
SIGCHLD landed while busybox ash was blocked opening the drawer's fifo. ash
does not retry that open on EINTR, the drawer never started, and the room
waited out its stall ceiling (#2248, c516dc43 -- found there, fixed in one file).

So a room is built by `agentlib.agent_sources` (or `agent_libs` for a
`$LIB_DIR` loop): imagesrv-agent's order, and an error on a library loaded
without what it calls. This file holds every test file to that.

#2281: the same family on the failure path -- failmark.sh and progress.sh call
disk_serial (diskinfo.sh), and rooms that loaded them without it marked a failed
drawer with "disk_serial: not found": not the evidence the agent would write.

#2289: progress.sh's target_init calls target_ident_keep (failmark.sh). Rooms
that loaded progress without failmark never wrote the drawer's `seen`
(serial|port|ata, #1602) -- "target_ident_keep: not found", rc 0.
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from agentlib import NEEDS, agent_libs, agent_order, agent_sources
from native import requires_native
from test_agent import BASH, posix
from test_timeouts import run_sh

TESTS = Path(__file__).resolve().parent

#: `. <path>/drawers.sh` written out in a test, not built by agent_sources.
LITERAL_SOURCE = re.compile(
    r"(?:^|[\s;{(\"'])\.\s+[\"']?[^\s;\"']*?\b(" + "|".join(map(re.escape, NEEDS)) + r")\.sh\b")


def _rooms(path: Path, names: set[str]) -> list[tuple[int, list[str]]]:
    """Every tuple or list literal in ``path`` made only of agent library names, and
    every `agent_sources(...)` / `agent_libs(...)` call by its literal names (#2281:
    18 rooms were call arguments, and only a run of the test itself saw them)."""
    found = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)   # another file's escapes are not this scan's
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) > 1 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value in names
                for e in node.elts):
            found.append((node.lineno, [e.value for e in node.elts]))
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in (
                "agent_sources", "agent_libs"):
            got = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if got:
                found.append((node.lineno, got))
    return found


def offenders(directory: Path = TESTS) -> list[str]:
    names = set(agent_order())
    bad = []
    files = sorted(p for p in directory.glob("*.py") if p.name not in {"agentlib.py", Path(__file__).name})
    assert len(files) > 600, f"{directory}: only {len(files)} test files -- the scan found nothing to hold"
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for lib in LITERAL_SOURCE.findall(line):
                bad.append(f"{path.name}:{no}: sources {lib}.sh by path -- use agent_sources")
        for no, room in _rooms(path, names):
            for lib, needs in NEEDS.items():
                lost = [d for d in needs if lib in room and d not in room]
                if lost:
                    bad.append(f"{path.name}:{no}: loads {lib} without {', '.join(lost)}")
    return bad


def test_every_room_loads_what_its_libraries_call():
    assert offenders() == []


def test_the_scan_sees_both_shapes(tmp_path):
    """The guard's own control: each shape that hid #2248 and #2281 is caught,
    and the agent's shape passes."""
    for n in range(601):
        (tmp_path / f"test_pad_{n}.py").write_text("")
    (tmp_path / "test_path.py").write_text(
        'X = f". {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; "\n')
    (tmp_path / "test_names.py").write_text('LIBS = ("common", "restore", "drawers", "drawerfeed")\n')
    (tmp_path / "test_failpath.py").write_text(   # #2281: the failure path's own room
        'R = ("common", "progress", "failmark")\n'
        'X = f". {posix(AGENT)}/lib/core/progress.sh; "\n'
        'Y = agent_sources("common", "waits", "progress")\n'   # a room as call arguments
        'Z = agent_sources("common", "sysinfo", "progress")\n')   # #2289: target_init without failmark
    (tmp_path / "test_ok.py").write_text('LIBS = ("common", "restore", "writeback", "drawers")\n'
                                         'R = ("common", "sysinfo", "progress", "failmark")\n'
                                         'Y = agent_sources("common", "sysinfo", "progress", "failmark")\n'
                                         'D = ("drawers", "2", "2", "0")   # a progress row, not a room\n')
    got = offenders(tmp_path)
    assert got == ["test_failpath.py:2: sources progress.sh by path -- use agent_sources",
                   "test_failpath.py:1: loads failmark without sysinfo",
                   "test_failpath.py:1: loads progress without sysinfo",
                   "test_failpath.py:3: loads progress without sysinfo, failmark",
                   "test_failpath.py:4: loads progress without failmark",
                   "test_names.py:1: loads drawers without writeback",
                   "test_names.py:1: loads drawerfeed without writeback",
                   "test_path.py:1: sources drawers.sh by path -- use agent_sources",
                   "test_path.py:1: sources drawerfeed.sh by path -- use agent_sources"], got


def test_the_agent_loads_what_needs_says():
    """NEEDS is the agent's own shape, not a test's idea of it: every library it
    names is one the agent sources. The calls are at run time, so the order is not
    the rule -- the agent loads failmark after progress (#2289)."""
    order = agent_order()
    for lib, needs in NEEDS.items():
        assert lib in order and all(d in order for d in needs), (lib, needs)


@requires_native(("bash", BASH))
def test_target_init_in_a_room_writes_the_drawer_s_seen(tmp_path):
    """#2289: the room the agent runs keeps the drawer's identity at target_init;
    a room of progress without failmark is refused before it runs."""
    run = tmp_path / "run"
    out = run_sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path)!r}; "
                 + agent_sources("common", "sysinfo", "progress", "failmark")
                 + "target_init sda 100; echo finished")
    assert out.strip().endswith("finished") and "not found" not in out, out
    assert (run / "targets" / "sda" / "seen").read_text(encoding="utf-8") == "||\n"
    with pytest.raises(ValueError, match="progress needs failmark"):
        agent_libs("common", "sysinfo", "progress")


def test_agent_libs_keeps_the_agent_order_and_refuses_a_lone_drawer():
    assert agent_libs("drawers", "common", "writeback", "restore") == ["common", "restore", "writeback", "drawers"]
    with pytest.raises(ValueError, match="drawerfeed needs writeback"):
        agent_libs("common", "drawerfeed")
    with pytest.raises(ValueError, match="failmark needs sysinfo.*progress needs sysinfo"):   # #2281
        agent_libs("common", "progress", "failmark")
    with pytest.raises(ValueError, match="not sourced"):
        agent_libs("common", "no-such-lib")
