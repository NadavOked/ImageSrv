"""#1920 -- the plan the operator sees, and the one run they approve.

``plan`` opens the stick **read-only**, reads its log, and says exactly what a sync would do in each
direction (counts, rejected records with their reason, the files that would be written). It returns a
``digest`` of that plan. Nothing is written by it.

``run`` is the sync: it takes the operator's digest, builds the plan again from what is on the stick
now, and refuses (``PlanChanged``) when it is not the plan they approved -- they approved a
summary, not "whatever is there". Then, in order:

1. stick -> server: every new, valid record in ONE transaction (all or none), stamped
   source=usb, the stick's id, who and when. The same record is never taken twice (its id is the
   primary key); what the server measured itself is another table and is never touched.
2. server -> stick: ``server-sync/*`` is written (each file to ``.new``, fsync, rename; the manifest last)
   with the stick mounted read-write for this step only, then the stick is unmounted, **mounted
   read-only again and every file read back and its sha256 compared** -- written is not the same as
   there (principle 5). Only when nothing changed is nothing written.

A direction that fails says so and does not hide the other's result. ``partial`` means something was
rejected or one direction failed; only ``ok`` is "all done".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from server.db import _settle, _write_lock, journal, now_iso, writing
from server.usbstick import knowledge, records
from server.usbstick.device import Stick, StickError

log = logging.getLogger("imagesrv.usbstick")

#: one reader or writer of the stick at a time: it is one mount point and one device.
LOCK = threading.Lock()
#: how many records the stick's table lists in the console (newest first).
LIST_LIMIT = 200


class PlanChanged(Exception):
    """The stick or the server changed between the summary and the approval."""


class Busy(Exception):
    """Another sync is running."""


@dataclass
class Plan:
    device_id: str
    new: list[records.Record]
    already: int
    reading: records.Reading
    bundle: knowledge.Bundle
    stick_manifest: dict | None
    imported: dict[str, str]        # record_id -> when the server took it (the ones already in)

    def smart_on_stick(self) -> str | None:
        return (self.stick_manifest or {}).get("smart_table_version")

    def changed_files(self) -> list[str]:
        have = {n: v.get("sha256") for n, v in ((self.stick_manifest or {}).get("files") or {}).items()}
        return [n for n, h in self.bundle.hashes().items() if have.get(n) != h]

    def rows(self) -> list[dict]:
        """The stick's log as the console's table: newest first, each said to be new or already in the server."""
        seen, out = set(), []
        for rec in sorted(self.reading.records, key=lambda r: (r.recorded_at, r.record_id), reverse=True):
            if rec.record_id in seen:
                continue
            seen.add(rec.record_id)
            at = self.imported.get(rec.record_id)
            out.append({**rec.stored(), "in_server": at is not None, "imported_at": at})
        return out[:LIST_LIMIT]

    def digest(self) -> str:
        doc = {"device": self.device_id, "new": sorted(r.record_id for r in self.new),
               "rejected": sorted((r.name, r.reason) for r in self.reading.rejected),
               "files": self.bundle.hashes(), "write": self.changed_files()}
        return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:32]

    def public(self) -> dict:
        write = self.changed_files()
        return {
            "device_id": self.device_id, "digest": self.digest(),
            "to_server": {"new": len(self.new), "already": self.already,
                          "rejected": [{"name": r.name, "reason": r.reason} for r in self.reading.rejected],
                          "ignored": self.reading.ignored, "beyond_cap": self.reading.beyond_cap,
                          "total": len(self.reading.records) + len(self.reading.rejected)},
            "to_stick": {"models": self.bundle.models, "smart_version": self.bundle.smart_version,
                         "smart_on_stick": self.smart_on_stick(),
                         "smart_changed": self.bundle.smart_version != self.smart_on_stick(),
                         "files": write, "nothing_to_write": not write},
            "nothing_to_do": not self.new and not write,
        }


def _stored_at(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["record_id"]: r["imported_at"] for r in conn.execute("SELECT record_id, imported_at FROM usb_records")}


def plan(conn: sqlite3.Connection, hooks: dict, stick: Stick) -> Plan:
    """Read-only. Raises ``StickError`` (cannot open/close it) or ``records.ReadError`` (cannot read the log)."""
    with hooks["open"](stick, write=False) as root:
        reading = records.read_reports(root)
        manifest = _read_manifest(root)
    stored = _stored_at(conn)
    seen: set[str] = set()
    new = []
    for rec in reading.records:
        if rec.record_id in stored or rec.record_id in seen:
            continue
        seen.add(rec.record_id)
        new.append(rec)
    already = len({r.record_id for r in reading.records}) - len(new)
    extra = [(r.model, r.controller, r.copy_mbps, r.result) for r in new]
    return Plan(stick.device_id, new, already, reading, knowledge.build(conn, extra), manifest, stored)


def _read_manifest(root: Path) -> dict | None:
    path = Path(root) / knowledge.SYNC_DIR / knowledge.MANIFEST
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with open(path, "rb") as fh:
            return knowledge.parse_manifest(fh.read(1 << 20))
    except OSError:
        return None


def import_records(conn: sqlite3.Connection, new: list[records.Record], device_id: str, user: str) -> int:
    """All of ``new`` or none. Returns how many rows went in (a concurrent twin is skipped, not an error)."""
    if not new:
        return 0
    _settle(conn)
    at = now_iso()
    with _write_lock, writing(conn):
        before = conn.execute("SELECT COUNT(*) FROM usb_records").fetchone()[0]
        conn.executemany(
            # ON CONFLICT on the id only: "or ignore" would also swallow a CHECK/NOT NULL failure and drop that row quietly
            "INSERT INTO usb_records (record_id, source, device_id, recorded_at, imported_at, imported_by,"
            " model, controller, driver, bytes, elapsed_s, copy_mbps, verify_mbps, result, targets, targets_failed)"
            " VALUES (?, 'usb', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (record_id) DO NOTHING",
            [(r.record_id, device_id, r.recorded_at, at, user, r.model, r.controller, r.driver, r.bytes,
              r.elapsed_s, r.copy_mbps, r.verify_mbps, r.result, r.targets, r.targets_failed) for r in new])
        return conn.execute("SELECT COUNT(*) FROM usb_records").fetchone()[0] - before


def _safe_dir(root: Path) -> Path:
    dest = Path(root) / knowledge.SYNC_DIR
    if dest.is_symlink():
        raise StickError("server-sync על ההתקן אינו תיקייה רגילה — לא כתבתי אליו")
    dest.mkdir(exist_ok=True)
    if not dest.is_dir():
        raise StickError("server-sync על ההתקן אינו תיקייה — לא כתבתי אליו")
    return dest


def write_bundle(root: Path, files: dict[str, bytes]) -> None:
    """Only under ``server-sync/``: each file to ``<name>.new``, fsync, rename; ``files`` has the manifest last."""
    dest = _safe_dir(root)
    for name, data in files.items():
        if name not in knowledge_names():
            raise StickError(f"שם קובץ לא צפוי: {name}")
        tmp = dest / (name + ".new")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest / name)


def knowledge_names() -> tuple[str, ...]:
    return (knowledge.RATES, knowledge.SMART_NAME, knowledge.HW_NAME, knowledge.MANIFEST)


def verify_bundle(root: Path, files: dict[str, bytes]) -> list[str]:
    """Names whose bytes on the stick are not the bytes written, read back from the mounted device."""
    bad = []
    for name, data in files.items():
        try:
            with open(Path(root) / knowledge.SYNC_DIR / name, "rb") as fh:
                got = fh.read(len(data) + 1)
        except OSError:
            bad.append(name)
            continue
        if hashlib.sha256(got).hexdigest() != hashlib.sha256(data).hexdigest():
            bad.append(name)
    return bad


def _to_stick(conn, hooks, stick, plan_: Plan, server_version: str) -> dict:
    write = plan_.changed_files()
    # the bundle again: the records just imported are now rows, so it is what the plan promised
    bundle = knowledge.build(conn)
    if bundle.hashes() != plan_.bundle.hashes():
        return {"error": "מה שהשרת יודע השתנה באמצע הסנכרון — לא כתבתי להתקן; סנכרנו שוב"}
    if not write:
        return {"skipped": True, "written": [], "smart_version": bundle.smart_version, "models": bundle.models}
    files = dict(bundle.files)
    files[knowledge.MANIFEST] = knowledge.manifest_bytes(bundle, device_id=stick.device_id, server_version=server_version)
    try:
        with hooks["open"](stick, write=True) as root:
            write_bundle(root, files)
        with hooks["open"](stick, write=False) as root:
            bad = verify_bundle(root, files)
    except StickError as exc:
        return {"error": f"הכתיבה להתקן נכשלה: {exc}"}
    except OSError as exc:      # #2543: ה-strerror ליומן השרת; בתשובה — משפט שלנו
        log.warning("usb stick write failed: %s", exc)
        return {"error": "הכתיבה להתקן נכשלה — הפרטים ביומן השרת"}
    if bad:
        return {"error": "הקריאה החוזרת מההתקן לא תאמה למה שנכתב: " + ", ".join(bad)}
    return {"skipped": False, "written": sorted(files), "verified": True,
            "smart_version": bundle.smart_version, "models": bundle.models}


def run(conn: sqlite3.Connection, hooks: dict, stick: Stick, digest: str, user: str, server_version: str) -> dict:
    if not LOCK.acquire(blocking=False):
        raise Busy()
    try:
        current = plan(conn, hooks, stick)
        if current.digest() != digest:
            raise PlanChanged()
        out: dict = {"device_id": stick.device_id, "by": user}
        try:
            imported = import_records(conn, current.new, stick.device_id, user)
            out["to_server"] = {"imported": imported, "already": current.already + (len(current.new) - imported),
                                "rejected": [{"name": r.name, "reason": r.reason} for r in current.reading.rejected]}
        except sqlite3.Error as exc:
            log.warning("usb stick import failed: %s", exc)        # #2543: טקסט sqlite ליומן
            out["to_server"] = {"imported": 0, "already": current.already,
                                "error": "הרישום בשרת נכשל — הפרטים ביומן השרת",
                                "rejected": [{"name": r.name, "reason": r.reason} for r in current.reading.rejected]}
        out["to_stick"] = ({"error": "לא כתבתי להתקן: הקליטה בשרת נכשלה, והידע שנכתב נגזר ממנה"}
                           if out["to_server"].get("error")
                           else _to_stick(conn, hooks, stick, current, server_version))
        failed = [k for k in ("to_server", "to_stick") if out[k].get("error")]
        clean = not failed and not out["to_server"]["rejected"]
        out["state"] = "ok" if clean else ("failed" if len(failed) == 2 else "partial")
        out["at"] = now_iso()
        _record(conn, out, user)
        return out
    finally:
        LOCK.release()


def _record(conn: sqlite3.Connection, summary: dict, user: str) -> None:
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO usb_syncs (at, by, device_id, state, summary_json) VALUES (?, ?, ?, ?, ?)",
                     (summary["at"], user, summary["device_id"], summary["state"],
                      json.dumps(summary, ensure_ascii=False, sort_keys=True)))
    to_server, to_stick = summary["to_server"], summary["to_stick"]
    journal(conn, "usb_stick_sync",
            f"{summary['state']} {summary['device_id']}: נקלטו {to_server['imported']}, נדחו {len(to_server['rejected'])}"
            f", נכתבו להתקן {len(to_stick.get('written') or [])}"
            + (f" · שרת: {to_server['error']}" if to_server.get("error") else "")
            + (f" · התקן: {to_stick['error']}" if to_stick.get("error") else ""), user)


def last_sync(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT summary_json FROM usb_syncs ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["summary_json"]) if row else None
