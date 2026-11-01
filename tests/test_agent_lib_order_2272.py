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
"""

from __future__ import annotations

import ast
import re
import warnings
from pathlib import Path

import pytest

from agentlib import NEEDS, agent_libs, agent_order

TESTS = Path(__file__).resolve().parent

#: `. <path>/drawers.sh` written out in a test, not built by agent_sources.
LITERAL_SOURCE = re.compile(
    r"(?:^|[\s;{(\"'])\.\s+[\"']?[^\s;\"']*?\b(" + "|".join(map(re.escape, NEEDS)) + r")\.sh\b")


def _rooms(path: Path, names: set[str]) -> list[tuple[int, list[str]]]:
    """Every tuple or list literal in ``path`` made only of agent library names."""
    found = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)   # another file's escapes are not this scan's
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) > 1 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value in names
                for e in node.elts):
            found.append((node.lineno, [e.value for e in node.elts]))
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


def test_every_room_loads_writeback_with_the_drawers():
    assert offenders() == []


def test_the_scan_sees_both_shapes(tmp_path):
    """The guard's own control: each shape that hid #2248 is caught, and the
    agent's shape passes."""
    for n in range(601):
        (tmp_path / f"test_pad_{n}.py").write_text("")
    (tmp_path / "test_path.py").write_text(
        'X = f". {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; "\n')
    (tmp_path / "test_names.py").write_text('LIBS = ("common", "restore", "drawers", "drawerfeed")\n')
    (tmp_path / "test_ok.py").write_text('LIBS = ("common", "restore", "writeback", "drawers")\n'
                                         'D = ("drawers", "2", "2", "0")   # a progress row, not a room\n')
    got = offenders(tmp_path)
    assert got == ["test_names.py:1: loads drawers without writeback",
                   "test_names.py:1: loads drawerfeed without writeback",
                   "test_path.py:1: sources drawers.sh by path -- use agent_sources",
                   "test_path.py:1: sources drawerfeed.sh by path -- use agent_sources"], got


def test_the_agent_loads_writeback_before_the_drawers():
    """NEEDS is the agent's own shape, not a test's idea of it."""
    order = agent_order()
    for lib, needs in NEEDS.items():
        assert all(order.index(d) < order.index(lib) for d in needs), (lib, needs)


def test_agent_libs_keeps_the_agent_order_and_refuses_a_lone_drawer():
    assert agent_libs("drawers", "common", "writeback", "restore") == ["common", "restore", "writeback", "drawers"]
    with pytest.raises(ValueError, match="drawerfeed needs writeback"):
        agent_libs("common", "drawerfeed")
    with pytest.raises(ValueError, match="not sourced"):
        agent_libs("common", "no-such-lib")
