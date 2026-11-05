"""#2448 -- the server's half: ``session.target_disks``, the disk in each slot the room chose.

``session.target_ports`` carries slot numbers only, so the agent could not compare the
disk it binds at ``target_init`` with what the console showed the operator. The hello
answer of a cloner in a room wave now carries, next to it, ``target_disks``: for each
slot of ``target_ports``, the serial and size that **this hello's own inventory** puts
there -- the inventory the console draws its cards from. A slot with no disk in it, or
two disks claiming it, or no serial, is ``null`` there: the agent finds no "same disk"
in it and does not write (principle 5). An old agent ignores the field; ``target_ports``
is unchanged (``schema`` stays 1).

Negative control: server/rounds/room.py and server/agent/hello.py back to the base
together with the agent files (#439) -- see the commit message.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1,
    cloner_hello,
    room_body,
    room_server,
)

from server.rounds import room

SIZE = 256060514304          # cloner_hello's size_bytes


def test_the_hello_answer_carries_each_chosen_slots_disk(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    session = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]
    assert session["target_ports"] == [1, 2], "unchanged: the old agent's field"
    assert session["target_disks"] == [{"port": 1, "serial": "S1", "size_bytes": SIZE},
                                       {"port": 2, "serial": "S2", "size_bytes": SIZE}]


def test_the_field_follows_this_hellos_inventory(room_server):
    """The disk now in slot 1 is S9: that is what the console shows, and what is sent --
    the agent then compares it with what it binds at target_init."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])     # awake when chosen: no #1954 `expect` to drop the slot
    deploy.post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    session = cloner_hello(anon, CLONER1, ["S9", "S2"])["session"]
    assert session["target_disks"][0] == {"port": 1, "serial": "S9", "size_bytes": SIZE}


def _disk(port, serial, size=SIZE, dev="sda"):
    return {"dev": dev, "port": port, "serial": serial, "size_bytes": size}


@pytest.mark.parametrize("ports,disks,expected", [
    ([1, 2], [_disk(1, "S1"), _disk(2, "S2", dev="sdb")],
     [{"port": 1, "serial": "S1", "size_bytes": SIZE}, {"port": 2, "serial": "S2", "size_bytes": SIZE}]),
    ([2], [_disk(1, "S1")], [{"port": 2, "serial": None, "size_bytes": None}]),             # slot empty
    ([1], [_disk(1, "S1"), _disk(1, "S2", dev="sdb")],
     [{"port": 1, "serial": None, "size_bytes": None}]),                                    # two claim it
    ([1], [_disk(1, None)], [{"port": 1, "serial": None, "size_bytes": SIZE}]),            # no serial
    ([1], [_disk(1, "")], [{"port": 1, "serial": None, "size_bytes": SIZE}]),
    ([1], [_disk(1, "S1", size=0)], [{"port": 1, "serial": "S1", "size_bytes": None}]),    # unmeasured
    ([1], [_disk(1, "S1", size=True)], [{"port": 1, "serial": "S1", "size_bytes": None}]),
    ([1], [_disk(True, "S1")], [{"port": 1, "serial": None, "size_bytes": None}]),         # not a slot
    ([], [_disk(1, "S1")], []),
])
def test_target_disks(ports, disks, expected):
    assert room.target_disks(ports, disks) == expected


@pytest.mark.parametrize("ports,disks", [(None, [_disk(1, "S1")]), ([1], None), ([1], "junk")])
def test_nothing_to_send_is_none(ports, disks):
    """A round from before the selection, or a hello without an inventory: the agent
    goes on by the slot alone, as before #2448."""
    assert room.target_disks(ports, disks) is None
