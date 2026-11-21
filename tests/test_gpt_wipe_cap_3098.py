"""#3098: the GPT wipe of a failed drawer (wipe_written_gpt) runs under a ceiling.

`fail_written_target` wipes the primary and backup GPT of a disk this run wrote (#1388), and so does
"cancel this disk" (cancel_drawer). Both commands -- `sgdisk --zap-all` and `blockdev --rereadpt` -- ran
with no ceiling, inside the main loop of both engines (drawers.sh, verdict.sh, chunkrecv.sh). A dying disk
that is still in sysfs is exactly the disk a write does not come back from, and the caller stayed there
with it: no watchdog beat, the other drawers held, the machine rebooted after the lease.

The fix (agent/lib/restore/failmark.sh): both run under `capped_run` (waits.sh) with WAIT_HELPER_S, never
waited for after the kill (a process in D state does not die of kill -9). A wipe that did not end is said
by the disk's name -- "did not end ... not wiped", never "wiped" -- and the drawer fails by its own reason.

Negative control: failmark.sh of lab/vm-1113g (e08f8322) -- the two ceiling tests fail (the call waits the
stub's 20 s out; no line names the disk). The guard (sgdisk refusing at once) passes in both states.
"""

from __future__ import annotations

import time

from native import requires_native
from test_agent import BASH, posix
from test_failmark import IDENT, LIBS, error_of
from test_timeouts import log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

HANG_S = 20          # what a command stuck on a dying disk looks like to the caller
BUDGET_S = 10        # the ceiling below is 1 s per command; the rest is the shell's own start-up

REASON = "partition 3: stream cut"


def _stub(box, name, body):
    return f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{posix(box)}/{name}.calls"\n{body}\n'


def wipe_run(tmp_path, *, sgdisk="exit 0", blockdev="exit 0"):
    box = tmp_path / "box"
    run = box / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n", newline="\n")
    (run / "targets" / "sda" / "write_started").write_text("S3TWNE0JB04745\n", newline="\n")
    (box / "sysroot" / "proc").mkdir(parents=True)
    started = time.monotonic()
    out = run_sh(
        make_stubs(box / "stubs", {"sgdisk": _stub(box, "sgdisk", sgdisk), "blockdev": _stub(box, "blockdev", blockdev),
                                   "dmesg": "#!/bin/sh\n:\n"})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)}/dev SYSROOT={posix(box / 'sysroot')!r} WAIT_HELPER_S=1; "
        + LIBS + IDENT
        + f'target_bind sda; fail_written_target sda "{REASON}"; echo "rc=$?"')
    return box, run, out, time.monotonic() - started


def test_a_wipe_that_does_not_come_back_is_left_and_the_drawer_fails_by_its_own_reason(tmp_path):
    box, run, out, took = wipe_run(tmp_path, sgdisk=f"sleep {HANG_S}")
    assert out.strip().endswith("rc=0"), out
    assert took < BUDGET_S, f"fail_written_target waited {took:.1f}s for a wipe that never ends"
    assert (box / "sgdisk.calls").read_text().split() == ["--zap-all", f"{posix(box)}/dev/sda"]
    assert (run / "targets" / "sda" / "state").read_text().strip() == "failed"
    assert error_of(run, "sda") == REASON
    log = log_of(run)
    assert "sda: the GPT wipe did not end within 1s after restore failure -- not wiped" in log, log
    assert "wiped primary and backup GPT" not in log, log


def test_a_table_reread_that_does_not_come_back_does_not_hold_the_caller(tmp_path):
    box, run, out, took = wipe_run(tmp_path, blockdev=f"sleep {HANG_S}")
    assert out.strip().endswith("rc=0"), out
    assert took < BUDGET_S, f"fail_written_target waited {took:.1f}s for a table re-read that never ends"
    assert (box / "blockdev.calls").read_text().split() == ["--rereadpt", f"{posix(box)}/dev/sda"]
    assert error_of(run, "sda") == REASON
    assert "sda: wiped primary and backup GPT after restore failure" in log_of(run)


def test_a_wipe_that_is_refused_is_said_as_refused_not_as_wiped(tmp_path):
    """The guard, in both states: sgdisk answers at once with a failure -- the old WARNING, no "did not end"."""
    box, run, out, _took = wipe_run(tmp_path, sgdisk="exit 4")
    assert out.strip().endswith("rc=0"), out
    log = log_of(run)
    assert "WARNING: sda: failed to wipe GPT after restore failure" in log, log
    assert "did not end" not in log and "wiped primary" not in log, log
    assert not (box / "blockdev.calls").exists()
    assert error_of(run, "sda") == REASON
