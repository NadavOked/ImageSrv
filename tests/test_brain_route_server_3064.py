"""#3064 (K5): the completion route -- HTTP or a multicast pass -- recorded by the real server, in shadow.

The path is the server's own: a cloning-room wave on the chunk engine (`room_server`), the agent's `chunk-pass`
reports through `POST /api/v1/agent/chunk-pass`, the ledger (`chunk_ledger`) they write, and `brain_decisions`. One
cloner, two drawers, a partition of 10 chunks: after the first pass S1 holds all 10 and S2 holds 0-4, so the cloner
lacks 5-9 alone and the code of today fetches them over HTTP (D9). The brain only writes down what it would do.

This file does not import `server.brain.route`: it reads what the server wrote, so with the production files back at
the base it fails on behaviour (no row), not on an import.

**Negative control:** with `server/app.py`, `server/agent/api.py` and `server/brain/budget.py` back at the base and
`server/brain/route.py` removed, the test marked (neg) fails: no `COMPLETE_ROUTE`, `FETCH_LEASE`, `HTTP_FETCH` or
`REPAIR_TOTALS` row. The guard (`off` records nothing) passes in both states.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.brain import memory
from server.db import set_setting

from conftest import MANIFEST_256
from test_chunksend_2533 import CLONER1, _index_the_image, _room_wave, wait_for
from test_server_room import room_server  # noqa: F401 -- shared fixture

CHUNKS = 10


def _rows(ctx, action: str) -> list[dict]:
    return [d for d in memory.decisions(ctx.conn, rule="K5", limit=200) if d["action"] == action]


def _evidence(row: dict, key: str) -> str | None:
    return next((e.split("=", 1)[1] for e in row.get("evidence") or [] if str(e).startswith(key + "=")), None)


def _open_wave(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    _index_the_image(ctx, chunks=CHUNKS)
    wave, _hello = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))

    def tell(pass_, state, s1, s2):
        got = anon.post("/api/v1/agent/chunk-pass", json={
            "mac": CLONER1, "session_id": wave, "part": part, "pass": pass_, "state": state, "targets": [
                {"serial": "S1", "write_mbps": 150, "durable": [{"part": part, "ranges": s1}]},
                {"serial": "S2", "write_mbps": 150, "durable": [{"part": part, "ranges": s2}]}]})
        assert got.status_code == 200, got.text
        return got.json()
    return ctx, wave, part, tell


def test_a_fetch_round_is_recorded_with_its_route_its_lease_and_what_it_measured(room_server):
    """(neg) the round of today (HTTP for 5 chunks one cloner lacks) -> the brain's route next to it, the completion
    contract of the download, the download's measured numbers when it ends, and the wave's totals."""
    ctx, wave, part, tell = _open_wave(room_server)
    assert tell(1, "listening", [], [])["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)
    assert wait_for(lambda: tell(1, "ended", [[0, 9]], [[0, 4]]).get("action") == "fetch", 10)

    assert wait_for(lambda: _rows(ctx, "COMPLETE_ROUTE") and _rows(ctx, "FETCH_LEASE"))
    route = _rows(ctx, "COMPLETE_ROUTE")[0]
    assert route["round_id"] == wave and route["mode"] == "shadow"
    assert route["verdict"] == "HTTP_TAIL", route                       # 5 chunks: far under a minute over HTTP
    assert _evidence(route, "today") == "HTTP" and _evidence(route, "shared") == "0"
    assert _evidence(route, "engine_plan") == "matches", route["evidence"]
    assert _evidence(route, "r_http") == "42.0"                         # no download measured yet: KT4
    lease = _rows(ctx, "FETCH_LEASE")[0]
    assert lease["entity_key"] == CLONER1 and _evidence(lease, "chunks") == "5" and _evidence(lease, "ranges") == "1"
    assert 300 <= int(_evidence(lease, "lease_s")) <= 600                # the floor, plus the grace after a restart

    assert tell(2, "ended", [[0, 9]], [[0, 9]])["ok"] is True
    assert wait_for(lambda: _rows(ctx, "HTTP_FETCH"))
    fetched = _rows(ctx, "HTTP_FETCH")[0]
    assert fetched["verdict"] == "MEASURED" and _evidence(fetched, "chunks") == "5", fetched
    assert _evidence(fetched, "bytes") == str(5 * 4 * 1024 * 1024) and _evidence(fetched, "ranges") == "1"
    assert float(_evidence(fetched, "rate_cps")) > 0
    assert "בתוך" in (_rows(ctx, "FETCH_LEASE")[0]["outcome"] or "")
    assert "הסבב לקח בפועל" in (_rows(ctx, "COMPLETE_ROUTE")[0]["outcome"] or "")

    ctx.chunk.stop(wave)
    assert wait_for(lambda: _rows(ctx, "REPAIR_TOTALS"))
    totals = _rows(ctx, "REPAIR_TOTALS")[0]
    assert totals["verdict"] == "NO_TARGETED_MULTICAST", totals
    assert _evidence(totals, "fetched_chunks") == "5" and _evidence(totals, "multicast_chunks") == "0"


def test_with_the_brain_off_nothing_is_recorded(room_server):
    """Guard, both states: `brain_veto_mode=off` -- the round runs as today and the brain writes no row."""
    ctx, wave, _part, tell = _open_wave(room_server)
    set_setting(ctx.conn, "brain_veto_mode", "off")
    assert tell(1, "listening", [], [])["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)
    assert wait_for(lambda: tell(1, "ended", [[0, 9]], [[0, 4]]).get("action") == "fetch", 10)
    assert tell(2, "ended", [[0, 9]], [[0, 9]])["ok"] is True
    ctx.chunk.stop(wave)
    assert wait_for(lambda: ctx.chunk.status()["state"] == "stopped")
    assert _rows(ctx, "COMPLETE_ROUTE") == [] and _rows(ctx, "HTTP_FETCH") == [] and _rows(ctx, "REPAIR_TOTALS") == []
