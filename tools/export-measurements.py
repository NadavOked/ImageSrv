#!/usr/bin/env python3
"""#2038: an anonymous export of the multicast measurements a server recorded.

  tools/export-measurements.py --db /var/lib/imagesrv/imagesrv.db --out measurements.csv

One row per closed multicast wave. Numbers only: how many machines and drawers,
how long, the stream rate, lost blocks, the per-drive write rates, the drive
models (a product name, not a unit) and the kernel's receive drops.

What it never writes: serial numbers (not even hashed), MAC and IP addresses,
host or station or user names, the college's name, image names, exact dates (the
month only). The wave is numbered 1..N by start time, not by its id.

The guard does not trust the column list: before writing, every cell is checked
for a MAC or an IPv4 address, and for any serial or MAC that this very database
knows. A hit stops the tool (exit 1) and writes nothing; so does a database it
could not read (exit 2). "Nothing exported" is never reported as success.

Publishing is not done here: Nadav sees the whole report first (#2038).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

COLUMNS = [
    "wave", "month", "machines", "drawers", "drawers_done", "drawers_failed",
    "duration_s", "sent_gb", "stream_mb_s", "loss_blocks", "ended",
    "drive_mb_s_min", "drive_mb_s_median", "drive_mb_s_max", "drive_models",
    "receive_drops",
]

MAC = re.compile(r"(?i)\b[0-9a-f]{2}([:-])[0-9a-f]{2}(?:\1[0-9a-f]{2}){4}\b")
IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
MIN_ID = 4          # a known serial shorter than this would match anything


class Refused(Exception):
    pass


def _json(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _t(raw):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _cell(value):
    return "" if value is None else value       # empty = not measured, which is not 0


def rows(conn: sqlite3.Connection) -> list[dict]:
    waves = conn.execute(
        "SELECT s.id, s.started_at, s.closed_at, w.stream, w.drops, w.delivered "
        "FROM sessions s JOIN wave_rates w ON w.session_id = s.id "
        "WHERE s.kind = 'multicast' AND s.state = 'closed' AND s.started_at IS NOT NULL "
        "ORDER BY s.started_at, s.id").fetchall()
    out = []
    for n, w in enumerate(waves, 1):
        stream = _json(w["stream"]) or {}
        delivered = _json(w["delivered"]) or {}
        drops = _json(w["drops"]) or {}
        rates = [r["rate_bps"] / 1e6 for r in conn.execute(
            "SELECT rate_bps FROM drive_rates WHERE session_id = ?", (w["id"],))]
        models = sorted({r["model"] for r in conn.execute(
            "SELECT model FROM drive_rates WHERE session_id = ?", (w["id"],)) if r["model"]})
        start, end = _t(w["started_at"]), _t(w["closed_at"])
        counted = [d for d in delivered.values() if isinstance(d, dict)]
        drop_vals = [sum(v for k, v in d.items() if k in ("rcvbuf", "inerrors") and isinstance(v, int))
                     for d in drops.values() if isinstance(d, dict)]
        failed = [d.get("failed_drawers") for d in drops.values() if isinstance(d, dict)
                  and isinstance(d.get("failed_drawers"), int)]
        sent, bps = stream.get("sent_bytes"), stream.get("stream_bps")
        out.append({
            "wave": n,
            "month": start.strftime("%Y-%m") if start else None,
            "machines": conn.execute("SELECT COUNT(*) FROM session_members WHERE session_id = ?",
                                     (w["id"],)).fetchone()[0],
            "drawers": sum(d.get("drawers", 0) for d in counted) if counted else None,
            "drawers_done": sum(d.get("done", 0) for d in counted) if counted else None,
            "drawers_failed": sum(failed) if failed else None,
            "duration_s": round((end - start).total_seconds()) if start and end else None,
            "sent_gb": round(sent / 1e9, 2) if isinstance(sent, (int, float)) else None,
            "stream_mb_s": round(bps / 1e6, 1) if isinstance(bps, (int, float)) else None,
            "loss_blocks": stream.get("loss_blocks") if isinstance(stream.get("loss_blocks"), int) else None,
            "ended": stream.get("ended"),
            "drive_mb_s_min": round(min(rates), 1) if rates else None,
            "drive_mb_s_median": round(statistics.median(rates), 1) if rates else None,
            "drive_mb_s_max": round(max(rates), 1) if rates else None,
            "drive_models": ";".join(models) if models else None,
            "receive_drops": sum(drop_vals) if drop_vals else None,
        })
    return out


def known_identifiers(conn: sqlite3.Connection) -> set[str]:
    """Everything this database could use to name a unit or a machine."""
    found: set[str] = set()
    for table, column in (("disk_inventory", "serial"), ("disk_inventory", "mac"), ("drive_rates", "serial"),
                          ("machines", "mac"), ("session_members", "mac")):
        try:
            found |= {str(r[0]).lower() for r in conn.execute(f"SELECT DISTINCT {column} FROM {table}")
                      if r[0] and len(str(r[0])) >= MIN_ID}
        except sqlite3.OperationalError:
            continue            # a table this server version does not have: nothing to learn from it
    return found


def check(data: list[dict], known: set[str]) -> None:
    for row in data:
        for column, value in row.items():
            text = str(_cell(value)).lower()
            if MAC.search(text) or IPV4.search(text):
                raise Refused(f"wave {row['wave']}, column {column}: looks like a MAC or an IP address")
            for ident in known:
                if ident in text:
                    raise Refused(f"wave {row['wave']}, column {column}: contains an identifier this database knows")


def export(db: Path, out: Path) -> int:
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        data = rows(conn)
        check(data, known_identifiers(conn))
    except sqlite3.Error as exc:
        print(f"cannot read {db}: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"refused, nothing written: {exc}", file=sys.stderr)
        return 1
    if not data:
        print("refused, nothing written: no closed multicast wave with recorded rates in this database", file=sys.stderr)
        return 1
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows({k: _cell(v) for k, v in row.items()} for row in data)
    print(f"{len(data)} waves written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Anonymous export of multicast measurements (#2038).")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    if not args.db.is_file():
        print(f"no such database: {args.db}", file=sys.stderr)
        return 2
    return export(args.db, args.out)


if __name__ == "__main__":
    sys.exit(main())
