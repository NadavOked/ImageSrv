"""#1847: the room clock's early re-send and the no-wake alert, over the `wol_wakes` rows.

Split out of `server/learn/wol.py` (#2531): the sweep sends packets through `server.net.wol`, which
records every wake through `server.learn.wol` -- one module that imports both closes no cycle.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from server.agent import held
from server.db import _write_lock, journal, writing
from server.learn import wol as learn_wol
from server.net import wol
from server.system import steadyclock


def sweep(conn: sqlite3.Connection, send=None, now: float | None = None) -> None:
    """משעון החדר: שליחה חוזרת מוקדמת, והתראה על רצף. לעולם אינו זורק."""
    wall = time.time() if now is None else now
    now = steadyclock.now() if now is None else now
    send = send or wol._send_broadcast
    try:
        for row in conn.execute(
                "SELECT id, mac, sent_epoch FROM wol_wakes WHERE answered_at IS NULL"
                " AND early = 0 AND sent_epoch > ?", (now - learn_wol.ANSWER_S,)).fetchall():
            typical = learn_wol.stats(conn, row["mac"], now)["typical_s"]
            if typical is None or now - row["sent_epoch"] < learn_wol.EARLY_FACTOR * typical:
                continue
            if held.is_held(conn, row["mac"], datetime.fromtimestamp(wall, timezone.utc)):
                continue          # #3590: the hold began after the wave's WoL; it is lit, not asleep
            reason = wol._try_send(row["mac"], send)
            with _write_lock, writing(conn):
                conn.execute("UPDATE wol_wakes SET early = 1 WHERE id = ?", (row["id"],))
            journal(conn, "wol_resent",
                    f"{row['mac']} after={int(now - row['sent_epoch'])}s typical={int(typical)}s"
                    + (f" failed: {reason}" if reason else ""), machine=row["mac"])
        for (mac,) in conn.execute("SELECT DISTINCT mac FROM wol_wakes").fetchall():
            got = learn_wol.stats(conn, mac, now)
            if got["streak"] < learn_wol.FAIL_STREAK:
                continue
            newest = conn.execute(
                "SELECT id, alerted FROM wol_wakes WHERE mac = ? AND answered_at IS NULL"
                " AND sent_epoch <= ? ORDER BY sent_epoch DESC LIMIT 1",
                (mac, now - learn_wol.ANSWER_S)).fetchone()
            if newest is None or newest["alerted"]:
                continue          # פעם אחת לרצף — ההערה האחרונה בו נושאת את הסימן
            with _write_lock, writing(conn):
                conn.execute("UPDATE wol_wakes SET alerted = 1 WHERE id = ?", (newest["id"],))
            journal(conn, "wol_no_wake", f"{mac} {learn_wol.NO_WAKE_HE.format(n=got['streak'])}",
                    machine=mac)
    except Exception:             # noqa: BLE001 — שעון החדר ממשיך
        learn_wol.log.exception("wol sweep failed")
