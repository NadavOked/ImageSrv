"""Server connection mapping, NVMe identity, and optical inventory (#1485)."""

from __future__ import annotations

import json

import pytest

from conftest import hello_body
from server.disks import drawer_slots
from server.rounds import room
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401

MACHINES = "/api/console/machines"


def _machine(client, mac):
    return next(row for row in client.get(MACHINES).json() if row["mac"] == mac)


def _hello(client, mac, *, disks=None, optical_marker=False, optical=None):
    body = hello_body(mac)
    if disks is not None:
        body["disks"] = disks
    if optical_marker:
        body["optical"] = optical
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("value", [
    ["sata:1", "nvme:0000:03:00.0"],
    ["sata:32"],
])
def test_parse_accepts_connection_keys(value):
    assert drawer_slots.parse(value) == value


@pytest.mark.parametrize("value", [
    [], ["sata:1"] * 9, ["sata:2", "sata:2"], ["sata:0"],
    ["sata:33"], ["sata:01"], ["nvme:0000:03:00.8"], ["nvme:0000:03:00.A"],
])
def test_parse_rejects_empty_large_duplicate_and_bad_keys(value):
    with pytest.raises(ValueError):
        drawer_slots.parse(value)


def test_effective_and_disk_lookup_helpers():
    assert drawer_slots.effective(3, None) == ["sata:1", "sata:2", "sata:3"]
    slots = ["sata:5", "nvme:0000:03:00.0"]
    assert drawer_slots.key_of({"port": 5, "pci": "0000:09:00.0"}) == "sata:5"
    assert drawer_slots.key_of({"port": None, "pci": "0000:03:00.0"}) == slots[1]
    assert drawer_slots.key_of({"port": True, "pci": "junk"}) is None
    assert drawer_slots.drawer_of(slots, {"port": 5}) == 1
    assert drawer_slots.drawer_of(slots, {"pci": "0000:03:00.0"}) == 2
    assert drawer_slots.drawer_of(slots, {"port": 2}) is None


def test_put_mapping_get_reset_and_permissions(room_server):
    admin = room_server["admin"]
    url = f"{MACHINES}/{CLONER1}"
    mapping = ["sata:5", "nvme:0000:03:00.0"]
    response = admin.put(url, json={"drawer_slots": mapping})
    assert response.status_code == 200, response.text
    machine = _machine(admin, CLONER1)
    assert machine["drawer_slots"] == mapping
    assert machine["drawer_slots_custom"] is True
    assert machine["drawer_count"] == 2
    assert room_server["deploy"].put(url, json={"drawer_slots": ["sata:1"]}).status_code == 403
    assert room_server["anon"].put(url, json={"drawer_slots": ["sata:1"]}).status_code == 401

    response = admin.put(url, json={"drawer_slots": None})
    assert response.status_code == 200, response.text
    machine = _machine(admin, CLONER1)
    assert machine["drawer_slots"] == ["sata:1", "sata:2"]
    assert machine["drawer_slots_custom"] is False


def test_put_mapping_validation_happens_before_writes(room_server):
    admin, ctx = room_server["admin"], room_server["ctx"]
    url = f"{MACHINES}/{CLONER1}"
    before = tuple(ctx.conn.execute(
        "SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (CLONER1,)
    ).fetchone())
    for body in (
        {"drawer_slots": ["sata:2", "sata:2"]},
        {"drawer_slots": ["sata:2"], "drawer_count": 2},
    ):
        assert admin.put(url, json=body).status_code == 400
        after = tuple(ctx.conn.execute(
            "SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (CLONER1,)
        ).fetchone())
        assert after == before

    assert admin.put(url, json={"drawer_slots": ["sata:5", "sata:1"]}).status_code == 200
    assert admin.put(url, json={"drawer_count": 3}).status_code == 400
    stored = ctx.conn.execute(
        "SELECT drawer_count, drawer_slots FROM machines WHERE mac = ?", (CLONER1,)
    ).fetchone()
    assert stored["drawer_count"] == 2
    assert json.loads(stored["drawer_slots"]) == ["sata:5", "sata:1"]


def test_hello_roles_receive_mapping_only_when_applicable(room_server):
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    build = "aa:bb:cc:00:00:31"
    classroom = "aa:bb:cc:00:00:32"
    assert admin.post(MACHINES, json={
        "mac": build, "name": "build-1", "group_id": "grp_BUILD"}).status_code == 200
    ctx.conn.execute("INSERT INTO groups (id, label, role) VALUES (?, ?, ?)",
                     ("grp_TESTCLASS", "Test", "classroom"))
    ctx.conn.execute(
        "INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, ?, ?, ?)",
        (classroom, "01", "grp_TESTCLASS", "now"))
    ctx.conn.commit()
    assert _hello(anon, CLONER1)["drawer_slots"] == ["sata:1", "sata:2", "sata:3"]
    assert _hello(anon, build)["drawer_slots"] == ["sata:1", "sata:2", "sata:3"]
    assert "drawer_slots" not in _hello(anon, classroom)


def test_optical_state_and_nvme_pci_are_returned_by_machines(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    nvme = {"dev": "nvme0n1", "size_bytes": 1000, "model": "NVMe", "serial": "N1",
            "removable": False, "scheme": "gpt", "has_data": True,
            "port": None, "pci": "0000:03:00.0"}
    optical = [{"dev": "sr0", "model": "HL-DT-ST DVDRAM", "port": 3}]
    _hello(anon, CLONER1, disks=[nvme], optical_marker=True, optical=optical)
    machine = _machine(admin, CLONER1)
    assert machine["optical"] == optical
    assert machine["disks"][0]["pci"] == "0000:03:00.0"

    _hello(anon, CLONER2)
    assert _machine(admin, CLONER2)["optical"] is None
    _hello(anon, CLONER1)
    assert _machine(admin, CLONER1)["optical"] is None


def test_room_uses_mapping_for_validation_and_drawer_numbers(room_server):
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    assert admin.put(f"{MACHINES}/{CLONER1}",
                     json={"drawer_slots": ["sata:5", "sata:1"]}).status_code == 200
    disks = [
        {"dev": "sda", "size_bytes": 1000, "model": "A", "serial": "S5",
         "removable": False, "scheme": "gpt", "has_data": False, "port": 5},
        {"dev": "sdb", "size_bytes": 1000, "model": "B", "serial": "S1",
         "removable": False, "scheme": "gpt", "has_data": False, "port": 1},
        {"dev": "sdc", "size_bytes": 1000, "model": "C", "serial": "S2",
         "removable": False, "scheme": "gpt", "has_data": False, "port": 2},
    ]
    _hello(anon, CLONER1, disks=disks)
    assert room._validate_target_slots(
        ctx.conn, [{"mac": CLONER1, "ports": [5]}]) == [{"mac": CLONER1, "ports": [5]}]
    with pytest.raises(ValueError):
        room._validate_target_slots(ctx.conn, [{"mac": CLONER1, "ports": [2]}])

    machine = next(m for m in admin.get("/api/console/room").json()["machines"]
                   if m["mac"] == CLONER1)
    assert machine["drawer_slots"] == ["sata:5", "sata:1"]
    by_port = {disk["port"]: disk["drawer"] for disk in machine["drawer_list"]}
    assert by_port == {5: 1, 1: 2, 2: None}
