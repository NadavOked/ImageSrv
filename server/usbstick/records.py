"""#1920 -- the clone stick's log, read under a hard schema (Nadav 20/10).

The stick keeps one ``reports/clone-<YYYYMMDD>-<HHMMSS>.json`` per clone
(``usb_report_save``, agent/lib/usb/usbdiag.sh): the machine's DMI model, the
controller and its driver, bytes and stage times -- **never a disk's serial or SMART**.
Here that file is DATA ONLY: a JSON object, every key and type checked against the
lists below. An unknown key, a key that names a disk's serial or SMART, a wrong type or
a number out of range rejects the whole record, aloud and with the reason -- a
half-read record is not "imported" (principle 5). No free text from the stick is
stored (``error``, ``ntfs_note``, ... are accepted by the schema and dropped), and
nothing in it is a path or a command: the file name is matched by a fixed pattern
and the content is never executed or used as a path.

The record id is a hash of what is kept, so the same report is never taken twice and
two sticks that carry the same record agree on it. It does not depend on the stick.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from server.disks.model_slots import model_key

REPORTS_DIR = "reports"
REPORT_RE = re.compile(r"^clone-(\d{8})-(\d{6})\.json$")
#: one report is about a kilobyte; anything near this is not one.
MAX_FILE_BYTES = 64 * 1024
#: reports read in one pass (newest first); the rest are counted and said, not skipped silently.
MAX_FILES = 5000

TOP_KEYS = frozenset({
    "mac", "source", "targets", "shrunk", "source_restore", "stopped", "bytes", "elapsed_s",
    "shrink_path", "snapshot_teardown", "info_s", "resize_s", "relocations_mb", "copy_s",
    "verify_s", "restore_s", "machine", "controller"})
SOURCE_KEYS = frozenset({"dev", "port"})
TARGET_KEYS = frozenset({"dev", "port", "state", "error", "ntfs_note"})
MACHINE_KEYS = frozenset({"vendor", "product"})
SECONDS_KEYS = ("info_s", "resize_s", "copy_s", "verify_s", "restore_s")
#: key names that mean a disk's identity or health -- named in the rejection (Nadav 20/10).
FORBIDDEN_FRAGMENTS = ("serial", "smart", "wwn", "wwid", "uuid", "guid")
#: what the stick writes in place of a line it could not show clean (usbdiag.sh USB_ID_GONE).
_STICK_GONE = "מזהה דיסק"

_MAX_INT = 1 << 50
_MAX_SECONDS = 10_000_000
_FREE_TEXT = 2000
_DRIVER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DISK_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")


@dataclass(frozen=True)
class Record:
    record_id: str
    recorded_at: str            # UTC, from the file name (the stick's own clock, unverified)
    model: str | None           # the DMI key the server uses (server.disks.model_slots); None = no usable one
    controller: str | None      # "ahci" | "ide" | "other" | None = not read
    driver: str | None
    bytes: int | None
    elapsed_s: int | None
    copy_mbps: int | None
    verify_mbps: int | None
    result: str                 # "ok" | "failed" | "stopped"
    targets: int
    targets_failed: int

    def stored(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class Rejected:
    name: str
    reason: str                 # Hebrew, for the operator


@dataclass
class Reading:
    records: list[Record] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    ignored: int = 0            # entries in reports/ that are not clone-*.json files
    beyond_cap: int = 0         # reports older than the newest MAX_FILES, not read


class ReadError(Exception):
    """The reports could not be read at all -- not 'there are none'."""


class _Bad(Exception):
    pass


def _key_name(key: object) -> str:
    text = str(key)
    return text if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", text) else "לא תקין"


def _check_keys(obj: dict, allowed: frozenset, where: str) -> None:
    for key in obj:
        low = str(key).lower()
        if any(part in low for part in FORBIDDEN_FRAGMENTS):
            raise _Bad(f"שדה {_key_name(key)} אסור בסכמה")
        if key not in allowed:
            raise _Bad(f"שדה {_key_name(key)} אינו בסכמה ({where})")


def _int(value: object, name: str, hi: int = _MAX_INT) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= hi:
        raise _Bad(f"הערך של {name} אינו מספר שלם בטווח")
    return value


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise _Bad(f"הערך של {name} אינו true/false")
    return value


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise _Bad(f"הערך של {name} אינו טקסט תקין")
    return value


def parse_controller(raw: object) -> tuple[str | None, str | None]:
    """``"<mode>|<disk>=<driver> ..."`` (``usb.ctrl`` of the stick) -> (kind, driver).

    The kind is read from the drivers the way ``usb_ctrl_kind`` does: ``ata_piix`` / ``ata_generic``
    / ``pata_*`` is IDE (one drawer behind it is enough, as the stick says "IDE mode"), ``ahci`` on
    every drawer is AHCI, anything else is "other". Empty = not read: a controller nobody read is
    not "AHCI"."""
    if not isinstance(raw, str):
        raise _Bad("הערך של controller אינו טקסט")
    if raw.strip() in ("", "|"):
        return None, None
    mode, sep, drivers = raw.partition("|")
    if not sep or mode not in ("", "ide", "other"):
        raise _Bad("הערך של controller אינו בפורמט המוכר")
    found = []
    for part in drivers.split():
        disk, eq, driver = part.partition("=")
        if not eq or not _DISK_RE.fullmatch(disk) or not _DRIVER_RE.fullmatch(driver):
            raise _Bad("הערך של controller אינו בפורמט המוכר")
        found.append(driver)
    if not found:
        return None, None
    ide = [d for d in found if d in ("ata_piix", "ata_generic") or d.startswith("pata_")]
    if ide:
        return "ide", ide[0]
    if all(d == "ahci" for d in found):
        return "ahci", "ahci"
    return "other", found[0]


def _rate(total: int | None, seconds: int | None) -> int | None:
    """MB/s the way the stick's diag computes it: bytes / seconds / 1e6. None = not measured."""
    return total // seconds // 1_000_000 if total and seconds else None


