"""#2201 -- a selected drawer with no disk on the machine fails alone; the rest are written.

Metal 25/10, cloner 2, round ``room_edc35340``: drawer 5 (a Toshiba) was taken out
of the kernel a second after the round opened, and ``resolve_target_drawers`` --
"every selected port maps to exactly one disk" -- caught the **zero** matches too:
``die_local`` -> failure screen -> ``reboot -f``, and drawer 2, ready and mapped
without doubt, was not written.

Decision 03/11 (option b): zero matches is no ambiguity -- the agent writes the
others; two or more on one port, and a missing/malformed selection, stay
``die_local``.

Negative control: ``agent/lib/restore/drawerselect.sh`` is new (the function moved
out of ``clonergui.sh``, which was at 298 of 300), so the control restores
**both** production files to the base: ``clonergui.sh`` gets the function back and
``drawerselect.sh`` is removed -- and the test loads the function from wherever the
agent sources it (``imagesrv-agent``, restored too).
"""

from __future__ import annotations

import json
import re
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH), "jq")


def _lib_of_resolve() -> str:
    """The file under agent/lib that defines resolve_target_drawers -- found, not
    named, so the same test runs against the code before the move."""
    hits = [p for p in (AGENT / "lib").rglob("*.sh")
            if re.search(r"^resolve_target_drawers\(\)", p.read_text(encoding="utf-8"), re.M)]
    assert len(hits) == 1, hits
    return posix(hits[0])


def resolve(tmp_path, ports, disks: dict[str, int]) -> dict:
    """ports: session.target_ports as sent; disks: dev -> its port on this machine."""
    resp = tmp_path / "response.json"
    resp.write_text(json.dumps({"schema": 1, "known": True,
                                "session": {"id": "ses_1", "target_ports": ports}}), encoding="utf-8")
    log = tmp_path / "agent.log"
    cases = " ".join(f"{d}) echo {p} ;;" for d, p in disks.items())
    script = (
        f'export RUN_DIR={posix(tmp_path)!r} RESP={posix(resp)!r} LOG_FILE={posix(log)!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {_lib_of_resolve()}; '
        'die_local() { echo "DIE: $*"; exit 0; }; '
        f'list_drawers() {{ printf "%s\\n" {" ".join(disks) or "''"}; }}; '
        f'disk_port() {{ case "$1" in {cases} esac; }}; '
        'resolve_target_drawers; echo "DISKS:$(echo $_disks)"'
    )
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return {"out": out.stdout,
            "log": log.read_text(encoding="utf-8") if log.exists() else ""}


def test_room_edc35340_drawer_5_gone_drawer_2_is_written(tmp_path):
    """Ports 2 and 5 selected; only port 2 has a disk. Before: DIE, nothing written."""
    r = resolve(tmp_path, [2, 5], {"sdb": 2})
    assert "DIE" not in r["out"], r
    assert "DISKS:sdb" in r["out"], r
    assert "selected drawer 5 has no disk on this machine" in r["log"], r


def test_the_missing_drawer_can_be_any_of_them(tmp_path):
    """The first one missing, not only the last: the loop goes on to the next port."""
    r = resolve(tmp_path, [1, 2, 5], {"sdc": 2, "sdd": 5})
    assert "DIE" not in r["out"] and "DISKS:sdc sdd" in r["out"], r


def test_two_disks_on_one_selected_port_still_stop(tmp_path):
    """The ambiguous mapping is unchanged: never guess which of two disks to write."""
    r = resolve(tmp_path, [1, 2], {"sdb": 1, "sdc": 2, "sdd": 2})
    assert "DIE: selected drawer 2 does not map to exactly one disk" in r["out"], r
    assert "DISKS:" not in r["out"], r


def test_no_selected_drawer_here_at_all_still_stops(tmp_path):
    """Nothing selected is on this machine: nothing to go on with. (Before the
    fix the words were "does not map to exactly one disk"; the stop is the same.)"""
    r = resolve(tmp_path, [5], {"sdb": 1})
    assert "DIE: " in r["out"], r
    assert "DISKS:" not in r["out"], r


@pytest.mark.parametrize("ports", [None, [], "2,5", [2.5], ["2"]])
def test_a_missing_or_malformed_selection_still_stops(tmp_path, ports):
    """Never "every disk" (#1410): null, empty, or not an array of whole numbers."""
    r = resolve(tmp_path, ports, {"sdb": 2, "sdc": 5})
    assert "DIE: room target selection is missing" in r["out"], r
    assert "DISKS:" not in r["out"], r


def test_the_agent_sources_the_file_that_defines_it():
    """The function is in the initramfs: every agent/lib file is flattened in
    (tools/agent-lib-flatten.sh), and imagesrv-agent sources this one by name."""
    lib = _lib_of_resolve().rsplit("/", 1)[1]
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert f'. "$LIB_DIR/{lib}"' in main, lib
