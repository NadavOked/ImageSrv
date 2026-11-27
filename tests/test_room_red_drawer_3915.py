"""#3915: nine requested disks can be written across two regular room waves."""

from conftest import hello_body
from test_server_room import (  # noqa: F401 -- room_server is a fixture
    CLONER1, CLONER2, heartbeat, report, room, room_server,
)

CLONER3 = "aa:bb:cc:00:00:23"
RED_SERIAL = "C3-red"


def _hello(client, mac, serials, *, joining=True):
    body = hello_body(mac)
    body["joining"] = joining
    body["disks"] = [
        {"dev": dev, "port": port, "serial": serial, "size_bytes": 256060514304,
         "model": "Drawer SSD", "removable": False, "scheme": "gpt", "has_data": False,
         **({"smart": "fail", "smart_reason": "readonly"} if serial == RED_SERIAL else {"smart": "ok"})}
        for port, (dev, serial) in enumerate(zip(("sda", "sdb", "sdc"), serials, strict=True), 1)
    ]
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()["session"]


def test_eight_selected_drawers_write_nine_disks_in_two_regular_waves(room_server):
    admin, deploy, anon = (room_server[key] for key in ("admin", "deploy", "anon"))
    added = admin.post("/api/console/machines", json={
        "mac": CLONER3, "name": "shich-3", "group_id": "grp_CLONERS",
    })
    assert added.status_code == 200, added.text
    inventory = {
        CLONER1: (("A1", "A2", "A3"), [1, 2, 3]),
        CLONER2: (("B1", "B2", "B3"), [1, 2, 3]),
        CLONER3: (("C1", "C2", RED_SERIAL), [1, 2]),
    }
    for mac, (serials, _) in inventory.items():
        configured = admin.put(f"/api/console/machines/{mac}", json={"drawer_count": 3})
        assert configured.status_code == 200, configured.text
        _hello(anon, mac, serials, joining=False)

    opened = deploy.post("/api/console/room", json={
        "image_id": "img_7f3a91",
        "target_drives": 9,
        "target_slots": [{"mac": mac, "ports": ports} for mac, (_, ports) in inventory.items()],
    })
    assert opened.status_code == 200, opened.text
    round_id = opened.json()["id"]
    wave1 = room(deploy)["round"]["wave_session_id"]
    for mac, (serials, ports) in inventory.items():
        session = _hello(anon, mac, serials)
        assert session["id"] == wave1
        assert session["target_ports"] == ports

    first = room(deploy)["round"]
    assert first["production_line"] is None
    assert (first["target_drives"], first["ready_drives"], first["wave_state"]) == (9, 8, "open")
    # The operator starts with the eight ready disks, invoking the real precheck.
    started = deploy.post("/api/console/room/start")
    assert started.status_code == 200, started.text
    assert room(deploy)["round"]["wave_state"] == "running"

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done", "sdc": "done"})
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done", "sdc": "done"})
    report(anon, wave1, CLONER3, {"sda": "done", "sdb": "done"})
    heartbeat(room_server)
    second = room(deploy)["round"]
    assert second["id"] == round_id
    assert (second["wave_number"], second["wave_state"]) == (2, "open")
    assert (second["written_drives"], second["remaining_drives"]) == (8, 1)
    wave2 = second["wave_session_id"]
    assert wave2 != wave1

    # Put a new disk in a selected drawer that was used in wave 1.
    session = _hello(anon, CLONER3, ("C4", "C2", RED_SERIAL))
    assert session["id"] == wave2
    assert session["target_ports"] == [1]
    assert session["target_disks"][0]["serial"] == "C4"
    heartbeat(room_server)
    assert room(deploy)["round"]["wave_state"] == "running"
    report(anon, wave2, CLONER3, {"sda": "done"})
    heartbeat(room_server)

    assert room(deploy)["round"] is None
    row = room_server["ctx"].conn.execute(
        "SELECT state, written_drives, wave_number FROM room_rounds WHERE id = ?", (round_id,),
    ).fetchone()
    assert (row["state"], row["written_drives"], row["wave_number"]) == ("closed", 9, 2)
