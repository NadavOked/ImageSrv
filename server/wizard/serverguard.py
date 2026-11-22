"""#2583: the explicit rerun of the wizard stops the server for the duration, and brings it back.

The wizard and the running server both take the console port (`server.core.ports.CONSOLE`, #3871), so a
rerun beside a running server could not work (measured 09/11: EADDRINUSE). Nadav, 12/11, after comparing
FOG, ESXi DCUI, Pi-hole and GitLab: none of them fails silently. What is built:

1. No round, ingest, direct session or server transfer is active: say that the server stops for
   the setup and comes back at the end, stop it, and bring it back at the end or on failure --
   also when the wizard is interrupted (`try/finally` in `__main__`, and `ExecStopPost=` in the unit).
2. Work is active: refuse, naming what runs. Nothing is stopped in silence.
3. Positive evidence (principle 5): after the stop, the console port is free and no process holds the DB
   open. "Could not check whether work is active" is a refusal, not a go-ahead.

Starting `imagesrv-wizard-rerun.service` (the documented explicit rerun) is the approval: the web
page cannot ask first, because it cannot listen while the server holds the port.
"""
from __future__ import annotations

import errno
import os
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from server.core import ports as product_ports   # #3871

SERVER_UNIT = "imagesrv-server.service"
#: #3384: the server's own screen (tty1). It opens imagesrv.db too, so a rerun on a server installed from the ISO
#: (firstboot starts it) never saw "the DB is closed" while it ran (argusvm, 17/11).
DCUI_UNIT = "imagesrv-dcui.service"
#: the console port, which the wizard and the server both take (#3871: one source, `server/core/ports.py`).
PORT = product_ports.CONSOLE
NOTICE_HE = "השרת ייעצר לזמן ההגדרה ויחזור בסוף."
DCUI_NOTICE_HE = "גם מסך השרת (DCUI) ייעצר לזמן ההגדרה ויחזור בסוף."


class Refused(Exception):
    """The rerun does not start. The text is for the operator."""


# (sentence, SQL counting the rows that mean "work is running")
_WORK = (
    ("סבב פעיל בחדר המשכפלים", "SELECT COUNT(*) FROM room_rounds WHERE state = 'active'"),
    ("הפצה ישירה פתוחה או רצה", "SELECT COUNT(*) FROM sessions WHERE state IN ('open', 'running')"),
    ("קליטה או שחזור רצים (משימה פתוחה)", "SELECT COUNT(*) FROM tasks WHERE state IN ('pending', 'running', 'verifying')"),
    ("העברת אימג' בין שרתים רצה", "SELECT COUNT(*) FROM storage_transfers WHERE state IN ('queued', 'sending', 'verifying')"),
)


def active_work(db: Path) -> list[str] | None:
    """What runs, as sentences for the operator. [] = checked, nothing runs. None = could not check."""
    if not db.is_file():
        return []                      # a server that never created its DB has no work
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        found = []
        for sentence, sql in _WORK:
            count = conn.execute(sql).fetchone()[0]
            if count:
                found.append(f"{sentence} ({count})")
        return found
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=90, check=False).returncode


def _is_active(unit: str) -> bool | None:
    """True active, False inactive (exit 3, also a unit that does not exist), None = systemctl did not answer."""
    try:
        rc = _systemctl("is-active", "--quiet", unit)
    except (OSError, subprocess.SubprocessError):
        return None
    return True if rc == 0 else False if rc == 3 else None


def unit_active() -> bool | None:
    return _is_active(SERVER_UNIT)


def dcui_active() -> bool | None:
    return _is_active(DCUI_UNIT)


