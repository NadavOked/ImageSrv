"""#3898 S2b — דופק, בדיקת דיסקים בהתעוררות והגנת כיבוי לכל סבב."""

import json

import pytest

pytest.importorskip("fastapi")

from server.rounds import room  # noqa: E402
from test_round_exclusive_3898 import between_waves, slots  # noqa: E402
from test_round_suggest_3897 import add, mac  # noqa: E402
from test_server_room import CLONER1, CLONER2, room_server  # noqa: E402,F401


def _open(rs, n, **extra):
    response = rs["admin"].post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 3, "target_slots": slots(n), **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("parallel", [False, True], ids=["single-round", "parallel-rounds"])
def test_tick_advances_a_round_even_if_another_fails(room_server, monkeypatch, parallel):
    rs = room_server
    ctx = rs["ctx"]
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", parallel)
    add(rs, 2)
    if parallel:
        add(rs, 1)
        first = _open(rs, 1)
        between_waves(rs, 1)  # שלב 3 עוד לא פיצל את חריץ השידור.
    second = _open(rs, 2)
    between_waves(rs, 2)
    resume = room._resume

    def fail_first(conn, store, round_row, wave):
        if parallel and round_row["id"] == first["id"]:
            raise RuntimeError("injected round A failure")
        return resume(conn, store, round_row, wave)

    monkeypatch.setattr(room, "_resume", fail_first)
    room.tick(ctx.conn, ctx.store)

    next_wave = room.round_of_mac(ctx.conn, mac(2))["wave_session_id"]
    assert next_wave and next_wave != second["wave_session_id"]
    assert ctx.conn.execute("SELECT state FROM sessions WHERE id = ?", (next_wave,)).fetchone()["state"] == "open"
    entries = ctx.conn.execute(
        "SELECT round_id, detail FROM journal WHERE event = 'room_tick_failed'"
    ).fetchall()
    if parallel:
        assert room.round_of_mac(ctx.conn, mac(1))["wave_session_id"] == first["wave_session_id"]
        (entry,) = entries
        assert entry["round_id"] == first["id"]
        assert first["id"] in entry["detail"] and "injected round A failure" in entry["detail"]
    else:
        assert not entries


@pytest.mark.parametrize("parallel", [False, True], ids=["single-round", "parallel-rounds"])
def test_opening_with_keep_on_protects_every_active_round(room_server, monkeypatch, parallel):
    rs = room_server
    conn = rs["ctx"].conn
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", parallel)
    add(rs, 2)
    if parallel:
        add(rs, 1)
        _open(rs, 1)
        between_waves(rs, 1)

    result = _open(rs, 2, keep_on=[CLONER1])["keep_on"]

    protected = {mac(1), mac(2)} if parallel else {mac(2)}
    assert {m["mac"] for m in result["kept"]} == protected | {CLONER1}
    assert {m["mac"] for m in result["off"]} == {CLONER2}
    assert {tuple(row) for row in conn.execute("SELECT mac, action FROM power_requests")} == {
        (CLONER2, "poweroff"),
    }


@pytest.mark.parametrize("parallel", [False, True], ids=["single-round", "parallel-rounds"])
def test_wake_check_reconciles_only_the_round_owning_the_cloner(room_server, monkeypatch, parallel):
    rs = room_server
    conn = rs["ctx"].conn
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", parallel)
    add(rs, 2)
    if parallel:
        add(rs, 1)
        _open(rs, 1)
        first = room.round_of_mac(conn, mac(1))
        between_waves(rs, 1)
    second = _open(rs, 2)
    # בחירה שנקשרה למלאי שמור כשהמשכפל היה כבוי.
    selected = [{"mac": mac(2), "ports": [1, 2, 3], "expect": {"1": "old", "2": "same"}}]
    conn.execute("UPDATE room_rounds SET target_slots_json = ? WHERE id = ?",
                 (json.dumps(selected), second["id"]))
    conn.commit()

    room.wake_check(conn, mac(2), [{"port": 1, "serial": "replacement"}, {"port": 2, "serial": "same"}])

    row = room.round_of_mac(conn, mac(2))
    assert row["target_drives"] == 2
    assert json.loads(row["target_slots_json"]) == [{
        "mac": mac(2), "ports": [2, 3],
        "wake_skipped": [{"port": 1, "serial": "old", "found": "replacement"}],
    }]
    if parallel:
        other = room.round_of_mac(conn, mac(1))
        assert other["id"] == first["id"] and other["target_drives"] == first["target_drives"]
        assert other["target_slots_json"] == first["target_slots_json"]
