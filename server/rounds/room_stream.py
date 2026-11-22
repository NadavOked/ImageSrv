"""#3173/#3978: what the room page shows of the stream -- "קצב זרם" and the card "מצב הרשת" -- from the chunk engine.

They were the counters of the removed multicast sender (#989). The chunk engine measures the same things in its own
units, and `/room.round` takes them from it:

    throughput_bps  bytes a second on the wire: the compressed bytes the first passes multicast, over the engine's
                    seconds of those passes (`ChunkEngine.stream_view`; at the end of a wave `chunk_rate.sample_of`'s
                    ``sent_bps`` / 8). The repair rounds are not in it.
    network         the card (#3978, option A, Nadav 21/11): the chunks of the first passes and those that did not
                    arrive (`lost_chunks`, the worst machine), the repair rounds and what they sent again (multicast,
                    HTTP), the first passes' seconds, the compressed bytes, the machines lost, what held the sender
                    (`paced_ms`) and the `--write-kbps` the drives asked for -- and `state`, the verdict.

Where they come from, in order:

    1. the wave the engine holds now, when it is this round's current wave (live). Until a first pass of it ended,
       ``network`` says so (``measuring``) and its numbers are None: the previous wave's are not this wave's;
    2. otherwise the newest wave of this round that `chunk_rate.record` kept (`wave_rates.stream["chunk"]`) -- the
       last wave that ended, also when the round has already opened the next one. The rate falls back to it also while
       the live wave has not finished a first pass.

No measurement is None -- "לא נמדד" on the screen (principle 5), never 0. A row of the removed sender (no ``chunk``
key) is not a chunk wave and is not read. A wave the server did not send (a build machine, #715) has neither.

The verdict (`state`): ``ok`` -- the first passes lost at most `NET_OK_PERMILLE`; ``bad`` -- more than
`NET_BAD_PERMILLE`; ``loss`` -- between; None -- the loss was not measured. ``repair_warn`` -- more repair rounds than
`NET_REPAIR_WARN_ROUNDS`. They are opening estimates in the brain's catalog (T-NET-OK, T-NET-BAD, T-NET-REPAIRS;
Nadav 14/11: technical thresholds are the brain's, not settings), untested on metal. The base is `chunk_rate.LOSS_PERMILLE`,
the loss that cuts the rate of the next wave.
"""

from __future__ import annotations

import json
import sqlite3

from server.agent.registry import ROOM_GROUPS
from server.rounds import drive_rates

#: up to this loss in the first passes (permille) the network is "תקינה": the loss that `chunk_rate` cuts the rate above
#: (`chunk_rate.LOSS_PERMILLE`, the same expression; a test pins the two). Catalog T-NET-OK, an estimate.
NET_OK_PERMILLE = round(drive_rates.LOSS_DOWN_RATIO * 1000)
#: above this loss (permille) the network is "בעייתית": a tenth of the stream did not arrive. Catalog T-NET-BAD -- an
#: estimate from the approved mockup (#3978: 58 permille "יש אבדן", 240 "בעייתית"), not a measurement.
NET_BAD_PERMILLE = 100
#: more repair rounds than this colour the tile (the mockup: `rounds > 2`). Catalog T-NET-REPAIRS, an estimate.
NET_REPAIR_WARN_ROUNDS = 2
#: how many of the round's newest waves are read for the last one the engine kept. A round has a handful.
SCAN = 50

#: what the card reads of a wave, live or kept
NUMBERS = ("chunks", "lost_chunks", "repair_rounds", "multicast_chunks", "fetched_chunks", "first_pass_s",
           "stream_zbytes", "away", "write_kbps")


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value: object) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def verdict(loss_permille: float | None) -> str | None:
    if loss_permille is None:
        return None
    if loss_permille <= NET_OK_PERMILLE:
        return "ok"
    return "bad" if loss_permille > NET_BAD_PERMILLE else "loss"


def network(got: dict | None, *, measuring: bool = False) -> dict | None:
    """The card of one wave -- live (`ChunkEngine.stream_view`) or kept (the ``chunk`` sample); None = no wave."""
    if got is None:
        return None
    if measuring:
        return {"measuring": True, "state": None, "loss_permille": None, "repair_warn": None, "paced_ms": None,
                **{k: None for k in NUMBERS}}
    out = {k: (_number(got.get(k)) if k == "first_pass_s" else _count(got.get(k))) for k in NUMBERS}
    chunks, lost = out["chunks"], out["lost_chunks"]
    loss = 1000 * lost / chunks if chunks and lost is not None else None
    paced = got.get("paced_ms")
    rounds = out["repair_rounds"]
    return {"measuring": False, "state": verdict(loss), "loss_permille": loss,
            "repair_warn": rounds > NET_REPAIR_WARN_ROUNDS if rounds is not None else None,
            "paced_ms": paced if isinstance(paced, dict) and _count(paced.get("wire")) is not None
            and _count(paced.get("disk")) is not None else None, **out}


def recorded(conn: sqlite3.Connection, created_at: str, closed_at: str | None) -> dict | None:
    """The ``chunk`` sample of the newest wave of the round (its window) that the engine kept; None = none."""
    from server.rounds import chunk_rate             # noqa: PLC0415 -- heavy (the brain); only for the room view
    rows = conn.execute(
        f"SELECT w.stream FROM sessions s JOIN wave_rates w ON w.session_id = s.id"
        f" WHERE s.group_id IN {ROOM_GROUPS} AND s.kind = 'multicast'"
        " AND julianday(s.created_at) >= julianday(?) AND julianday(s.created_at) <= julianday(?)"
        " ORDER BY s.created_at DESC, s.rowid DESC LIMIT ?",
        (created_at, closed_at or "9999-12-31", SCAN)).fetchall()
    for row in rows:
        try:
            got = (json.loads(row["stream"]) if row["stream"] else {}).get(chunk_rate.STREAM_KEY)
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(got, dict):
            return got
    return None


def view(conn: sqlite3.Connection, engine, round_row) -> dict:
    """`{"throughput_bps", "network"}` of the round -- live from the engine, else from the last wave it kept."""
    session_id = round_row["wave_session_id"]
    reader = getattr(engine, "stream_view", None)      # an engine that has no such view measures nothing here
    live = reader(session_id) if reader is not None and session_id else None
    last = recorded(conn, round_row["created_at"], round_row["closed_at"])
    sent = _count((last or {}).get("sent_bps"))
    rate = (live or {}).get("throughput_bps")
    if live is not None:
        card = network(live, measuring=not live.get("first_pass"))
    else:
        card = network(last)
    return {"throughput_bps": rate if rate is not None else (sent // 8 if sent is not None else None), "network": card}
