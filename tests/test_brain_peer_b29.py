"""B29 (נדב 15/11: "לבנות") -- המוח בוחר מאיפה להשלים: מגירה שכנה, משכפל שכן או השרת. **במצב צל.**

שני חלקים: `choose` ו-`load_of` (טהורות), וההחלטה דרך ה-API: דיווח התקדמות של מגירה שחזרה (``rejoin``), ledger של
השרת שאומר שמגירה במשכפל השני מחזיקה את מה שחסר, ותקציב K7 שאומר שהשרת עמוס -> שורה ב-``brain_decisions`` (``B29``,
``PEER``), והסוכן לא שומע דבר. הביצוע נשאר HTTP.

**בקרה שלילית:** `server/brain/peer.py`, `server/agent/api.py`, `server/console/journal_he.py` ו-`catalog.toml` חוזרים לבסיס. ה-import של `server.brain.peer` נופל
(ModuleNotFoundError) בטסטים הטהורים -- ולכן הטסטים של ה-API כתובים כך שגם בלי המודול הם נופלים ב-AssertionError: אין שורת
B29 ב-``brain_decisions``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from test_server_room import CLONER1, CLONER2, cloner_hello, room, room_server  # noqa: E402,F401

MIB = 1024 * 1024
SIZE = 256060514304          # cloner_hello's drawers
XXH = "00112233445566ff"


def _peer_mod():
    from server.brain import peer                                           # noqa: PLC0415
    return peer


def _p(peer, mac="m2", covers=((0, 29),), budget=None, ip="10.0.0.2", dev="sda"):
    return peer.Peer(mac, ip, dev, f"S-{mac}", tuple(covers), peer.CEILING_BPS if budget is None else budget)


def test_the_choice_follows_load_and_rate():
    peer = _peer_mod()
    ok = peer.Load(50 * MIB, False, 60 * MIB, "K7: RAISE")
    busy = peer.Load(50 * MIB, True, 60 * MIB, "K7 קיצץ")
    unmeasured = peer.Load(None, None, None, "אין")
    full = peer.CEILING_BPS
    # 1. the cloner's own drawers hold all of it: no network at all, whatever the server says
    assert peer.choose(30, 30, full, [_p(peer)], busy, full).source == "DRAWER"
    # 2. no peer, no drawer
    assert peer.choose(30, 0, full, [], busy, full).source == "HTTP"
    # 3. the server's load not measured is not "free": stays HTTP, as today
    got = peer.choose(30, 0, full, [_p(peer)], unmeasured, full)
    assert got.source == "HTTP" and "לא נמדד" in got.reason
    # 4. a busy server: the peer is faster than HTTP at its floor
    got = peer.choose(30, 0, full, [_p(peer)], busy, full)
    assert (got.source, got.peer.mac) == ("PEER", "m2")
    assert got.rate_bps == 60 * MIB, "the peer reads within what is left after the stream (K7 limit), not 100"
    # 5. a server that is not busy and faster than the peer may keep it
    fast = peer.Load(110 * MIB, False, 8 * MIB, "K7: RAISE")
    assert peer.choose(30, 0, full, [_p(peer)], fast, full).source == "HTTP"
    # 6. a peer whose own write is already hurt (its K5 budget was cut) is never read from
    got = peer.choose(30, 0, full, [_p(peer, budget=full // 2)], busy, full)
    assert got.source == "HTTP" and any("קוצץ" in e for e in got.evidence)
    # 7. a peer with no known address, or no device, cannot be paired
    assert peer.choose(30, 0, full, [_p(peer, ip=None)], busy, full).source == "HTTP"
    assert peer.choose(30, 0, full, [_p(peer, dev=None)], busy, full).source == "HTTP"
    # 8. the peer holding more of the lack wins; never above 100MiB/s, never above the needy cloner's own budget
    got = peer.choose(30, 0, full, [_p(peer, "m3", ((0, 9),)), _p(peer, "m2", ((0, 29),))],
                      peer.Load(1 * MIB, True, 900 * MIB, "K7 קיצץ"), 40 * MIB)
    assert (got.peer.mac, got.rate_bps) == ("m2", 40 * MIB)


def test_the_load_is_read_from_k7():
    peer = _peer_mod()
    from server.brain import budget                                         # noqa: PLC0415

    assert peer.load_of(None).busy is None
    cut = budget.Decision(100_000_000, "CUT", "io", 400_000_000, 500_000_000, ("io_full=0.5",))
    assert peer.load_of(cut).busy is True and peer.load_of(cut).http_bps == 100_000_000 // 8
    floor = budget.Decision(8_000_000, "CLAMP", "full", 8_000_000, 900_000_000, ())
    assert peer.load_of(floor).busy is True
    blind = budget.Decision(50_000_000, "HOLD", "?", 400_000_000, 500_000_000,
                            ("rx_dropped=unmeasured", "abandoned=unmeasured", "tx_dropped=unmeasured",
                             "io_full=unmeasured"))
    assert peer.load_of(blind).busy is None, "HOLD with nothing measured is not 'quiet'"
    quiet = budget.Decision(50_000_000, "RAISE", "ok", 400_000_000, 500_000_000, ("io_full=0.01",))
    assert peer.load_of(quiet).busy is False


# --- דרך ה-API -----------------------------------------------------------------------------------------------------


def _round(rs) -> str:
    admin, anon = rs["admin"], rs["anon"]
    assert admin.put(f"/api/console/machines/{CLONER1}", json={"drawer_count": 2}).status_code == 200
    assert admin.put(f"/api/console/machines/{CLONER2}", json={"drawer_count": 2}).status_code == 200
    cloner_hello(anon, CLONER1, ["A1", "A2"])
    cloner_hello(anon, CLONER2, ["B1", "B2"])
    slots = [{"mac": CLONER1, "ports": [1, 2]}, {"mac": CLONER2, "ports": [1, 2]}]
    got = rs["deploy"].post("/api/console/room", json={"image_id": "img_7f3a91", "target_drives": 4,
                                                       "target_slots": slots})
    assert got.status_code == 200, got.text
    cloner_hello(anon, CLONER1, ["A1", "A2"])
    cloner_hello(anon, CLONER2, ["B1", "B2"])
    return room(rs["deploy"])["round"]["wave_session_id"]


def _ledger(conn, wave, mac, serial, ranges, chunks=18254, part=3, xxh=XXH):
    conn.execute("INSERT OR REPLACE INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64,"
                 " chunks, ranges_json, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (serial, SIZE, part, wave, mac, "img_7f3a91", xxh, chunks, json.dumps(ranges), "2026-11-15T00:00:00"))
    conn.commit()


def _busy_server(rs):
    from server.brain import budget                                         # noqa: PLC0415
    rs["ctx"].budget = SimpleNamespace(last=budget.Decision(8_000_000, "CUT", "דיסק הספרייה לחוץ", 300_000_000,
                                                            600_000_000, ("io_full=0.6",)))


def _rejoin(rs, wave, missing=30):
    answer = rs["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing", "targets": [
            {"dev": "sda", "serial": "A1", "port": 1, "state": "writing", "bytes_written": 10, "bytes_total": 100,
             "rejoin": {"part": 3, "missing": missing, "chunks": 18254, "away_s": 2}},
            {"dev": "sdb", "serial": "A2", "port": 2, "state": "writing", "bytes_written": 10, "bytes_total": 100}]})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _rows(rs):
    return rs["ctx"].conn.execute("SELECT * FROM brain_decisions WHERE rule = 'B29' ORDER BY id").fetchall()


def _setup(rs):
    wave = _round(rs)
    conn = rs["ctx"].conn
    _ledger(conn, wave, CLONER1, "A1", [[30, 999]])            # A1 came back: it lacks 0-29 (and what is still to come)
    _ledger(conn, wave, CLONER1, "A2", [[40, 999]])            # its own neighbour lacks the same: no drawer to read
    _ledger(conn, wave, CLONER2, "B1", [[0, 999]])             # the other cloner's drawer holds all of it
    conn.execute("UPDATE net_devices SET ip = '10.55.0.22' WHERE mac = ?", (CLONER2,))
    conn.commit()
    return wave


def test_in_shadow_the_brain_picks_the_peer_cloner_and_the_agent_hears_nothing(room_server):
    wave = _setup(room_server)
    _busy_server(room_server)
    got = _rejoin(room_server, wave)
    assert not {"peer_fill", "peer_serve"} & set(got), "בצל הסוכן לא שומע"
    rows = _rows(room_server)
    assert len(rows) == 1, [dict(r) for r in rows]
    row = rows[0]
    assert (row["mode"], row["action"], row["verdict"], row["entity_key"]) == ("shadow", "FILL_PEER", "PEER", "A1")
    evidence = json.loads(row["evidence_json"])
    assert f"peer={CLONER2}" in evidence and "peer_disk=B1" in evidence
    # the same report again is not a new decision: one row per change, not one every 2 seconds
    _rejoin(room_server, wave)
    assert len(_rows(room_server)) == 1


def test_a_server_whose_load_was_not_measured_stays_http(room_server):
    wave = _setup(room_server)
    room_server["ctx"].budget = None
    _rejoin(room_server, wave)
    rows = _rows(room_server)
    assert len(rows) == 1 and rows[0]["verdict"] == "HTTP", [dict(r) for r in rows]
    assert "לא נמדד" in rows[0]["reason"]


def test_a_peer_of_another_image_index_or_a_failed_member_is_not_a_source(room_server):
    wave = _setup(room_server)
    _busy_server(room_server)
    conn = room_server["ctx"].conn
    _ledger(conn, wave, CLONER2, "B1", [[0, 999]], xxh="ffffffffffffffff")   # another index of partition 3
    _rejoin(room_server, wave)
    assert [r["verdict"] for r in _rows(room_server)] == ["HTTP"]
    _ledger(conn, wave, CLONER2, "B1", [[0, 999]])
    conn.execute("UPDATE session_members SET state = 'failed' WHERE session_id = ? AND mac = ?", (wave, CLONER2))
    conn.commit()
    _rejoin(room_server, wave, missing=31)
    assert [r["verdict"] for r in _rows(room_server)] == ["HTTP"], "a failed member is never paired"


def test_the_switch(room_server):
    from server.db import set_setting                                       # noqa: PLC0415

    wave = _setup(room_server)
    _busy_server(room_server)
    set_setting(room_server["ctx"].conn, "brain_peer_mode", "off")
    _rejoin(room_server, wave)
    assert _rows(room_server) == [], "off: nothing computed"
    set_setting(room_server["ctx"].conn, "brain_peer_mode", "shadow")
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "off")
    _rejoin(room_server, wave)
    assert _rows(room_server) == [], "the general switch off turns this off too"
    set_setting(room_server["ctx"].conn, "brain_veto_mode", "shadow")
    _rejoin(room_server, wave)
    assert [r["verdict"] for r in _rows(room_server)] == ["PEER"]


# --- enforce: the server pairs, and only it (B29, PR ב') ------------------------------------------------------------


def _report(rs, wave, mac, targets):
    answer = rs["anon"].post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": "writing", "targets": targets})
    assert answer.status_code == 200, answer.text
    return answer.json()


def _peer_report(rs, wave):
    return _report(rs, wave, CLONER2, [{"dev": "sda", "serial": "B1", "port": 1, "state": "writing",
                                       "bytes_written": 10, "bytes_total": 100}])


def _enforce(rs):
    from server.db import set_setting                                       # noqa: PLC0415

    set_setting(rs["ctx"].conn, "brain_peer_mode", "enforce")
    rs["ctx"].conn.execute("UPDATE net_devices SET ip = '10.55.0.21' WHERE mac = ?", (CLONER1,))
    rs["ctx"].conn.commit()


def test_in_enforce_the_peer_hears_first_and_the_needy_cloner_after_it(room_server, caplog):
    import logging                                                          # noqa: PLC0415

    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    first = _rejoin(room_server, wave)
    assert "peer_fill" not in first, "the needy cloner hears nothing before the peer was told"
    serve = _peer_report(room_server, wave)["peer_serve"]
    assert len(serve) == 1
    s = serve[0]
    assert (s["dev"], s["part"], s["listen"], s["allow"]) == ("sda", 3, "10.55.0.22", "10.55.0.21"), s
    assert s["listen"] != "0.0.0.0" and 7717 <= s["port"] < 48202
    assert len(s["token"]) == 32 and 1 <= s["rate_mibs"] <= 100 and s["until_s"] > 0
    fill_ = _rejoin(room_server, wave)["peer_fill"]["A1"]
    assert (fill_["id"], fill_["token"], fill_["port"]) == (s["id"], s["token"], s["port"]), "one pairing, both sides"
    assert (fill_["ip"], fill_["me"], fill_["ranges"]) == ("10.55.0.22", "10.55.0.21", [[0, 29]]), fill_
    assert "PAIRED" in caplog.text and s["token"] not in caplog.text, "the pairing is logged, its token never"
    rows = _rows(room_server)
    assert rows[0]["mode"] == "enforce" and rows[0]["verdict"] == "PEER"


def test_a_second_cloner_with_the_same_token_or_another_id_gets_nothing(room_server):
    """The pairing is the server's, by mac: the peer's instruction never reaches the needy cloner and the other way."""
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    _rejoin(room_server, wave)
    assert "peer_fill" not in _peer_report(room_server, wave)
    assert "peer_serve" not in _rejoin(room_server, wave)


def test_a_rejoin_with_an_unhashable_dev_preserves_the_active_pairing(room_server):
    from server.rounds import room_state                                    # noqa: PLC0415

    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    _rejoin(room_server, wave)
    pairing_id = _peer_report(room_server, wave)["peer_serve"][0]["id"]

    ctx = room_server["ctx"]
    body = {"session_id": wave, "targets": [
        {"dev": [], "serial": "A1", "rejoin": {"part": 3, "missing": 30, "chunks": 18254}}]}
    assert ctx.peer.observe(ctx.conn, room_state.active_round(ctx.conn), CLONER1, body,
                            fill_brain=ctx.fill, budget=ctx.budget) == {}

    assert [a["id"] for a in _peer_report(room_server, wave).get("peer_serve", [])] == [pairing_id]


def test_a_neighbour_that_sent_a_bad_chunk_is_out_of_every_pairing_of_the_round(room_server):
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    _rejoin(room_server, wave)
    pid = _peer_report(room_server, wave)["peer_serve"][0]["id"]
    answer = _report(room_server, wave, CLONER1, [
        {"dev": "sda", "serial": "A1", "port": 1, "state": "writing", "bytes_written": 10, "bytes_total": 100,
         "rejoin": {"part": 3, "missing": 30, "chunks": 18254},
         "peer_fill_result": {"id": pid, "result": "bad", "why": "apply rc=8 (a frame was short, or not the index's)"}},
        {"dev": "sdb", "serial": "A2", "port": 2, "state": "writing", "bytes_written": 10, "bytes_total": 100}])
    assert "peer_fill" not in answer
    assert "peer_serve" not in _peer_report(room_server, wave), "its pairing is gone"
    conn = room_server["ctx"].conn
    events = conn.execute("SELECT * FROM journal WHERE event = 'chunk_peer_failed'").fetchall()
    assert len(events) == 1 and CLONER2 in events[0]["detail"], [dict(e) for e in events]
    banned = conn.execute("SELECT * FROM brain_decisions WHERE rule = 'B29' AND action = 'PEER_BANNED'").fetchall()
    assert len(banned) == 1 and banned[0]["entity_key"] == CLONER2
    # the next choice for the same disk is not that neighbour again
    _rejoin(room_server, wave, missing=31)
    assert _rows(room_server)[-1]["verdict"] == "HTTP", [dict(r) for r in _rows(room_server)]


def test_a_failure_of_the_needy_cloner_itself_does_not_blame_the_neighbour(room_server):
    """`none` = no BLAKE3 list here, or apply did not start: not the neighbour's, and it is not banned."""
    wave = _setup(room_server)
    _busy_server(room_server)
    _enforce(room_server)
    _rejoin(room_server, wave)
    pid = _peer_report(room_server, wave)["peer_serve"][0]["id"]
    _report(room_server, wave, CLONER1, [
        {"dev": "sda", "serial": "A1", "port": 1, "state": "writing", "bytes_written": 10, "bytes_total": 100,
         "rejoin": {"part": 3, "missing": 30, "chunks": 18254},
         "peer_fill_result": {"id": pid, "result": "none", "why": "no blake3"}}])
    conn = room_server["ctx"].conn
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'chunk_peer_failed'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM brain_decisions WHERE action = 'PEER_BANNED'").fetchone()[0] == 0


def test_the_shadow_default_pairs_nothing(room_server):
    """The switch is off for enforcement by default: shadow decides PEER and records it, and neither side hears a word."""
    wave = _setup(room_server)
    _busy_server(room_server)
    room_server["ctx"].conn.execute("UPDATE net_devices SET ip = '10.55.0.21' WHERE mac = ?", (CLONER1,))
    room_server["ctx"].conn.commit()
    assert not {"peer_fill", "peer_serve"} & set(_rejoin(room_server, wave))
    assert not {"peer_fill", "peer_serve"} & set(_peer_report(room_server, wave))
    assert not {"peer_fill", "peer_serve"} & set(_rejoin(room_server, wave))
    assert [r["verdict"] for r in _rows(room_server)] == ["PEER"]