def port_free(port: int = PORT) -> bool | None:
    """Can 127.0.0.1:port be bound now. None = failed for a reason that is not "in use"."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # TIME_WAIT of the stopped server is not a listener
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError as exc:
        return False if exc.errno == errno.EADDRINUSE else None
    finally:
        sock.close()


def db_holders(db: Path) -> list[int] | None:
    """PIDs (not ours) with the DB file open. None = /proc could not be read."""
    root = Path("/proc")
    if not root.is_dir():
        return None
    target, mine, found, saw = str(db), os.getpid(), [], False
    try:
        entries = [e for e in root.iterdir() if e.name.isdigit()]
    except OSError:
        return None
    for entry in entries:
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        saw = True
        for fd in fds:
            try:
                if os.readlink(fd) == target and int(entry.name) != mine:
                    found.append(int(entry.name))
                    break
            except OSError:
                continue
    return sorted(found) if saw else None


def _stop() -> int:
    return _systemctl("stop", SERVER_UNIT)


def _start() -> int:
    return _systemctl("start", SERVER_UNIT)


def _dcui_stop() -> int:
    return _systemctl("stop", DCUI_UNIT)


def _dcui_start() -> int:
    return _systemctl("start", DCUI_UNIT)


@dataclass
class Hooks:
    unit_active: Callable[[], bool | None] = unit_active
    stop: Callable[[], int] = _stop
    start: Callable[[], int] = _start
    port_free: Callable[[], bool | None] = port_free
    active_work: Callable[[Path], list[str] | None] = active_work
    db_holders: Callable[[Path], list[int] | None] = db_holders
    dcui_active: Callable[[], bool | None] = dcui_active
    dcui_stop: Callable[[], int] = _dcui_stop
    dcui_start: Callable[[], int] = _dcui_start


def default_hooks() -> Hooks:
    """A function, so a test can replace what `__main__` talks to."""
    return Hooks()


class Stopped:
    """`restore()` brings the server back once -- and the DCUI after it, if it was running (#3384) -- and says False
    when either could not come back."""

    def __init__(self, hooks: Hooks | None, out: Callable[[str], None], dcui: bool = False):
        self._hooks, self._out, self._dcui = hooks, out, dcui

    def restore(self) -> bool:
        hooks, self._hooks = self._hooks, None
        if hooks is None:
            return True
        ok = self._start(hooks.start, SERVER_UNIT)
        if self._dcui:
            ok = self._start(hooks.dcui_start, DCUI_UNIT) and ok    # the server first: the DCUI reads its DB
        return ok

    def _start(self, start: Callable[[], int], unit: str) -> bool:
        try:
            rc = start()
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc:
            what = "השרת" if unit == SERVER_UNIT else "מסך השרת (DCUI)"
            self._out(f"האשף: {what} לא הופעל בחזרה. להפעיל ידנית: systemctl start {unit}")
        return not rc


def stop_for_setup(db: Path, hooks: Hooks | None = None,
                   out: Callable[[str], None] = print) -> Stopped:
    """Raises `Refused` before anything is stopped when work runs or the check failed; after a
    stop that did not leave the port free and the DB closed, it restores the server and refuses."""
    hooks = hooks or Hooks()
    running = hooks.unit_active()
    if running is None:
        raise Refused("לא הצלחנו לבדוק אם השרת רץ — ההרצה החוזרת לא התחילה.")
    work = hooks.active_work(db)
    if work is None:
        raise Refused("לא הצלחנו לבדוק אם יש סבב, קליטה או הפצה פעילים — השרת לא נעצר.")
    if work:
        raise Refused(" · ".join(work) + ". סיימו או עצרו אותם קודם — השרת לא נעצר.")
    if not running:
        out("השרת כבוי; האשף לא יפעיל אותו בסוף ההגדרה.")
        return Stopped(None, out)
    dcui = hooks.dcui_active()
    if dcui is None:
        raise Refused("לא הצלחנו לבדוק אם מסך השרת (DCUI) פועל — השרת לא נעצר.")
    out(NOTICE_HE)
    stopped = Stopped(hooks, out, dcui=bool(dcui))
    if dcui:
        out(DCUI_NOTICE_HE)
        if hooks.dcui_stop():            # the DCUI first: it shows what the server does, and it holds the DB
            stopped.restore()
            raise Refused("עצירת מסך השרת (DCUI) נכשלה — ההרצה החוזרת לא התחילה.")
    if hooks.stop():
        stopped.restore()
        raise Refused("עצירת השרת נכשלה — ההרצה החוזרת לא התחילה.")
    free, holders = hooks.port_free(), hooks.db_holders(db)
    problem = None
    if free is not True:
        problem = f"פורט {PORT} עדיין תפוס"
    elif holders is None:
        problem = "לא הצלחנו לבדוק שהמסד סגור"
    elif holders:
        problem = "המסד עדיין פתוח בתהליך " + ", ".join(map(str, holders))
    if problem:
        stopped.restore()
        raise Refused(problem + " אחרי עצירת השרת — ההרצה החוזרת לא התחילה והשרת הופעל בחזרה.")
    return stopped
