"""#3898 S2a — ה-hello והדיווח קוראים את הגל שלהם, והספרייה מגינה על כל סבב פעיל."""

import json

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, MANIFEST_500, write_image  # noqa: E402
from server.rounds import prodline, room  # noqa: E402
from test_server_room import CLONER1, CLONER2, cloner_hello, room_server  # noqa: E402,F401

STAMP = "2026-11-20T12:00:00+00:00"


def _insert_round(conn, name, image_id, mac, port, wave_state):
    wave_id = f"wave-{name}"
    slots = [{"mac": mac, "ports": [port]}]
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, roster_json)"
        " VALUES (?, 'grp_CLONERS', ?, 'ROOM', 1, 30, ?, 'test', ?, 0, ?, ?)",
        (wave_id, image_id, wave_state, STAMP, STAMP, json.dumps([mac])),
    )
    conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json, state,"
        " wave_session_id, wave_number, opened_by, created_at) VALUES (?, ?, 1, ?, 'active', ?, 2, 'test', ?)",
        (name, image_id, json.dumps(slots), wave_id, STAMP),
    )
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, ?)",
                 (wave_id, mac, STAMP))
    return slots


@pytest.mark.parametrize("parallel", [False, True], ids=["single-round", "parallel-rounds"])
def test_hello_and_progress_read_their_own_round(room_server, monkeypatch, parallel):
    rs = room_server
    ctx = rs["ctx"]
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", parallel)
    # כאן בודקים את הקריאות בלבד. קידום סבבים במקביל שייך לפרוסה של room.py.
    monkeypatch.setattr(room, "pulse", lambda *_args: False)
    monkeypatch.setattr(ctx.clock_trust, "ok", lambda: False)
    if parallel:
        _insert_round(ctx.conn, "a", MANIFEST_500["id"], CLONER1, 1, "closed")
    slots = _insert_round(ctx.conn, "b", MANIFEST_256["id"], CLONER2, 2, "running")
    prodline.open_line(ctx.conn, "b", slots, 1, 1, None)
    ctx.conn.commit()

    answer = cloner_hello(rs["anon"], CLONER2, ["B1", "B2"], joining=False)
    session = answer["session"]
    assert session is not None, "hello must return wave B for its cloner"
    assert session["id"] == "wave-b" and session["image_id"] == MANIFEST_256["id"]
    assert session["target_ports"] == [2]
    assert session["target_disks"] == [{"port": 2, "serial": "B2", "size_bytes": 256060514304}]
    assert session["forecast"] == {"ends_at": None, "why": "no_history", "clock": False}
    assert session["production_line"]["target"] == 1

    progress = rs["anon"].post("/api/v1/agent/progress", json={
        "session_id": "wave-b", "mac": CLONER2, "state": "writing", "targets": [],
    })
    assert progress.status_code == 200 and progress.json()["ok"], progress.text
    assert progress.json()["production_line"]["target"] == 1


@pytest.mark.parametrize("parallel", [False, True], ids=["single-round", "parallel-rounds"])
def test_library_protects_the_image_between_waves(room_server, monkeypatch, parallel):
    ctx = room_server["ctx"]
    admin = room_server["admin"]
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", parallel)
    if parallel:
        _insert_round(ctx.conn, "a", MANIFEST_256["id"], CLONER1, 1, "closed")
    write_image(ctx.library.root, MANIFEST_500)
    image_id, image_name = MANIFEST_500["id"], MANIFEST_500["name"]
    _insert_round(ctx.conn, "b", image_id, CLONER2, 2, "closed")
    ctx.conn.commit()

    url = f"/api/console/images/{image_id}"
    deleted = admin.post(f"{url}/delete", json={"confirm_name": image_name})
    assert deleted.status_code == 409, deleted.text
    detail = deleted.json()["detail"]
    if parallel:
        assert image_name in detail
    else:
        assert detail == "האימג' נמצא בשימוש בסשן פעיל"
    assert ctx.library.get(image_id)["name"] == image_name
    marked = admin.post(f"{url}/system-partition", json={"index": 1})
    assert marked.status_code == 409
    assert marked.json()["detail"] == "האימג' נמצא בשימוש בסבב פעיל — סמנו אחרי שיסתיים"


@pytest.mark.parametrize("state", ["active", "closing"])
def test_any_live_round_blocks_an_individual_cloner_reboot(room_server, state):
    conn = room_server["ctx"].conn
    _insert_round(conn, "a", MANIFEST_256["id"], CLONER1, 1, "closed")
    conn.execute("UPDATE room_rounds SET state = ? WHERE id = 'a'", (state,))
    conn.commit()

    response = room_server["admin"].post(f"/api/console/machines/{CLONER2}/reboot",
                                         json={"confirm_name": "shich-2"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "יש סבב חדר פעיל — עצרו אותו לפני אתחול משכפל"
