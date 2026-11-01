"""A child that exits while a drawer opens its fifo must not fail the drawer (#2273).

busybox ash opens a command's `< fifo` in the shell itself, and that open
blocks until fanout opens the other end. A child of that shell that exits
meanwhile sends SIGCHLD; ash's open() returns EINTR and ash does not retry it
("can't open .../feed: Interrupted system call"). dash and bash retry.

In `_drawer_pipeline` (drawerfeed.sh) the only child is the fsync watcher
(`writeback_watch &`, #1841). Today it sleeps WRITE_FSYNC_INTERVAL_S before it
can leave -- protection by accident. Here the watcher leaves 0.3s in and
fanout opens the drawers' fifos a second late, so the exit lands inside the
open, every time. The drawer must still be written, whole, and done.

Measured in isolation on the testrunner (31/10, #2248): busybox fails, dash
and bash pass -- so the busybox case is the one that holds the fix.
"""

from __future__ import annotations

import pytest

import test_drawer_single_decoder as feed
from test_agent import posix
from test_drawer_single_decoder import (  # noqa: F401 -- fanout_dir is a fixture
    DISKS, PACKED, RAW, SHELLS, fanout_dir, room, state)
from test_timeouts import log_of

pytestmark = feed.pytestmark   # gcc for the real fanout; serial, like the room it borrows


def _early_exit_in_the_open(fanout_dir) -> str:
    """The watcher exits 0.3s in; every fanout opens its fifos a second late --
    so the exit lands while the drawer's shell is blocked in open(). (An exit
    at once can land before the open and prove nothing.)"""
    real = posix(fanout_dir / "ready" / "fanout")
    return ('writeback_watch() { sleep 0.3; }; '
            'mkdir -p "$BOX/late" && '
            f"printf '#!/bin/sh\\nsleep 1\\nexec %s \"$@\"\\n' '{real}' > \"$BOX/late/fanout\" && "
            'chmod +x "$BOX/late/fanout" && export PATH="$BOX/late:$PATH"; ')


@pytest.mark.parametrize("shape", ["shared", "each"])
@pytest.mark.parametrize("shell", SHELLS)
def test_a_child_exiting_in_the_fifo_open_does_not_fail_the_drawer(tmp_path, fanout_dir, shell, shape):
    box, run, out = room(tmp_path, fanout_dir, shell, tune=f"IMAGESRV_DRAWER_DECODE={shape}\n",
                         before_restore=_early_exit_in_the_open(fanout_dir))
    log = log_of(run)
    assert f"decode={shape}" in log, log
    assert "rc=0" in out.splitlines(), out + log
    for d in DISKS:
        assert state(run, d) != "failed", (d, state(run, d, "error"), log)
        assert (box / "dev" / f"{d}3").read_bytes() == RAW, f"{d}: not every byte"
        assert f"{d} {len(PACKED)}" in out, out
