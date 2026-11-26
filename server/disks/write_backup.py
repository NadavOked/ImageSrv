"""#3268 (Nadav 14/11): the write check on the waiting screen keeps a copy.

The check writes 3 x 4MiB and a few 4KiB markers on a disk nobody chose for a
round. Before it writes, the agent reads exactly those regions, sends them here
(one body: the originals, then what the check will write there -- the same
lengths, region by region -- + the list of regions + sha256) and waits for
``ok`` with the same sha256: no confirmation, no write. After the check it puts
the originals back, reads them back, compares, and only then deletes the copy
here. A copy still open (power cut in the middle) is put back by the agent
block by block, only where the check's pattern still is (#3271 H3).

The agent never asks to delete a copy it could not put back whole: a copy with
blocks that hold something else is marked ``stale`` (``POST .../{id}/stale``)
and **kept** -- the copy is the only record of what was there.

Stored by **serial** (not by device name, which changes): one open copy per
serial. A second POST for a serial that still has one is refused (409) -- the
agent restores the first, it never stacks. Files, not a table (principle 3):
``<data>/write-backups/<hex(serial)>/<id>.bin`` + ``<id>.json``, each fsynced
with its folder before ``ok`` (#3271 M4). A record that cannot be read is not
"no copy": the listing answers 500 and a new copy is refused (principle 5).

**#3268 (Nadav 18/11): the sustained part of the check** keeps a second kind of copy, ``kind: "long"``:
ONE region of some hundred MiB (the originals) followed by the one short pattern the check writes over it,
**repeated** (so the pattern is stored once, not once per MiB). It is too large to read into memory: it is
streamed to a file while its sha256 is computed, and only then stored (``POST /write-backup/long``). Same
rules as the short copy: one open copy per serial, ``ok`` only after fsync, kept until the agent says the
restore was verified. The listing, the blob, the release and the stale mark are shared.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac
from server.agent import registry
from server.agent.api import ServerContext, identity_gate
from server.core.logtext import one_line
from server.db import journal

log = logging.getLogger("imagesrv.write_backup")
#: #3891: a record that cannot be read is said by name; the exception's text (a path, json's or the OS's words)
#: goes to the server log, never into the answer (CodeQL py/stack-trace-exposure)
UNREADABLE_OPEN = "an open copy of this serial cannot be read -- the details are in the server log"
UNREADABLE_COPY = "this copy cannot be read -- the details are in the server log"

MAX_BYTES = 64 * 1024 * 1024
#: #3283 (L1): a ceiling for the whole ``write-backups/`` folder (about 80 copies of 24MiB). Over it a new
#: copy is refused (507) -- the agent then does not write (no ``ok``, no write), and the check is "unchecked".
#: #3268 (18/11): 8GiB -- six cloners with four drawers each, a 256MiB sustained copy per drawer, open at the same time, is ~6GiB.
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024
#: #3268: the sustained copy -- the region (and the pattern) are never read into memory; this is its own ceiling.
MAX_LONG_BYTES = 1280 * 1024 * 1024
_ALIGN = 4096
#: #3300 (N3): the check for an open copy and the write of the new one are one step. Two POSTs for the
#: same serial (twin disks in two cloners) never both pass the check.
_PUT_LOCK = threading.Lock()
_REGIONS = re.compile(r"^[0-9]+:[0-9]+(,[0-9]+:[0-9]+)*$")
_ONE_REGION = re.compile(r"^([0-9]+):([0-9]+)$")
_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class UnreadableCopy(Exception):
    """A record of a copy that is there and cannot be read -- never "no copy"."""


def _dir(root: Path, serial: str) -> Path:
    return root / "write-backups" / serial.encode("utf-8").hex()


def _meta(path: Path) -> dict:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnreadableCopy(f"{path.name}: {exc}") from exc
    if not isinstance(meta, dict):
        raise UnreadableCopy(f"{path.name}: not an object")
    return meta


def _durable(path: Path, data: bytes) -> None:
    """Written, fsynced, renamed into place, and the folder fsynced: an ``ok`` a crash can take back is no ``ok``."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    if hasattr(os, "O_DIRECTORY"):   # POSIX (the server); a folder cannot be opened for fsync on Windows
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _feed(f, digest, chunk: bytes) -> None:
    digest.update(chunk)
    f.write(chunk)


def _commit_long(folder: Path, tmp: Path, bid: str, serial: str, meta: dict, want: int, root: Path):
    """The copy of a sustained check goes from its temporary file to a stored one: one step under the lock, the
    blob first and the record last. Returns ``(code, error, status)`` on a refusal, ``None`` when it is stored."""
    with _PUT_LOCK:
        try:
            if open_for(root, serial):
                return "open_copy", "this serial still has an open copy -- restore it first", 409
        except UnreadableCopy as exc:
            log.warning("write-backup of %s: %s", one_line(serial), one_line(exc))
            return "unreadable", UNREADABLE_OPEN, 500
        if stored_bytes(root) + want > MAX_TOTAL_BYTES:
            return "quota", "the folder of write-check copies is full", 507
        with open(tmp, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())   # the blob is on disk before it is named a copy (M4)
        os.replace(tmp, folder / f"{bid}.bin")
        _durable(folder / f"{bid}.json", json.dumps(meta).encode("utf-8"))
    return None


