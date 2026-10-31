"""#1447: the server's timezone as a POSIX TZ string for the agent's GUI.

The initramfs has no zoneinfo and no /etc/localtime, so glibc in native-gui
showed UTC (metal, 16/10: the cloner's clock was 3 h behind). The zone name is
the ``timezone`` setting, else Asia/Jerusalem; the string is the footer of its
TZif file -- tzdata's own rule, not a table in this code.

``None`` means unknown (rule 5: never a guess); the GUI then shows its clock
as UTC with a ``UTC`` label. The file is read once per path per process.

The same zone is the server's own (the DCUI clock, auto-reboot's "03:00"):
the console changes it with ``timedatectl`` and the setting together, and
``apply_on_start`` brings an OS zone that differs -- a server installed with
Debian's Etc/UTC before #1447 -- to the setting. Only the display changes:
the DB keeps UTC timestamps. Every touch of the machine goes through hooks
(``default_hooks``) that tests replace; success is a read-back, not an exit
code (rule 5).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable

from server.db import get_setting, journal

DEFAULT_ZONE = "Asia/Jerusalem"
ZONEINFO = Path("/usr/share/zoneinfo")
#: tzdata's own list: one row per country, the zone in the third column.
ZONE_TAB = "zone.tab"
SETTING = "timezone"

Hooks = dict[str, Callable]

_ZONE_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
_POSIX_RE = re.compile(r"^[A-Za-z0-9<>+\-,./:]{1,64}$")
log = logging.getLogger(__name__)


def zone_name(conn: sqlite3.Connection) -> str:
    value = get_setting(conn, SETTING)
    return value.strip() if value and value.strip() else DEFAULT_ZONE


def _unknown(zone: str, reason: str) -> None:
    log.warning("cannot derive POSIX timezone for %r: %s", zone, reason)


def posix_tz(conn: sqlite3.Connection) -> str | None:
    zone = zone_name(conn)
    if not _ZONE_RE.fullmatch(zone):
        _unknown(zone, "invalid zone name")
        return None
    return _footer(str(ZONEINFO / zone), zone)


@lru_cache(maxsize=16)
def _footer(path: str, zone: str) -> str | None:
    """Every hello asks; the file and the answer (also None) are per process."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        _unknown(zone, str(exc))
        return None
    if not data.startswith(b"TZif"):
        _unknown(zone, "not a TZif file")
        return None
    if len(data) < 5 or data[4:5] not in (b"2", b"3", b"4"):
        _unknown(zone, "unsupported TZif version")
        return None
    if not data.endswith(b"\n"):
        _unknown(zone, "missing TZif footer terminator")
        return None
    start = data.rfind(b"\n", 0, -1)
    footer = data[start + 1:-1] if start >= 0 else b""
    try:
        value = footer.decode("ascii")
    except UnicodeDecodeError:
        _unknown(zone, "non-ASCII TZif footer")
        return None
    if not _POSIX_RE.fullmatch(value):
        _unknown(zone, "invalid TZif footer")
        return None
    return value


def zones(root: Path | None = None) -> list[str]:
    """The zones the console offers: tzdata's zone.tab plus UTC, sorted.
    ``OSError`` when the list cannot be read -- never an empty "no zones"."""
    names = {"UTC"}
    for line in ((root or ZONEINFO) / ZONE_TAB).read_text(encoding="utf-8").splitlines():
        cols = line.split("	")
        if not line.startswith("#") and len(cols) >= 3 and _ZONE_RE.fullmatch(cols[2]):
            names.add(cols[2])
    return sorted(names)


def _timedatectl_zone() -> str | None:
    """The OS zone as systemd reports it; ``None`` = could not read it."""
    try:
        done = subprocess.run(["timedatectl", "show", "-p", "Timezone", "--value"],
                              capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


def _timedatectl_set(zone: str) -> str | None:
    try:
        done = subprocess.run(["timedatectl", "set-timezone", zone],
                              capture_output=True, text=True, timeout=20,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"timedatectl לא רץ: {exc}"
    if done.returncode != 0:
        return f"timedatectl החזיר {done.returncode}: {(done.stderr or done.stdout).strip()}"
    return None


def default_hooks() -> Hooks:
    return {"timezone": _timedatectl_zone, "set_timezone": _timedatectl_set}


def set_system_zone(zone: str, hooks: Hooks) -> str | None:
    """``None`` once the OS reads ``zone`` back, else why not. On success this
    process re-reads the zone too (``time.tzset``): glibc keeps the zone it
    read first, and auto-reboot's local "03:00" lives in this process."""
    error = hooks["set_timezone"](zone)
    if not error:
        seen = hooks["timezone"]()
        if seen != zone:
            error = f"הפקודה הצליחה אך המערכת מדווחת {seen!r} — השינוי לא אומת"
    if not error and hasattr(time, "tzset"):
        time.tzset()
    return error


def apply_on_start(conn: sqlite3.Connection, hooks: Hooks) -> None:
    """The server's start: the OS zone follows the setting (else the default).
    Every outcome other than "already equal" is a journal line."""
    zone = zone_name(conn)
    seen = hooks["timezone"]()
    if seen is None:
        journal(conn, "timezone_apply_failed", f"אזור הזמן של המערכת לא נקרא; הרצוי {zone}")
        return
    if seen == zone:
        return
    error = set_system_zone(zone, hooks)
    if error:
        journal(conn, "timezone_apply_failed", f"{seen} → {zone}: {error}")
    else:
        journal(conn, "timezone_applied", f"{seen} → {zone}")
