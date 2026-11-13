"""#2458 -- ``session.target_disks`` is what the console showed **when the operator chose**.

#2448 built ``target_disks`` from the inventory of the same hello that carried it. That
checks the agent against itself, not against the choice: a drawer swapped between the
choice in the console and the next hello, on a cloner that stayed on, was "what the
console shows" by then, and was written.

Now ``_validate_target_slots`` keeps, for each chosen slot, the serial and size of the
stored inventory the console draws its cards from (``target_slots[].shown``) at the
moment the round opens. In the wave the choice opened (wave 1) the hello sends that,
not the fresh inventory -- and the agent's ``drawers_bound`` fails the swapped drawer by
name ("המגירה הוחלפה אחרי שנבחרה"), not written. From wave 2 on drawers are swapped on
purpose and nothing new was chosen: the hello's inventory, as in #2448. A wave-1 item
with no ``shown`` (a round opened before #2458) is not "the same disk": ``null`` per
slot, not written in that wave (principle 5).

Negative control: server/rounds/room_state.py, server/rounds/room.py and
server/agent/hello.py back to the base together (#439) -- see the PR.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1,
    cloner_hello,
    room_body,
    room_server,
)
from test_target_identity_2448 import SWAPPED_HE, _resolve, jq

SIZE = 256060514304          # cloner_hello's size_bytes
SECTORS = SIZE // 512


def _open(room_server, serials=("S1", "S2")):
    deploy, anon = room_server["deploy"], room_server["anon"]
    cloner_hello(anon, CLONER1, list(serials))       # awake when chosen: no #1954 `expect`
    opened = deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: list(serials)}))
    assert opened.status_code == 200, opened.text
    return deploy, anon


def _hello_disks(anon, disks):
    from conftest import hello_body               # noqa: PLC0415
    body = hello_body(CLONER1)
    body.pop("disks", None)
    if disks is not None:
        body["disks"] = disks
    response = anon.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()["session"]


def test_a_drawer_swapped_after_the_choice_is_sent_as_the_one_chosen(room_server):
    """The Issue's scenario: S1 chosen in slot 1; by the next hello slot 1 holds S9."""
    _deploy, anon = _open(room_server)
    session = cloner_hello(anon, CLONER1, ["S9", "S2"])["session"]
    assert session["target_ports"] == [1, 2], "unchanged: the slot is still chosen"
    assert session["target_disks"] == [{"port": 1, "serial": "S1", "size_bytes": SIZE},
                                       {"port": 2, "serial": "S2", "size_bytes": SIZE}]


def test_the_size_is_the_one_shown_too(room_server):
    _deploy, anon = _open(room_server)
    disk = {"dev": "sda", "port": 1, "serial": "S1", "size_bytes": SIZE * 2}
    session = _hello_disks(anon, [disk])
    assert session["target_disks"][0] == {"port": 1, "serial": "S1", "size_bytes": SIZE}


def test_a_hello_without_an_inventory_still_gets_what_was_shown(room_server):
    """Before: no inventory in the hello = no field, and the agent went by the slot alone."""
    _deploy, anon = _open(room_server)
    session = _hello_disks(anon, None)
    assert session["target_disks"] == [{"port": 1, "serial": "S1", "size_bytes": SIZE},
                                       {"port": 2, "serial": "S2", "size_bytes": SIZE}]


def test_the_snapshot_is_kept_on_the_round(room_server):
    deploy, _anon = _open(room_server)
    slots = deploy.get("/api/console/room").json()["round"]["target_slots"]
    assert slots[0]["shown"] == [{"port": 1, "serial": "S1", "size_bytes": SIZE},
                                 {"port": 2, "serial": "S2", "size_bytes": SIZE}]


def test_from_wave_2_drawers_are_swapped_on_purpose(room_server):
    """Wave 2 writes the drawers put in after wave 1: the hello's inventory, as in #2448."""
    _deploy, anon = _open(room_server)
    conn = room_server["ctx"].conn
    conn.execute("UPDATE room_rounds SET wave_number = 2")
    conn.commit()
    session = cloner_hello(anon, CLONER1, ["S9", "S2"])["session"]
    assert session["target_disks"][0] == {"port": 1, "serial": "S9", "size_bytes": SIZE}


def test_a_round_from_before_the_fix_is_not_the_same_disk(room_server):
    """Wave 1 of a round opened before #2458 (no `shown`): nothing was kept of what was
    shown -- null per slot, the agent does not write it in this wave (principle 5)."""
    _deploy, anon = _open(room_server)
    conn = room_server["ctx"].conn
    row = conn.execute("SELECT target_slots_json FROM room_rounds").fetchone()
    slots = json.loads(row["target_slots_json"])
    for item in slots:
        item.pop("shown")
    conn.execute("UPDATE room_rounds SET target_slots_json = ?", (json.dumps(slots),))
    conn.commit()
    session = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert session["target_disks"] == [{"port": 1, "serial": None, "size_bytes": None},
                                       {"port": 2, "serial": None, "size_bytes": None}]


@jq
@pytest.mark.parametrize("now,written", [("S1", True), ("S9", False)])
def test_server_to_agent_the_swapped_drawer_is_not_written(room_server, tmp_path, now, written):
    """The whole chain: the server's hello answer, after the choice, into the agent's
    resolve_target_drawers -> target_init -> drawers_bound. Slot 1 holds `now`."""
    _deploy, anon = _open(room_server)
    session = cloner_hello(anon, CLONER1, [now, "S2"])["session"]
    session = {**session, "target_ports": [1],
               "target_disks": [d for d in session["target_disks"] if d["port"] == 1]}
    out = _resolve(tmp_path, session, disks={"sdb": (now, 1, SECTORS)},
                   script='target_init sdb 1000; echo "out=$(drawers_bound $_disks)"')
    assert out.strip().endswith("out=sdb" if written else "out="), out
    if not written:
        assert SWAPPED_HE in (tmp_path / "box/run/targets/sdb/error").read_text(encoding="utf-8")
