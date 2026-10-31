"""#1594 -- ``blockdev --rereadpt`` is refused (EBUSY) while anything holds a
partition of the disk open. ``test_local_clone_engine.py`` failed 1-5 of 5 on
the Testrunner because of it: udev probes the nodes a reread has just made, and
the next write to the same table (``apply_gpt`` then ``expand_last``, one
target after another, five targets at once) came in that window:

    blockdev: ioctl error on BLKRRPART: Device or resource busy
    loop53: the kernel refused to re-read the partition table

``reread_table`` (waits.sh) asks again every second. What it must NOT do is turn
a refusal that lasts into a pass: the kernel's answer stays the answer.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def _reread(tmp_path: Path, refuse: int, tries: int | None = None) -> tuple[int, int, str]:
    """Runs reread_table against a blockdev that refuses `refuse` times and then
    answers; returns (exit code, how many times blockdev was asked, the log)."""
    log = tmp_path / "log"; calls = tmp_path / "calls"
    env = f"REREAD_TRIES={tries}; export REREAD_TRIES; " if tries else ""
    out = sh(f'{env}. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; LOG_FILE={posix(log)!r}; '
             f'blockdev() {{ echo x >> {posix(calls)!r}; '
             f'[ "$(wc -l < {posix(calls)!r})" -gt {refuse} ] && return 0; '
             'echo "blockdev: ioctl error on BLKRRPART: Device or resource busy" >&2; return 1; }; '
             'reread_table /dev/loop9; echo "rc=$?"', timeout=60)
    assert out.returncode == 0, out.stderr
    rc = int(next(l for l in out.stdout.splitlines() if l.startswith("rc=")).split("=")[1])
    return rc, len(calls.read_text().splitlines()), log.read_text() if log.exists() else ""


def test_a_busy_refusal_that_passes_is_asked_again(tmp_path):
    rc, asked, _ = _reread(tmp_path, refuse=2)
    assert (rc, asked) == (0, 3)


def test_an_answer_the_first_time_is_not_asked_twice(tmp_path):
    rc, asked, _ = _reread(tmp_path, refuse=0)
    assert (rc, asked) == (0, 1)


def test_a_refusal_that_lasts_is_the_answer_not_a_pass(tmp_path):
    rc, asked, log = _reread(tmp_path, refuse=99, tries=3)
    assert (rc, asked) == (1, 3)                                # the third refusal ends it
    assert "Device or resource busy" in log, log                # and the kernel's words are in the log
