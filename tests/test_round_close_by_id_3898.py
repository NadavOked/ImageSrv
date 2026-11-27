"""#3898 (S2c) -- סגירה, התחלה, ביטול מגירה ושאלת הפסקה לפי `round_id`, ובלעדיו כשיש סבב אחד כמו תמיד."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_round_exclusive_3898 import between_waves, slots  # noqa: E402
from test_round_suggest_3897 import add, mac, open_with  # noqa: E402
from test_server_room import room_server  # noqa: E402,F401

from server.rounds import room  # noqa: E402


def two_rounds(rs, monkeypatch):
    monkeypatch.setattr(room, "PARALLEL_ROUNDS", True)
    for n in (1, 2):
        add(rs, n)
    assert open_with(rs, 3, slots(1)).status_code == 200
    between_waves(rs, 1)
    assert open_with(rs, 3, slots(2)).status_code == 200
    conn = rs["ctx"].conn
    return room.round_of_mac(conn, mac(1))["id"], room.round_of_mac(conn, mac(2))["id"]


def state(rs, round_id):
    return rs["ctx"].conn.execute("SELECT state FROM room_rounds WHERE id = ?", (round_id,)).fetchone()["state"]


def test_closing_round_b_by_id_leaves_round_a_active_and_broadcasting(room_server, monkeypatch):
    rs = room_server
    a, b = two_rounds(rs, monkeypatch)
    wave_a = rs["ctx"].conn.execute("SELECT wave_session_id FROM room_rounds WHERE id = ?", (a,)).fetchone()[0]
    wave_state_a = rs["ctx"].conn.execute("SELECT state FROM sessions WHERE id = ?", (wave_a,)).fetchone()[0]
    stopped = []
    real_stop = rs["ctx"].chunk.stop if rs["ctx"].chunk is not None else None
    if real_stop is not None:

        def spy(*a, **k):
            stopped.extend(a[:1])
            return real_stop(*a, **k)
        monkeypatch.setattr(rs["ctx"].chunk, "stop", spy)

    r = rs["admin"].post("/api/console/room/close", json={"round_id": b})

    assert r.status_code == 200, r.text
    assert (state(rs, a), state(rs, b)) == ("active", "closed")
    assert rs["ctx"].conn.execute("SELECT state FROM sessions WHERE id = ?", (wave_a,)).fetchone()[0] == wave_state_a
    assert wave_a not in stopped          # המשדר של סבב א' לא נעצר


def test_without_round_id_and_two_active_rounds_every_action_is_refused_by_name(room_server, monkeypatch):
    rs = room_server
    a, b = two_rounds(rs, monkeypatch)
    for path, body in (("close", {"confirm_name": "x"}), ("start", {}),
                       ("drawer/cancel", {"mac": mac(1), "dev": "sda"}),
                       ("interrupt", {"answer": "close", "machines": [mac(1)]})):
        r = rs["admin"].post(f"/api/console/room/{path}", json=body)
        assert r.status_code == 409, (path, r.text)
        assert a in r.json()["detail"] and b in r.json()["detail"] and "round_id" in r.json()["detail"], path
    assert (state(rs, a), state(rs, b)) == ("active", "active")
    unknown = rs["admin"].post("/api/console/room/close", json={"round_id": "nope"})
    assert unknown.status_code == 409 and state(rs, a) == "active"
    assert rs["admin"].post("/api/console/room/close", json={"round_id": 7}).status_code == 400


def test_a_single_round_behaves_as_before_with_or_without_round_id(room_server):
    rs = room_server
    add(rs, 1)
    assert open_with(rs, 3, slots(1)).status_code == 200
    only = room.active_round(rs["ctx"].conn)["id"]
    wrong = rs["admin"].post("/api/console/room/close", json={"confirm_name": "not the name"})
    assert wrong.status_code == 400 and state(rs, only) == "active"
    ok = rs["admin"].post("/api/console/room/close",
                          json={"confirm_name": room.round_label(rs["ctx"], room.active_round(rs["ctx"].conn))})
    assert ok.status_code == 200 and state(rs, only) == "closed"
    # ובלי מזהה ובלי סבב: כמו תמיד
    assert rs["admin"].post("/api/console/room/close", json={}).status_code == 409
