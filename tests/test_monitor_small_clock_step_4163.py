"""#4163 -- a clock correction BELOW the step threshold must not show a live machine as offline in the monitor.

#3752 made the monitor's presence window two-sided (`0 <= age <= ONLINE_SECONDS`) so a stamp from the future is not "alive" forever. But the
clock-step detector (`clockstep.JUMP_SECONDS`, 60 s) re-bases the stamps only for a step larger than that; a correction of 30 s (chrony,
a VM resume) moves "now" behind a `last_seen` that was written a moment ago, the age becomes negative, and `seen_within` / `_target` say
"not connected" until the machine's next hello or progress re-stamps it (a hello every ~10 s, up to ~25 s for a quiet machine). The monitor list
shows it offline and `/monitor` answers 409 for a machine that never stopped reporting.
Found by a static review of #3752 (Codex 26/11); proved here on the real `machine_rows` / `_target`.

The tolerance is the detector's own threshold: a stamp up to `JUMP_SECONDS` in the future is a correction the detector deliberately ignores; a
bigger one is the detector's to re-base, and stays "not connected" until it does.

**Negative control:** with `server/console/monitor.py` back at the base of this PR the 30 s tests fail on behaviour (`online: False`, a 409).
The 120 s test and the plain freshness tests pass in both states: the guards against "any future stamp is alive".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException

from server.console import monitor
from server.system import clockstep
from test_monitor import MAC, prepare_secret_machine


def _stamped(server) -> tuple[object, datetime]:
    """The machine said hello: its `last_seen`, as the database holds it."""
    prepare_secret_machine(server)
    ctx = server["ctx"]
    row = ctx.conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (MAC,)).fetchone()
    return ctx, datetime.fromisoformat(row["last_seen"])


def _online(ctx, now: datetime) -> bool:
    return [r["online"] for r in monitor.machine_rows(ctx.conn, now) if r["mac"] == MAC][0]


@pytest.mark.parametrize("step", [5, 30, clockstep.JUMP_SECONDS])
def test_a_backward_correction_under_the_threshold_keeps_a_live_machine_online(server, step):
    """(neg) The clock moved back `step` seconds right after the hello: the list says online and the proxy accepts the machine."""
    ctx, seen = _stamped(server)
    now = seen - timedelta(seconds=step)
    assert _online(ctx, now) is True
    assert monitor._target(ctx, MAC, now)[1] in ("build", "cloner")
    assert monitor.seen_within(seen.isoformat(timespec="seconds"), now) is True


def test_a_stamp_further_in_the_future_than_the_detector_threshold_is_still_not_alive(server):
    """The two-sided window of #3752 stays: 120 s ahead is the detector's case (it re-bases it), never evidence of life."""
    ctx, seen = _stamped(server)
    now = seen - timedelta(seconds=clockstep.JUMP_SECONDS + 60)
    assert _online(ctx, now) is False
    with pytest.raises(HTTPException) as err:
        monitor._target(ctx, MAC, now)
    assert err.value.status_code == 409


def test_the_ordinary_window_is_unchanged(server):
    ctx, seen = _stamped(server)
    assert _online(ctx, seen + timedelta(seconds=monitor.ONLINE_SECONDS)) is True
    assert _online(ctx, seen + timedelta(seconds=monitor.ONLINE_SECONDS + 1)) is False
    with pytest.raises(HTTPException):
        monitor._target(ctx, MAC, seen + timedelta(seconds=monitor.ONLINE_SECONDS + 1))
