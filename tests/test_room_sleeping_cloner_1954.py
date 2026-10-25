"""#1954 -- "התחל סבב" with a cloner that is off (Nadav 22/10).

The console greyed out every disk of a sleeping cloner (``roomPickSlots``: ``ok: awake
&& …``) while the window said "מחשבים כבויים יידלקו דרך WoL". Now:

* the console lets the operator tick the disks the cloner reported last, saying
  "יידלק ב-WoL" on each;
* the server binds each of them to its serial (``expect`` in ``target_slots_json``)
  -- it decides "asleep" itself, from ``net_devices.last_seen``;
* the cloner's first hello in the round decides once per bound slot: the same disk
  is written as before; another disk or an empty slot is skipped in the open --
  out of the selection, the round's target one less, ``room_drawer_wake_mismatch`` in
  the journal, ``wake_skipped`` in the round for the console. Never another disk.
* a hello without an inventory decides nothing, and writes no bound slot.
* an awake cloner is not bound: ports, as before (#1790's re-waves keep working).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from native import requires_native  # noqa: E402
from test_server_room import CLONER1, CLONER2, _open_with_slots, cloner_hello, room, room_server  # noqa: E402,F401

REPO = Path(__file__).resolve().parent.parent


def _asleep(srv, mac: str) -> None:
    conn = srv["ctx"].conn
    conn.execute("UPDATE net_devices SET last_seen = '2026-01-01T00:00:00+00:00' WHERE mac = ?", (mac,))
    conn.commit()


def _round_row(srv):
    return srv["ctx"].conn.execute("SELECT * FROM room_rounds WHERE state = 'active'").fetchone()


def _item(srv, mac: str) -> dict:
    return next(x for x in json.loads(_round_row(srv)["target_slots_json"]) if x["mac"] == mac)


def _mismatches(srv) -> list[str]:
    return [r["detail"] for r in srv["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'room_drawer_wake_mismatch' ORDER BY id")]


def _open_asleep(srv, serials=("S1", "S2"), ports=(1, 2)) -> None:
    cloner_hello(srv["anon"], CLONER1, list(serials))
    _asleep(srv, CLONER1)
    r = _open_with_slots(srv["admin"], [{"mac": CLONER1, "ports": list(ports)}], target=len(ports))
    assert r.status_code == 200, r.text


def test_a_sleeping_cloners_drawers_open_a_round_bound_to_their_serials(room_server):
    _open_asleep(room_server)
    assert _item(room_server, CLONER1) == {"mac": CLONER1, "ports": [1, 2], "expect": {"1": "S1", "2": "S2"}}


def test_an_awake_cloner_is_not_bound(room_server):
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert _open_with_slots(room_server["admin"], [{"mac": CLONER1, "ports": [1, 2]}], target=2).status_code == 200
    assert "expect" not in _item(room_server, CLONER1)
    # a swapped drawer of an awake cloner is still written by port (as before #1954)
    assert cloner_hello(room_server["anon"], CLONER1, ["S1", "X9"])["session"]["target_ports"] == [1, 2]
    assert _mismatches(room_server) == []


def test_waking_with_the_same_disks_writes_them_and_drops_the_binding(room_server):
    _open_asleep(room_server)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert answer["session"]["target_ports"] == [1, 2]
    assert _item(room_server, CLONER1) == {"mac": CLONER1, "ports": [1, 2]}
    assert _round_row(room_server)["target_drives"] == 2
    assert _mismatches(room_server) == []


def test_waking_with_another_disk_in_the_slot_skips_that_drawer_in_the_open(room_server):
    _open_asleep(room_server)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "X9"])   # port 2: not the disk chosen
    assert answer["session"]["target_ports"] == [1]                      # never the other disk
    item = _item(room_server, CLONER1)
    assert item["ports"] == [1] and "expect" not in item
    assert item["wake_skipped"] == [{"port": 2, "serial": "S2", "found": "X9"}]
    assert _round_row(room_server)["target_drives"] == 1                 # the round does not wait for it
    assert len(_mismatches(room_server)) == 1 and _mismatches(room_server)[0].endswith(
        "shich-1 port=2 chosen=S2 found=X9")
    # decided once: the next hello neither writes it nor journals again
    assert cloner_hello(room_server["anon"], CLONER1, ["S1", "X9"])["session"]["target_ports"] == [1]
    assert len(_mismatches(room_server)) == 1
    # the console gets it
    slots = room(room_server["admin"])["round"]["target_slots"]
    assert next(x for x in slots if x["mac"] == CLONER1)["wake_skipped"][0]["found"] == "X9"


def test_waking_with_an_empty_slot_skips_it_as_missing(room_server):
    _open_asleep(room_server)
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1"])
    assert answer["session"]["target_ports"] == [1]
    assert _mismatches(room_server)[0].endswith("port=2 chosen=S2 found=missing")


def test_waking_with_every_chosen_disk_gone_does_not_join(room_server):
    _open_asleep(room_server, ports=(2,))
    answer = cloner_hello(room_server["anon"], CLONER1, ["S1", "X9"])
    assert answer["session"] is None                                     # nothing of it is chosen any more
    assert _item(room_server, CLONER1)["ports"] == []


def test_a_hello_without_an_inventory_decides_nothing_and_writes_no_bound_slot(room_server):
    from server import room as room_module
    _open_asleep(room_server)
    room_module.wake_check(room_server["ctx"].conn, CLONER1, None)
    assert _item(room_server, CLONER1)["expect"] == {"1": "S1", "2": "S2"}
    assert room_module.target_ports(_round_row(room_server), CLONER1, None) == []
    assert _mismatches(room_server) == []


def test_an_inventory_without_slots_decides_nothing(room_server):
    """An old agent (no ``port`` field): an empty-looking slot is no evidence of an empty
    slot -- the binding stays, nothing is written to it, the target is unchanged."""
    _open_asleep(room_server)
    assert cloner_hello(room_server["anon"], CLONER1, ["S1", "X9"], ports=False)["session"] is None
    assert _item(room_server, CLONER1)["expect"] == {"1": "S1", "2": "S2"}
    assert _round_row(room_server)["target_drives"] == 2 and _mismatches(room_server) == []


def test_only_the_sleeping_machine_is_bound(room_server):
    cloner_hello(room_server["anon"], CLONER1, ["S1"])
    cloner_hello(room_server["anon"], CLONER2, ["S3"])
    _asleep(room_server, CLONER2)
    assert _open_with_slots(room_server["admin"], [{"mac": CLONER1, "ports": [1]},
                                                   {"mac": CLONER2, "ports": [1]}], target=2).status_code == 200
    assert "expect" not in _item(room_server, CLONER1)
    assert _item(room_server, CLONER2)["expect"] == {"1": "S3"}


# --- the console ---------------------------------------------------------------------

NODE = shutil.which("node")


@requires_native(("node", NODE))
def test_the_console_lets_a_sleeping_cloners_disk_be_ticked_and_says_wol(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    names = ["roomPickSlots", "roomWakeSkipped"]
    pick = {n: re.search(rf"^function {n}\(.*?^\}}", js, re.M | re.S) for n in names}
    assert pick["roomPickSlots"], "roomPickSlots is gone from console.js"
    # a helper that does not exist (the code before #1954) says nothing -- the assertions below fail on what
    # the console shows, not on "not found"
    src = {n: m.group(0) if m else f'function {n}() {{ return ""; }}' for n, m in pick.items()}
    grid = re.search(r"^function roomPickGrid\(kids\) \{.*?^\}", js, re.M | re.S)
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x), ltr = (x) => String(x), machineName = (m) => m.name || m.mac;
const diskLabel = (m, d) => `דיסק ${d.port}`;
const UI = { note: (kind, html) => `<note ${kind}>${html}</note>` };
let IMAGES = [], DEPLOY = { form: { image: "" } };
const ROOM = { machines: [{ mac: "m1", awake: false }, { mac: "m2", awake: true }] };
const roomMachine = (mac) => ROOM.machines.find((x) => x.mac === mac);
const slotClass = () => "ok";
const machineSlots = (m) => ({ slots: [{ n: 1, disk: { port: 1, serial: "S1", size_bytes: 1 }, fail: null }] });
""" + "\n".join(src.values()) + """
const slots = roomPickSlots([{ mac: "m1", name: "1" }, { mac: "m2", name: "2" }]);
const r = { target_slots: [{ mac: "m1", ports: [], wake_skipped: [{ port: 2, serial: "S2", found: null }] }] };
console.log(JSON.stringify({ slots: slots.map((x) => ({ mac: x.m.mac, ok: x.ok, wol: x.wol })),
  note: roomWakeSkipped([{ mac: "m1", name: "1" }], r) }));
""", encoding="utf-8", newline="\n")
    out = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["slots"] == [{"mac": "m1", "ok": True, "wol": True}, {"mac": "m2", "ok": True, "wol": False}], got
    assert "המגירה דולגה ולא נכתבה" in got["note"] and "אין דיסק" in got["note"] and "<note warn>" in got["note"]
    assert grid and "x.wol ? \"יידלק ב-WoL\"" in grid.group(0), "the grid does not say WoL on the disk"
