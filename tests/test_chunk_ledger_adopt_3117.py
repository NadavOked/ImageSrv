"""#3117 (G21, brain inventory #3064) -- the ledger is tied to the image on the read side too.

`chunkledger.record` deletes the rows of another image and keeps ranges only when `index_xxh64` is
the same. The read side was keyed by the round alone: `adopt` (stage 4, "continue the write in a new
round") moved a row to a round that writes ANOTHER image, and `DbLedger.durable` -- which only
compared `round_id` -- then returned the old image's 60 chunks as durable for the new one:
`repair.plan` would not ask for them, and only a read-back (if it was whole) would catch it. That is
"we did not check" dressed as "whole" (principle 5). Nothing calls `adopt` today; the guard has to be
there before something does.

Two guards, both here: `adopt` refuses by name and moves nothing (`image_id` and the round's manifest
are required), and `DbLedger.durable` answers `[]` for a row whose image is not the wave's.
"""

from __future__ import annotations

import pytest

from server import db
from server.rounds import chunkledger as L
from test_chunk_ledger_2533 import CHUNKS, MANIFEST, SIZE, ranges, rec
from test_chunksend_2533 import MANIFEST_256, _index_the_image, _room_wave
from test_server_room import CLONER1, room_server  # noqa: F401 -- a fixture


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _round_of(conn, serial="S1") -> str:
    return conn.execute("SELECT round_id FROM chunk_ledger WHERE serial = ? AND part = 3", (serial,)).fetchone()[0]


def test_adopt_into_a_round_of_another_image_is_refused_by_name_and_moves_nothing(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 59]]}, {"part": 1, "ranges": [[0, 1]]}])          # ses_1 writes img_aaaaaa
    with pytest.raises(L.LedgerProblem, match="img_aaaaaa.*img_bbbbbb|not a continuation"):
        L.adopt(conn, "S1", SIZE, "ses_2", image_id="img_bbbbbb", manifest=MANIFEST)
    assert _round_of(conn) == "ses_1" and ranges(conn) == [(0, 59)], "a refused adopt moves no row"
    assert ranges(conn, part=1) == [(0, 1)]


def test_adopt_into_a_round_whose_chunk_index_differs_is_refused_whole(conn):
    """The same image id, but a partition's index is not the one that was written (the image was
    captured again): not the same write. One partition differing keeps the other from moving too."""
    rec(conn, [{"part": 3, "ranges": [[0, 59]]}, {"part": 1, "ranges": [[0, 1]]}])
    other = {"partitions": [{"index": 3, "index_xxh64": "a" * 16, "readback_chunks": CHUNKS},
                            {"index": 1, "index_xxh64": "f" * 16, "readback_chunks": 2}]}
    with pytest.raises(L.LedgerProblem, match="partition 1.*chunk index"):
        L.adopt(conn, "S1", SIZE, "ses_2", image_id="img_aaaaaa", manifest=other)
    assert _round_of(conn) == "ses_1"                                       # partition 3 did not move either


def test_adopt_into_a_round_whose_manifest_lacks_a_partition_of_the_ledger_is_refused(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 59]]}])
    with pytest.raises(L.LedgerProblem, match="partition 3 is not in the image's manifest"):
        L.adopt(conn, "S1", SIZE, "ses_2", image_id="img_aaaaaa", manifest={"partitions": []})
    assert _round_of(conn) == "ses_1"


def test_adopt_cannot_be_called_without_the_image_the_guard_is_not_the_callers_to_remember(conn):
    rec(conn, [{"part": 3, "ranges": [[0, 59]]}])
    with pytest.raises(TypeError):
        L.adopt(conn, "S1", SIZE, "ses_2")                                  # the old, unguarded form
    assert _round_of(conn) == "ses_1"


def test_adopt_into_a_round_of_the_same_image_still_carries_the_ledger(conn):
    """The guard against over-refusing: the continuation of the SAME write is what `adopt` is for."""
    rec(conn, [{"part": 3, "ranges": [[0, 59]]}])
    assert L.adopt(conn, "S1", SIZE, "ses_2", image_id="img_aaaaaa", manifest=MANIFEST) == 1
    assert _round_of(conn) == "ses_2"
    rec(conn, [{"part": 3, "ranges": [[60, 70]]}], round_id="ses_2")
    assert ranges(conn) == [(0, 70)]


def test_the_engine_does_not_read_a_row_of_another_image_as_durable(room_server):  # noqa: F811
    """The read side, on its own: a row that carries the round's id but another image (what an unguarded
    `adopt` would leave) is not evidence for the wave. The real `DbLedger`, a real wave."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    _index_the_image(ctx)
    assert room_server["admin"].post("/api/console/settings", json={"stream_engine": "chunk"}).status_code == 200
    wave, _answer = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    assert anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": "listening",
        "targets": [{"serial": "S1"}]}).json()["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)
    assert anon.post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": "ended",
        "targets": [{"serial": "S1", "durable": [{"part": part, "ranges": [[0, 0]]}]}]}).status_code == 200
    ledger = ctx.chunk.ledger
    assert ledger.durable(wave, CLONER1, "S1", part) == [(0, 0)]            # the guard that holds in both states: a row of this wave's image is evidence
    ctx.conn.execute("UPDATE chunk_ledger SET image_id = 'img_of_another_write' WHERE serial = 'S1'")
    ctx.conn.commit()
    assert ledger.durable(wave, CLONER1, "S1", part) == [], "the old image's chunks were read as the new one's"
    ctx.chunk.stop(wave)
