"""#2945 -- the precheck and held texts name the drawer the card shows, not the ATA port.

fd1111 (11/11): a cloner mapped SATA 2 -> disk 1, SATA 3 -> disk 2, SATA 4 -> disk 3
(`drawer_slots` = sata:3, sata:4, sata:5). The cards said "דיסק 1" and "דיסק 2"; the precheck
note on the same page said "CLONER1 · דיסק 3" and "דיסק 4" -- the port. The texts now use the
machine's mapping, as the console's `portLabel` does; an unmapped connection keeps "דיסק <port>".
"""

from __future__ import annotations

import json

from conftest import hello_body
from test_server_room import CLONER1, room, room_server  # noqa: F401 -- fixture

from server.rounds import room_precheck

MAP = ["sata:3", "sata:4", "sata:5"]          # SATA 2/3/4 -> disk 1/2/3


def _hello(client, disks: list[dict]) -> None:
    body = hello_body(CLONER1)
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "removable": False, "scheme": "gpt", "has_data": False,
                      "link_max_gbps": 6.0, "crc": None, **d} for i, d in enumerate(disks)]
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200, r.text


def _mapped(ctx) -> None:
    ctx.conn.execute("UPDATE machines SET drawer_slots = ? WHERE mac = ?", (json.dumps(MAP), CLONER1))
    ctx.conn.commit()


def test_the_precheck_note_names_the_mapped_drawer(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    # #3591: a link speed is not a warning; the CRC that rose since the disk's last round is (the baseline: disk_events)
    for serial, port in (("S1", 3), ("S2", 4)):
        ctx.conn.execute("INSERT INTO disk_events (session_id, mac, disk, port, serial, verdict, reason, crc, updated_at)"
                         " VALUES ('ses_old', ?, ?, ?, ?, 'ok', 'ok', 100, '2026-11-01T00:00:00+00:00')",
                         (CLONER1, f"sd{'ab'[port - 3]}", port, serial))
    ctx.conn.commit()
    drawers = [{"serial": "S1", "port": 3, "link_gbps": 1.5, "crc": 112}, {"serial": "S2", "port": 4, "link_gbps": 1.5, "crc": 112}]
    _hello(anon, [{**d, "crc": None} for d in drawers])
    _mapped(ctx)
    r = deploy.post("/api/console/room", json={"image_id": "img_7f3a91", "target_drives": 4,
                                               "target_slots": [{"mac": CLONER1, "ports": [3, 4]}]})
    assert r.status_code == 200, r.text
    _hello(anon, drawers)
    view = room(deploy)
    cards = {d["port"]: d["drawer"] for m in view["machines"] if m["mac"] == CLONER1 for d in m["drawer_list"]}
    assert cards == {3: 1, 4: 2}, cards
    texts = [w["text_he"] for w in view["round"]["precheck"]]
    assert len(texts) == 2, texts
    assert "· דיסק 1:" in texts[0] and "· דיסק 2:" in texts[1], texts
    assert not any("דיסק 3" in t or "דיסק 4" in t for t in texts), texts


def _machine(**extra) -> dict:
    return {"mac": CLONER1, "name": "CLONER1", "awake": True, "joined": False, **extra}


def test_held_names_the_mapped_drawer_of_the_failed_disk():
    m = _machine(held={"at": "x", "problems": [{"disk": "sdb", "level": "r", "reason": "איטי"}]},
                 drawer_slots=MAP,
                 drawer_list=[{"dev": "sda", "port": 3, "drawer": 1, "fresh": False},
                              {"dev": "sdb", "port": 4, "drawer": 2, "fresh": False}])
    (got,) = room_precheck.missing([m], 2, [{"mac": CLONER1, "ports": [3, 4]}], 0)
    assert got["reason"] == "held" and got["text_he"].endswith("(דיסק 2: איטי)"), got


def test_a_missing_empty_drawer_is_named_by_the_mapping():
    m = _machine(joined=True, drawer_slots=MAP,
                 drawer_list=[{"dev": "sda", "port": 3, "drawer": 1, "fresh": True}])
    (got,) = room_precheck.missing([m], 2, [{"mac": CLONER1, "ports": [3, 4]}], 1)
    assert got["reason"] == "drawers" and got["text_he"].endswith("אין דיסק חדש בדיסק 2"), got


def test_an_unmapped_connection_keeps_the_port():
    m = _machine(joined=True, drawer_slots=MAP,
                 drawer_list=[{"dev": "sda", "port": 3, "drawer": 1, "fresh": True}])
    (got,) = room_precheck.missing([m], 2, [{"mac": CLONER1, "ports": [3, 7]}], 1)
    assert got["text_he"].endswith("בדיסק 7"), got


def test_a_slot_name_still_wins():
    m = _machine(joined=True, drawer_slots=MAP, slot_names={"sata:4": "עליונה"},
                 drawer_list=[{"dev": "sda", "port": 3, "drawer": 1, "fresh": True}])
    (got,) = room_precheck.missing([m], 2, [{"mac": CLONER1, "ports": [3, 4]}], 1)
    assert got["text_he"].endswith("בעליונה"), got
