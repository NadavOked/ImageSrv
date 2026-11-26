"""#3268 (Nadav 18/11, "אפשר גם לשלב את שתיהן"): what the server remembers of a slow disk, by serial.

The waiting-screen write check measures a disk **now**: 12MiB, then (since 18/11) a sustained part. A disk can still
pass both and be the one that fell behind in the last wave: a cache a few hundred MiB cannot get past, a cable that
only fails under a long stream, a drive that throttles when warm. What the server already knows about it:
`chunk_rate.sustained` (#3295) -- what the drive wrote in the newest clean chunk wave in which the receiver skipped
chunks for it ("the drive was behind", `wave_rates.stream.chunk.behind`), and when. This module only reads it -- no
new table and no new measurement.

**Evidence, not a verdict** (Nadav 18/11, on #3650: "the brain decides if it fell behind last time AND if the check
showed it falls behind now"). The one reader is the brain's rule B62 (`server/brain/slowdisk.py`), which sets it beside
what the check says now. The round does not read this, so nothing is blocked.

**Four answers, not two** (principle 5): ``found`` (a wave it fell behind in), ``none`` (the learning is on and there is
no such wave: "never learned" is not "learned to be fine", and it is said as that), ``off`` (the automatic rate is
switched off: the server learns nothing, so it cannot say there is none) and ``unread`` (the read failed). The serial is
the string the agent sends in the hello, spaces and all (`t10.ATA     APS-SL3N-512 ... SICSD40302WL`, #3574): a dict
key and a SQL parameter here, never a word split on whitespace.
"""

from __future__ import annotations

import logging
import sqlite3
from server.db import SqlConnection
from dataclasses import dataclass

from server.rounds import chunk_rate, drive_rates

log = logging.getLogger("imagesrv.slow_memory")

FOUND, NONE, OFF, UNREAD = "found", "none", "off", "unread"


@dataclass(frozen=True)
class Memory:
    """``state`` is one of the four above. ``mbps`` = what the disk wrote in the wave (MB/s, 10^6), ``at`` = when that wave
    was recorded (ISO 8601, ``wave_rates.at``; ``None`` if its row is gone), ``session_id`` = the wave. ``notes`` = the
    waves ``chunk_rate.sustained`` left out as not measured (the evidence of a decision that used it)."""
    state: str
    mbps: int | None = None
    at: str | None = None
    session_id: str | None = None
    notes: tuple = ()


def of(conn: SqlConnection, serial: str) -> Memory:
    """What the server remembers of the disk with this serial. Never raises: a read that failed is ``unread``."""
    try:
        if not drive_rates.enabled(conn):
            return Memory(OFF)
        notes: list = []
        got = chunk_rate.sustained(conn, serial, notes)
        if got is None:
            return Memory(NONE, notes=tuple(notes))
        mbps, session_id = got
        try:
            row = conn.execute("SELECT at FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
        except sqlite3.Error:
            log.exception("the wave %s of %r has no readable time", session_id, serial)
            row = None
        return Memory(FOUND, mbps, row["at"] if row else None, session_id, tuple(notes))
    except Exception:                       # noqa: BLE001 -- the memory never stops a hello
        log.exception("the memory of %r could not be read", serial)
        return Memory(UNREAD)
