"""#1920 -- what the server writes to the stick: knowledge about MACHINES, never about disks.

Nadav 20/10 (final): between server and stick only computer-model data moves -- the rate each
model reached, by controller mode where it is known -- plus the SMART knowledge table, which is
general knowledge (#1921), not information about any disk. Nothing keyed by a disk's serial, no
SMART history, no disk list. Every file lands in ``<IMGSRV_DATA>/server-sync/`` and nowhere else:
``reports/`` (the field log, possibly not yet imported), ``settings.json`` and the stick's other
files are never touched.

Files (``manifest.json`` is written last; its ``files`` hold each one's sha256 so the stick, and
the read-back after the write, can check what it got):

* ``rates.json``            -- ``{"schema": 1, "kind": "imagesrv-rates", "entries": [...]}``; an entry
                               is ``{"model", "controller", "basis", "source", "mbps", "samples"}``.
                               ``basis`` says what the number is: ``preflight`` = the server's
                               write check before the stream (per drawer), ``copy`` = a local clone's
                               bytes over the copy stage's seconds. The two are never mixed in one
                               number (principle 5b: a number says where it comes from).
* ``smart-knowledge.tsv`` / ``hw-issues.tsv`` -- the tables of ``agent/lib/data``, byte for byte.
* ``manifest.json``         -- schema, written_at, server_version, smart_table_version, files.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from server.disks import model_slots
from server.disks.smart_names import HW_TABLE, SMART_TABLE

SYNC_DIR = "server-sync"
SCHEMA = 1
MAX_ENTRIES = 500
MANIFEST = "manifest.json"
RATES = "rates.json"
SMART_NAME, HW_NAME = SMART_TABLE.name, HW_TABLE.name


@dataclass(frozen=True)
class Bundle:
    """What would be written: file name -> bytes (without the manifest, which carries a time)."""
    files: dict[str, bytes]
    models: int
    smart_version: str

    def hashes(self) -> dict[str, str]:
        return {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}


def _median(values: list[int]) -> int:
    return int(statistics.median_low(values))


def server_rates(conn: sqlite3.Connection) -> list[dict]:
    """The write-check speed each computer model reached on this server, from ``model_speeds`` (#1845):
    the row's ``mac`` is the cloning machine whose drawers were tested, so its DMI model is the key.
    The controller mode is not something the server reads, so it is ``None`` -- not guessed."""
    models = model_slots.models_by_mac(conn)
    by_model: dict[str, list[int]] = {}
    for row in conn.execute("SELECT mac, mbps FROM model_speeds"):
        model = models.get(row["mac"])
        if model and isinstance(row["mbps"], int) and row["mbps"] > 0:
            by_model.setdefault(model, []).append(row["mbps"])
    return [{"model": m, "controller": None, "basis": "preflight", "source": "server",
             "mbps": _median(v), "samples": len(v)} for m, v in by_model.items()]


def usb_rates(rows: list[tuple]) -> list[dict]:
    """``rows`` = ``(model, controller, copy_mbps, result)`` of the imported records (and, in a
    preview, of the ones about to be). Only a clone that finished fine and measured a copy rate."""
    groups: dict[tuple, list[int]] = {}
    for model, controller, copy_mbps, result in rows:
        if model and result == "ok" and isinstance(copy_mbps, int) and copy_mbps > 0:
            groups.setdefault((model, controller), []).append(copy_mbps)
    return [{"model": m, "controller": c, "basis": "copy", "source": "usb",
             "mbps": _median(v), "samples": len(v)} for (m, c), v in groups.items()]


def stored_usb_rows(conn: sqlite3.Connection) -> list[tuple]:
    return [(r["model"], r["controller"], r["copy_mbps"], r["result"])
            for r in conn.execute("SELECT model, controller, copy_mbps, result FROM usb_records")]


def smart_version(smart: bytes, hw: bytes) -> str:
    """The knowledge table's version: the first 12 hex of the sha256 of both files, in the order the
    agent reads them. A content hash, not a counter -- two servers with the same table agree."""
    return hashlib.sha256(smart + b"\0" + hw).hexdigest()[:12]


def build(conn: sqlite3.Connection, extra_usb_rows: list[tuple] = ()) -> Bundle:
    entries = server_rates(conn) + usb_rates(stored_usb_rows(conn) + list(extra_usb_rows))
    entries.sort(key=lambda e: (e["model"], e["controller"] or "", e["basis"]))
    entries = entries[:MAX_ENTRIES]
    rates = json.dumps({"schema": SCHEMA, "kind": "imagesrv-rates", "entries": entries},
                       ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8") + b"\n"
    smart, hw = SMART_TABLE.read_bytes(), HW_TABLE.read_bytes()
    return Bundle(files={RATES: rates, SMART_NAME: smart, HW_NAME: hw},
                  models=len({e["model"] for e in entries}), smart_version=smart_version(smart, hw))


def manifest_bytes(bundle: Bundle, *, device_id: str, server_version: str, now: datetime | None = None) -> bytes:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {"schema": SCHEMA, "kind": "imagesrv-server-sync", "written_at": stamp,
           "server_version": server_version, "device_id": device_id,
           "smart_table_version": bundle.smart_version, "models": bundle.models,
           "files": {n: {"sha256": h, "size": len(bundle.files[n])} for n, h in sorted(bundle.hashes().items())}}
    return (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def parse_manifest(raw: bytes) -> dict | None:
    """What a previous sync left on the stick, or None when it is not a manifest this version wrote
    (missing, damaged, another schema): the plan then writes everything again, and says so."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or doc.get("kind") != "imagesrv-server-sync":
        return None
    files = doc.get("files")
    if not isinstance(files, dict) or not all(
            isinstance(v, dict) and isinstance(v.get("sha256"), str) for v in files.values()):
        return None
    return doc
