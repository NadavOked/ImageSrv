"""#3127: a process the server starts says how long it took and how it ended.

The touch map (13-14/11) found the server's `systemctl`/`systemd-run` calls measured by
exit code alone, and `check=True` or a swallowed return code hid *how long* a call
took. `reboot`, `poweroff`, the NUT services and `systemctl enable dnsmasq` all
went through a bare `subprocess.run`: a call that took 25 of its 30 seconds looked
the same as one that took 20 ms, and a timeout left no line of its own.

`run_timed` is `subprocess.run` plus one journal line per call -- the command,
the exit code and the seconds -- at INFO when it succeeded and WARNING when it did
not. It changes nothing else: the same arguments go through, the same
`CompletedProcess` comes back, and every exception (timeout, a non-zero exit with
`check=True`, a binary that is not there) is raised again unchanged, after its own
line. It decides nothing from the result and adds no retry.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Sequence

log = logging.getLogger("imagesrv.proc")


def run_timed(argv: Sequence[str], *, what: str | None = None, **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run(argv, **kwargs)`, with the duration and the result logged.

    `what` names the call in the journal; the default is the first two words of
    `argv` (a unique unit name inside `systemd-run ...` is not a useful label)."""
    name = what or " ".join(str(part) for part in list(argv)[:2])
    started = time.monotonic()
    try:
        done = subprocess.run(argv, **kwargs)
    except subprocess.TimeoutExpired:
        log.warning("%s: timed out after %.1fs (limit %ss)", name, time.monotonic() - started, kwargs.get("timeout"))
        raise
    except subprocess.CalledProcessError as exc:
        log.warning("%s: rc=%s in %.2fs", name, exc.returncode, time.monotonic() - started)
        raise
    except OSError as exc:
        log.warning("%s: could not start (after %.2fs): %s", name, time.monotonic() - started, exc)
        raise
    log.log(logging.INFO if done.returncode == 0 else logging.WARNING,
            "%s: rc=%s in %.2fs", name, done.returncode, time.monotonic() - started)
    return done