def open_for(root: Path, serial: str) -> list[dict]:
    """The open copies of this serial (empty = none). A record without its blob is not a copy (the blob is
    written first and removed last). A record that is there and cannot be read raises ``UnreadableCopy``."""
    out = []
    for path in sorted(_dir(root, serial).glob("*.json")):
        if path.with_suffix(".bin").is_file():
            out.append(_meta(path))
    return out


def stored_bytes(root: Path) -> int:
    """The bytes of every copy under ``write-backups/`` (the blobs; a record is a few hundred bytes)."""
    total = 0
    for blob in (root / "write-backups").glob("*/*.bin"):
        try:
            total += blob.stat().st_size
        except OSError:      # removed between the glob and the stat: not stored any more
            continue
    return total


def create_write_backup_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1/agent")

    def _refuse(code: str, error: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": error, "code": code}, status_code=status)

    def _gate(request: Request, mac: str):
        canonical = lenient_mac(mac)
        if canonical is None:
            return None, _refuse("bad_mac", "missing or malformed mac")
        refused = identity_gate(ctx, canonical, request, "write-backup")
        if refused is not None:
            return canonical, refused
        # #3283 (L1): only a registered cloner keeps, reads, marks or drops a copy. The copy is of a cloner's
        # disk; a student machine or a build machine that passed the identity gate has no business with it.
        machine = registry.lookup(ctx.conn, canonical)
        if machine is None or machine["role"] != "cloner":
            return canonical, _refuse("not_a_cloner", "only a registered cloner uses write-backup", 403)
        return canonical, None

    @router.post("/write-backup")
    async def put(request: Request, mac: str = "", serial: str = "", size: int = 0,
                  regions: str = "", sha256: str = "") -> JSONResponse:
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        if ctx.data_dir is None:
            return _refuse("no_storage", "no data dir", 503)
        if not serial or size <= 0 or not _REGIONS.match(regions) or not _SHA.match(sha256):
            return _refuse("bad_query", "serial, size, regions and sha256 are all required")
        want = 2 * sum(int(r.split(":")[1]) for r in regions.split(","))   # the originals, then what the check writes there
        if want > MAX_BYTES:
            return _refuse("bad_body", "the regions are more than the cap")
        buf = bytearray()
        async for chunk in request.stream():   # never more than the cap + one chunk in memory
            buf += chunk
            if len(buf) > want:
                break
        body = bytes(buf)
        if len(body) != want:
            return _refuse("bad_body", f"body is {len(body)} bytes, the regions say {want} (originals + patterns)")
        got = hashlib.sha256(body).hexdigest()
        if got != sha256:
            return _refuse("bad_sha256", "the body does not match the sha256 it came with")
        with _PUT_LOCK:   # #3300: the check and the write are one step
            try:
                if open_for(ctx.data_dir, serial):
                    return _refuse("open_copy", "this serial still has an open copy -- restore it first", 409)
            except UnreadableCopy as exc:   # cannot tell whether one is open: no second one
                log.warning("write-backup of %s: %s", one_line(serial), one_line(exc))
                return _refuse("unreadable", UNREADABLE_OPEN, 500)
            if stored_bytes(ctx.data_dir) + len(body) > MAX_TOTAL_BYTES:   # #3283
                return _refuse("quota", "the folder of write-check copies is full", 507)
            folder = _dir(ctx.data_dir, serial)
            folder.mkdir(parents=True, exist_ok=True)
            bid = uuid.uuid4().hex
            # the blob first, the record last: a record without its blob is never listed (open_for)
            _durable(folder / f"{bid}.bin", body)
            meta = {"id": bid, "serial": serial, "size": size, "regions": regions, "sha256": got,
                    "mac": canonical, "created": int(time.time())}
            _durable(folder / f"{bid}.json", json.dumps(meta).encode("utf-8"))
        journal(ctx.conn, "write_backup_saved", f"{canonical} serial={serial} bytes={len(body)} id={bid}")
        return JSONResponse({"ok": True, "id": bid, "sha256": got})

    @router.post("/write-backup/long")
    async def put_long(request: Request, mac: str = "", serial: str = "", size: int = 0,
                       regions: str = "", pattern: int = 0, sha256: str = "") -> JSONResponse:
        """#3268 (18/11): the copy of the sustained part: ONE region (the originals) + the pattern written over it
        (``pattern`` bytes, repeated by the agent over the region). Streamed to a file, never held in memory."""
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        if ctx.data_dir is None:
            return _refuse("no_storage", "no data dir", 503)
        one = _ONE_REGION.match(regions)
        if not serial or size <= 0 or one is None or pattern <= 0 or not _SHA.match(sha256):
            return _refuse("bad_query", "serial, size, one region, pattern and sha256 are all required")
        off, length = int(one.group(1)), int(one.group(2))
        if length <= 0 or off % _ALIGN or length % _ALIGN or pattern % _ALIGN or off + length > size or pattern > length:
            return _refuse("bad_query", "the region and the pattern must be 4KiB aligned, inside the disk, the pattern not longer than the region")
        want = length + pattern
        if want > MAX_LONG_BYTES:
            return _refuse("bad_body", "the region is more than the cap")
        folder = _dir(ctx.data_dir, serial)
        folder.mkdir(parents=True, exist_ok=True)
        bid = uuid.uuid4().hex
        tmp = folder / f"{bid}.bin.tmp"   # not "*.bin": it is no copy, and the quota does not count it
        digest, got = hashlib.sha256(), 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():   # the cap + one chunk in memory, whatever the agent sends
                    got += len(chunk)
                    if got > want:
                        break
                    await run_in_threadpool(_feed, f, digest, chunk)
            if got != want:
                return _refuse("bad_body", f"body is {got} bytes or more, the region and the pattern say {want}")
            if digest.hexdigest() != sha256:
                return _refuse("bad_sha256", "the body does not match the sha256 it came with")
            meta = {"id": bid, "serial": serial, "size": size, "regions": regions, "sha256": sha256, "kind": "long",
                    "pattern": pattern, "mac": canonical, "created": int(time.time())}
            error = await run_in_threadpool(_commit_long, folder, tmp, bid, serial, meta, want, ctx.data_dir)
        finally:
            tmp.unlink(missing_ok=True)
        if error is not None:
            return _refuse(error[0], error[1], error[2])
        journal(ctx.conn, "write_backup_saved", f"{canonical} serial={serial} bytes={want} id={bid} kind=long")
        return JSONResponse({"ok": True, "id": bid, "sha256": sha256})

    @router.get("/write-backup")
    def listing(request: Request, mac: str = "", serial: str = "") -> JSONResponse:
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        if ctx.data_dir is None or not serial:
            return _refuse("bad_query", "serial is required")
        try:
            return JSONResponse({"ok": True, "backups": open_for(ctx.data_dir, serial)})
        except UnreadableCopy as exc:   # "could not read" is not "none": the agent puts nothing back and checks nothing
            log.warning("write-backup of %s: %s", one_line(serial), one_line(exc))
            return _refuse("unreadable", UNREADABLE_OPEN, 500)

    @router.get("/write-backup/{bid}/blob")
    def blob(request: Request, bid: str, mac: str = "", serial: str = ""):
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        path = _dir(ctx.data_dir, serial) / f"{bid}.bin" if ctx.data_dir and _ID.match(bid) else None
        if path is None or not path.is_file():
            return _refuse("no_copy", "no such copy", 404)
        return FileResponse(path, media_type="application/octet-stream")

    @router.delete("/write-backup/{bid}")
    def drop(request: Request, bid: str, mac: str = "", serial: str = "") -> JSONResponse:
        """Only after the agent put the disk back and verified it: every block the original again."""
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        if ctx.data_dir is None or not _ID.match(bid):
            return _refuse("bad_query", "bad id")
        folder = _dir(ctx.data_dir, serial)
        if not (folder / f"{bid}.json").is_file():
            return _refuse("no_copy", "no such copy", 404)
        (folder / f"{bid}.json").unlink()   # the record first: from here it is no longer an open copy
        (folder / f"{bid}.bin").unlink(missing_ok=True)
        journal(ctx.conn, "write_backup_released",
                f"{canonical} serial={serial} id={bid} -- restored and verified by the agent")
        return JSONResponse({"ok": True})

    @router.post("/write-backup/{bid}/stale")
    def stale(request: Request, bid: str, mac: str = "", serial: str = "", blocks: int = 0) -> JSONResponse:
        """#3271 H3: blocks hold something else (an image written since, another disk). The agent put back only
        where the check's pattern was; the copy is **kept**, marked. Journaled once, not at every boot."""
        canonical, refused = _gate(request, mac)
        if canonical is None or refused is not None:
            return refused
        if ctx.data_dir is None or not _ID.match(bid) or blocks <= 0:
            return _refuse("bad_query", "bad id or blocks")
        path = _dir(ctx.data_dir, serial) / f"{bid}.json"
        if not path.is_file():
            return _refuse("no_copy", "no such copy", 404)
        try:
            meta = _meta(path)
        except UnreadableCopy as exc:
            log.warning("write-backup %s: %s", one_line(path), one_line(exc))
            return _refuse("unreadable", UNREADABLE_COPY, 500)
        if "stale" not in meta:
            meta["stale"] = {"blocks": blocks, "at": int(time.time()), "mac": canonical}
            _durable(path, json.dumps(meta).encode("utf-8"))
            journal(ctx.conn, "write_backup_stale",
                    f"{canonical} serial={serial} id={bid} blocks_not_ours={blocks} -- התיישן: put back only where it "
                    f"was the check's pattern; the copy is kept")
        return JSONResponse({"ok": True})

    return router
