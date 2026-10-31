"""#1410 -- the agent's own refusal: a room round without a drawer selection writes nothing.

The server refuses ``POST /api/room`` without ``target_slots``
(``test_server_room.py::test_room_without_explicit_target_slots_is_rejected_without_a_row``).
This is the Issue's second layer, for an old server that still sends
``session.target_ports: null``: before f3319924 ``resolve_target_drawers`` read
``null`` as "every drawer" (``_disks=$(list_drawers)``); now it stops in
``die_local`` and no drawer is chosen.

Negative control (mutation): the ``null`` branch as it was -- ``_disks=$(list_drawers)``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH), "jq")


def resolve(tmp_path, session: dict) -> str:
    resp = tmp_path / "response.json"
    resp.write_text(json.dumps({"schema": 1, "known": True, "session": session}), encoding="utf-8")
    script = (
        f'export RUN_DIR={posix(tmp_path)!r} RESP={posix(resp)!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/gui/clonergui.sh; '
        'die_local() { echo "DIE: $*"; exit 0; }; '
        'list_drawers() { echo sdb; echo sdc; }; '
        'disk_port() { case "$1" in sdb) echo 1 ;; sdc) echo 2 ;; esac; }; '
        'resolve_target_drawers; echo "DISKS:$(echo $_disks)"'
    )
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.mark.parametrize("session", [
    {"id": "ses_1", "target_ports": None},
    {"id": "ses_1"},                          # the field absent reads as null
    {"id": "ses_1", "target_ports": []},
])
def test_no_selection_stops_and_chooses_no_drawer(tmp_path, session):
    out = resolve(tmp_path, session)
    assert "DIE: room target selection is missing" in out, out
    assert "DISKS:" not in out, out


def test_an_explicit_selection_chooses_exactly_those_drawers(tmp_path):
    """Guard (passes in both states): the refusal is not of every round."""
    out = resolve(tmp_path, {"id": "ses_1", "target_ports": [2]})
    assert "DIE" not in out and "DISKS:sdc" in out, out
