"""#3731 (Nadav 18/11: "חיבור או ניתוק זיהוי מיידי. אם אחרי 2 דקות לא חוזר אז להוריד אותו", הכרעת זאוס בשרשור) --
the network's wait is a DISCONNECT's wait, not any standstill's.

``wait_progress`` (waits.sh) counted every second the stream's counter did not move against the network's ceiling
(WAIT_STREAM_STALL_S, 120). A slow drawer of the SAME machine stops the counter too: fanout, which feeds the stream to the drawers,
stops reading when one drawer's ring is full -- and that drawer has its own timer (fanout's eviction, DISK_STALL_S, 120), which then
raced the network's and took the whole partition down with it. Now (stall.sh ``stream_still_tick``) the network's timer does not run
while a drawer of this machine stands as waiting_disk AND a carrier is READ as up. Everything else runs it, as before: a cable out,
a carrier that cannot be read, no record, a record that cannot be read, and a hold that outlasts WAIT_DRAWER_S.

The loop's naps are stubbed, so 120 "seconds" cost nothing; the stream's process is killed after LIMIT naps, and the question is only
whether wait_progress gave up on it first (rc=1, "פג הזמן") or saw it end (rc=0).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def _box(tmp_path: Path, *, carrier: str | None, drawer: str | None) -> Path:
    """carrier: '1' / '0' / None (no interface readable). drawer: the record's kind, None = no record."""
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n", newline="\n")
    if drawer:
        (run / "targets" / "sda" / "stall").write_text(f"{drawer} {int(time.time()) - 10} 120\n", newline="\n")
    net = tmp_path / "sys" / "class" / "net"
    (net / "lo").mkdir(parents=True)
    (net / "lo" / "carrier").write_text("1\n", newline="\n")   # lo never counts
    (net / "eth0").mkdir()
    if carrier is not None:
        (net / "eth0" / "carrier").write_text(carrier + "\n", newline="\n")
    return run


def _watch(tmp_path: Path, run: Path, limit: int, env: str = "") -> str:
    """wait_progress on a stream that never moves (ceiling 120), ended from outside after ``limit`` naps."""
    return sh(
        f"export RUN_DIR={posix(run)!r} SYSFS_ROOT={posix(tmp_path / 'sys')!r}; unset WAIT_DRAWER_S; " + env
        + agent_sources("common", "timing", "waits", "stall")
        + "log() { echo \"LOG $*\"; }; "
        f"_nap() {{ _n=$((${{_n:-0}} + 1)); if [ \"$_n\" -ge {limit} ]; then kill \"$1\" 2>/dev/null; fi; }}; "
        'sleep 600 & pid=$!; echo 5 > "$RUN_DIR/ctr"; '
        'wait_progress "$pid" "$RUN_DIR/ctr" 600 120 "the stream"; echo "rc=$?"; kill "$pid" 2>/dev/null; true')


def _gave_up(out: str) -> bool:
    return "rc=1" in out and "120 שניות בלי התקדמות" in out


def test_a_drawer_of_this_machine_that_holds_the_stream_does_not_run_the_networks_timer(tmp_path):
    """200 s of standstill with the carrier up and a waiting_disk drawer: the disk's wait (fanout's eviction), not the network's."""
    out = _watch(tmp_path, _box(tmp_path, carrier="1", drawer="waiting_disk"), 200)
    assert "rc=0" in out and "פג הזמן" not in out, out


def test_a_cable_that_is_out_runs_the_timer_even_with_a_drawer_standing(tmp_path):
    """The network's own evidence wins over the disk's (stall_disk_holds, #3594): 120 s and the drawer goes."""
    out = _watch(tmp_path, _box(tmp_path, carrier="0", drawer="waiting_disk"), 200)
    assert _gave_up(out), out


def test_a_standstill_with_no_record_of_a_drawer_runs_the_timer(tmp_path):
    out = _watch(tmp_path, _box(tmp_path, carrier="1", drawer=None), 200)
    assert _gave_up(out), out


def test_a_record_that_is_not_a_disks_runs_the_timer(tmp_path):
    out = _watch(tmp_path, _box(tmp_path, carrier="1", drawer="waiting_net"), 200)
    assert _gave_up(out), out


def test_a_carrier_that_cannot_be_read_is_not_evidence_for_the_hold(tmp_path):
    """Principle 5: "could not check" runs the timer -- today's safe behaviour -- it does not pause it."""
    out = _watch(tmp_path, _box(tmp_path, carrier=None, drawer="waiting_disk"), 200)
    assert _gave_up(out), out


def test_a_hold_that_the_eviction_did_not_end_does_not_wait_for_ever(tmp_path):
    """WAIT_DRAWER_S (here 50) of hold, then the network's 120 count again: a wait without a ceiling is the bug of #49."""
    out = _watch(tmp_path, _box(tmp_path, carrier="1", drawer="waiting_disk"), 400, env="export WAIT_DRAWER_S=50; ")
    assert _gave_up(out), out