def _stamp(name: str) -> str:
    m = REPORT_RE.fullmatch(name)
    if not m:
        raise _Bad("שם הקובץ אינו clone-<תאריך>-<שעה>.json")
    try:
        when = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        raise _Bad("התאריך בשם הקובץ אינו תקין") from None
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _no_constants(token: str):
    raise ValueError(token)


def parse_report(name: str, raw: bytes) -> Record:
    """One report file -> a Record, or ``_Bad`` with the reason (never a half record)."""
    recorded_at = _stamp(name)
    if len(raw) > MAX_FILE_BYTES:
        raise _Bad("הקובץ גדול מדי לדוח שיכפול")
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _Bad("הקובץ אינו JSON תקין") from None
    if not isinstance(data, dict):
        raise _Bad("הקובץ אינו אובייקט JSON")
    _check_keys(data, TOP_KEYS, "הדוח")
    machine = data.get("machine")
    if not isinstance(machine, dict):
        raise _Bad("חסר המחשב (machine)")
    _check_keys(machine, MACHINE_KEYS, "machine")
    source = data.get("source", {})
    if not isinstance(source, dict):
        raise _Bad("הערך של source אינו אובייקט")
    _check_keys(source, SOURCE_KEYS, "source")
    targets = data.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 64:
        raise _Bad("חסרים יעדים (targets)")
    states = []
    for target in targets:
        if not isinstance(target, dict):
            raise _Bad("יעד אינו אובייקט")
        _check_keys(target, TARGET_KEYS, "targets")
        if target.get("state") not in ("done", "failed"):
            raise _Bad("מצב יעד אינו done או failed")
        for free in ("error", "ntfs_note"):
            if free in target:
                _text(target[free], f"targets.{free}", _FREE_TEXT)
        _int(target.get("port"), "targets.port", 4096)
        states.append(target["state"])
    for free in ("mac", "source_restore", "snapshot_teardown"):
        if free in data:
            _text(data[free], free, _FREE_TEXT)
    if data.get("shrink_path") not in (None, "snapshot", "refused"):
        raise _Bad("הערך של shrink_path אינו מוכר")
    vendor = _text(machine.get("vendor", ""), "machine.vendor", 128)
    product = _text(machine.get("product", ""), "machine.product", 128)
    stopped = _bool(data.get("stopped", False), "stopped")
    _bool(data.get("shrunk", False), "shrunk")
    total = _int(data.get("bytes"), "bytes")
    elapsed = _int(data.get("elapsed_s"), "elapsed_s", _MAX_SECONDS)
    secs = {k: _int(data.get(k), k, _MAX_SECONDS) for k in SECONDS_KEYS}
    _int(data.get("relocations_mb"), "relocations_mb")
    kind, driver = parse_controller(data.get("controller", ""))
    model = None if _STICK_GONE in product else model_key(
        {"product_name": product.strip(), "sys_vendor": vendor.strip()})
    failed = states.count("failed")
    fields = {"recorded_at": recorded_at, "model": model, "controller": kind, "driver": driver,
              "bytes": total, "elapsed_s": elapsed, "copy_mbps": _rate(total, secs["copy_s"]),
              "verify_mbps": _rate(total, secs["verify_s"]),
              "result": "stopped" if stopped else ("failed" if failed else "ok"),
              "targets": len(states), "targets_failed": failed}
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:32]
    return Record(record_id=digest, **fields)


def read_reports(root: Path) -> Reading:
    """Every report under ``<root>/reports`` (newest first). A directory that is not there is
    an empty log; one that cannot be read is ``ReadError`` -- the two are different answers."""
    out = Reading()
    base = Path(root) / REPORTS_DIR
    if not base.exists():
        return out
    try:
        names = []
        with os.scandir(base) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False) and REPORT_RE.fullmatch(entry.name):
                    names.append(entry.name)
                else:
                    out.ignored += 1
    except OSError as exc:
        raise ReadError(f"לא הצלחתי לקרוא את תיקיית הדוחות: {exc.strerror or exc}") from exc
    names.sort(reverse=True)
    out.beyond_cap = max(0, len(names) - MAX_FILES)
    for name in names[:MAX_FILES]:
        try:
            with open(base / name, "rb") as fh:
                raw = fh.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            out.rejected.append(Rejected(name, f"הקובץ לא נקרא: {exc.strerror or exc}"))
            continue
        try:
            out.records.append(parse_report(name, raw))
        except _Bad as exc:
            out.rejected.append(Rejected(name, str(exc)))
    return out
