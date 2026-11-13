"""SMART and ATA errors of the server's own disks, on the health page (#3007).

The work server ran a whole day (12/11) on a disk whose SMART said FAILED --
smartd wrote "BACK UP DATA NOW!" every 30 minutes, to a mail command that is
not installed, and the console said nothing. SMART was read only for the
machines' disks (the agent), never for the disk that holds the library and
the DB.

One row per physical disk under the system (``/``) and the library: the
mount's device (``st_dev`` -> ``/sys/dev/block``) and, through a partition,
an md array or a dm device, the disks beneath it. Each row has three states,
by positive evidence (principle 5): ``ok`` only on smartctl's own PASSED,
``bad`` on FAILED or an attribute failing now (by name), ``unknown`` with the
reason when SMART could not be read -- no smartctl, a disk that would not
open, a virtual disk with no SMART (ESXi, Hyper-V). Never "fine" by silence.
An attribute that failed in the past is ``warn``.

Kernel ATA errors and a lowered SATA link this boot are added to the row by
disk name (``ata1.00: ... error``, ``limiting SATA link speed to 3.0 Gbps``),
and said in the server log once per new count. A cumulative counter (199
CRC) is never a verdict on its own (#872): only smartctl's verdicts and this
boot's kernel lines are read.

smartctl runs as the server (root) at most once per ``TTL_S`` -- the health
page polls, a disk does not change that fast.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from server.disks import smart_names
from server.system.health_rows import check

log = logging.getLogger("imagesrv.server_disks")

TTL_S = 300
SMARTCTL_TIMEOUT_S = 20
SYS = Path("/sys")
#: smartctl's exit status bits (smartctl(8) "RETURN VALUES").
OPEN_FAILED = 1 << 1
_ATA_PORT = re.compile(r"/(ata\d+)/")
_LINK = re.compile(r"limiting SATA link speed to ([\d.]+ Gbps)")
_ATA_ERROR = re.compile(r"\b(error|hard resetting link|failed command|exception Emask)\b", re.IGNORECASE)


def _block_dir(path: str, sys_root: Path) -> Path:
    st = os.stat(path)
    return (sys_root / "dev" / "block" / f"{os.major(st.st_dev)}:{os.minor(st.st_dev)}").resolve()


def _disks_under(block: Path) -> list[str]:
    """The whole disks beneath a block device: a partition -> its disk; md/dm -> its slaves."""
    if (block / "partition").exists():
        return [block.parent.name]
    slaves = sorted(p.name for p in (block / "slaves").iterdir()) if (block / "slaves").is_dir() else []
    if not slaves:
        return [block.name]
    found: list[str] = []
    for name in slaves:
        for disk in _disks_under((block / "slaves" / name).resolve()):
            if disk not in found:
                found.append(disk)
    return found


def disks_of(path: str, sys_root: Path = SYS) -> list[str] | None:
    """The disk names under ``path``, or None when they could not be found."""
    try:
        block = _block_dir(path, sys_root)
        if not block.is_dir():
            return None
        return _disks_under(block)
    except (OSError, AttributeError, ValueError):   # AttributeError: os.major off Linux
        return None


def run_smartctl(disk: str) -> tuple[int | None, str, str]:
    """(exit status, stdout, why) -- None = smartctl did not run, ``why`` says so."""
    try:
        got = subprocess.run(["smartctl", "-H", "-A", "-j", f"/dev/{disk}"], capture_output=True,
                             text=True, timeout=SMARTCTL_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, "", "smartctl אינו מותקן בשרת"
    except subprocess.TimeoutExpired:
        return None, "", f"smartctl לא ענה תוך {SMARTCTL_TIMEOUT_S} שניות"
    except OSError as exc:
        return None, "", f"smartctl לא הורץ ({exc.strerror or exc})"
    return got.returncode, got.stdout, ""


def read_kernel_log() -> str | None:
    """This boot's kernel lines about ATA; None = not read (a row says so, never "no errors")."""
    try:
        got = subprocess.run(["journalctl", "-k", "-b", "-o", "cat", "--no-pager", "--quiet", "--grep", r"ata[0-9]"],
                             capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if got.returncode == 1 and not got.stdout:   # --grep: 1 = no line matched -- an answer, not a failure
        return ""
    return got.stdout if got.returncode == 0 else None


def ata_port(disk: str, sys_root: Path = SYS) -> str | None:
    try:
        m = _ATA_PORT.search(str((sys_root / "block" / disk).resolve()) + "/")
    except OSError:
        return None
    return m.group(1) if m else None


def ata_events(kernel: str, port: str) -> tuple[int, str | None]:
    """(error lines, the last lowered link speed) of one ATA port in this boot's kernel log."""
    mine = re.compile(rf"^{port}(?:\.\d+)?: ")
    errors, link = 0, None
    for line in kernel.splitlines():
        if not mine.match(line):
            continue
        speed = _LINK.search(line)
        if speed:
            link = speed.group(1)
        elif _ATA_ERROR.search(line):
            errors += 1
    return errors, link


def _attr_names(table: list[dict], when: str) -> list[str]:
    out = []
    for a in table:
        if a.get("when_failed") == when:
            he, _, known = smart_names.name(int(a.get("id", 0)), a.get("name"))
            out.append(f"{a.get('id')} {a.get('name')}" + (f" ({he})" if known else ""))
    return out


def smart_verdict(rc: int | None, stdout: str, why: str, disk: str) -> tuple[str, str]:
    """(state, detail) from one smartctl -H -A -j run."""
    if rc is None:
        return "unknown", f"{why} — SMART לא נבדק"
    try:
        data = json.loads(stdout)
    except ValueError:
        return "unknown", f"פלט smartctl לא נקרא (rc={rc}) — SMART לא נבדק"
    if rc & OPEN_FAILED:
        return "unknown", f"smartctl לא הצליח לפתוח את /dev/{disk} (rc={rc}) — SMART לא נבדק"
    status = data.get("smart_status")
    if not isinstance(status, dict) or not isinstance(status.get("passed"), bool):
        model = (data.get("model_name") or data.get("scsi_product") or "").strip()
        return "unknown", (f"אין SMART לדיסק הזה{f' ({model})' if model else ''} — בשרת וירטואלי "
                           "בריאות הדיסק נבדקת במארח")
    table = (data.get("ata_smart_attributes") or {}).get("table") or []
    now, past = _attr_names(table, "now"), _attr_names(table, "past")
    if not status["passed"] or now:
        named = f" · נכשל עכשיו: {', '.join(now)}" if now else ""
        return "bad", (f"SMART: {'FAILED' if not status['passed'] else 'PASSED'}{named} — "
                       "הדיסק מדווח על כשל צפוי. לגבות את השרת ולהחליף את הדיסק")
    if past:
        return "warn", f"SMART: PASSED · נכשל בעבר: {', '.join(past)}"
    return "ok", "SMART: PASSED"


def disk_rows(paths: dict[str, str], *, smartctl=run_smartctl, kernel=read_kernel_log,
              sys_root: Path = SYS) -> list[dict]:
    """One row per disk under ``paths`` ({role in Hebrew: path})."""
    roles: dict[str, list[str]] = {}
    rows: list[dict] = []
    for role, path in paths.items():
        disks = disks_of(path, sys_root)
        if disks is None:
            rows.append(check(f"server_disk:{role}", f"דיסק השרת ({role})", "unknown",
                              f"לא נקבע על איזה דיסק יושב {path} — SMART לא נבדק"))
            continue
        for disk in disks:
            roles.setdefault(disk, []).append(role)
    kernel_text = kernel() if roles else ""
    for disk, used in roles.items():
        state, detail = smart_verdict(*smartctl(disk), disk)
        port = ata_port(disk, sys_root)
        if port and kernel_text is None:
            detail += " · יומן הקרנל לא נקרא — שגיאות ATA לא נבדקו"
        elif port:
            errors, link = ata_events(kernel_text, port)
            if errors or link:
                detail += f" · באתחול הזה: {errors} שגיאות ATA ב-{port}" + (
                    f", הקישור הוגבל ל-{link}" if link else "") + " — לבדוק כבל ושקע"
                if state == "ok":
                    state = "warn"
                _say_ata(disk, port, errors, link)
        rows.append(check(f"server_disk:{disk}", f"דיסק השרת {disk} ({', '.join(used)})", state, detail))
    return rows


_said: dict[str, tuple[int, str | None]] = {}


def _say_ata(disk: str, port: str, errors: int, link: str | None) -> None:
    """The server log, by disk name, once per new count (the health page polls)."""
    if _said.get(disk) == (errors, link):
        return
    _said[disk] = (errors, link)
    log.warning("server_disk_ata disk=%s port=%s errors=%d link=%s", disk, port, errors, link or "-")


class Cached:
    """``disk_rows`` at most once per ``ttl`` seconds -- the hook main injects."""

    def __init__(self, paths: dict[str, str], ttl: float = TTL_S, clock=time.monotonic, **kwargs):
        self.paths, self.ttl, self.clock, self.kwargs = paths, ttl, clock, kwargs
        self._lock = threading.Lock()
        self._at: float | None = None
        self._rows: list[dict] = []

    def __call__(self) -> list[dict]:
        with self._lock:
            if self._at is None or self.clock() - self._at >= self.ttl:
                self._rows = disk_rows(self.paths, **self.kwargs)
                self._at = self.clock()
            return list(self._rows)
