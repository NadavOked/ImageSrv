"""#3140 -- a cloner whose LAST live drawer is pulled stays in the round for a while, and the drawer that comes back completes.

VM lab 14/11 (room_75941455, MC4): one drawer failed the write check, the other -- the last -- was pulled and pushed
back 22 seconds later. The machine left the round 10 seconds after the pull (`pass no-target`, then `client_failed`),
before the drawer came back, and the completion in the background (#3112) was lost. MC5, with two live drawers, saw the
same pull and the same return and ended whole: whether a pulled drawer was forgiven depended on the drawer next to it.

The real `chunk_partition_drawers` and `drawer_back` run here (`agent/lib/restore/chunkrecv.sh`, `drawerback.sh`,
`drawerhold.sh`) against the harness of test_chunkrecv_2533.py: `chunkcast`, the server, sysfs and dmesg are fakes. What
is asserted is what the operator and the server see: the machine is told the targets failed and is NOT dropped, the
drawer is found by its serial, completes over HTTP, and is signed done; and the ceiling -- which is the server's
`lost_wait_seconds` -- is the longest the machine stays. Not that a number of seconds passed exactly: the order and the
margins (#2118).

**Negative control:** reverting `chunkrecv.sh` and `timing.sh` and deleting `drawerhold.sh` fails the behavioural tests
here (the machine returns 1 at the pass's end); the guards `[write error]`, `[another disk]` and the timing's `bad value`
pass in both states.
"""

from __future__ import annotations

import json

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH, fake_machine, posix, sh  # noqa: F401  (fake_machine is a fixture)
from test_agent_timing_2056_2059 import LIBS, _cut, _path, answer
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

GONE = "הכונן נעלם מהמערכת (נשלף או נותק)"
#: sdb failed before the pass (the write check); sda, the last one, vanishes two seconds into it and keeps two chunks
LAST_PULLED = "PRE_FAILED=sdb RECV_DIES=sda:vanish RECV_PARTIAL=1 REAL_SLEEP=1 TEST_START_CEILING=30"
#: seconds from the pull to the pass's own end in the harness (2 + 2 + 1): what the machine did before it could wait at all
PASS_TAIL = 5


def _stayed(tmp_path) -> int:
    """How long after the pull the machine left the partition -- the harness's two epoch files."""
    died = int((tmp_path / "died_at").read_text())
    return int((tmp_path / "returned_at").read_text()) - died


def test_the_last_drawer_pulled_and_back_after_the_pass_completes_and_the_machine_stays_in(tmp_path):
    """The Issue, as the lab saw it, with the return where the lab had it: after the pass ended. The machine does not
    leave; it tells the server the targets failed, finds the drawer by its serial under its new name, completes it
    over HTTP (one `apply`, on that drawer only) and signs it done."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{LAST_PULLED} RECV_BACK_LATE=4:sdc DRAWER_HOLD_S=60")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "PART-RETURNED rc=0" in trace, got
    assert GONE in next(t for t in trace if t.startswith("FAIL sda")), trace       # it failed when it was pulled, by name
    assert trace.index("PASS-END") < trace.index("BACK sdc") < trace.index("SET sdc writing"), trace   # back AFTER the pass
    applies = [t for t in trace if t.startswith("chunkcast apply")]
    assert len(applies) == 1 and "/dev/sdc3" in applies[0] and "/dev/sda3" not in applies[0], applies
    assert trace.index(applies[0]) < trace.index("ntfs_boot sdc 3 ntfs") < trace.index("DONE sdc"), trace
    assert "FAIL sdc" not in "\n".join(trace) and not any(t.startswith("sgdisk") for t in trace), trace
    # the server heard `ended` with BOTH targets failed -- the wave does not wait for a machine with nothing more to say
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    assert {t["serial"]: t["failed"] for t in ended[0]["targets"]} == {"SER-sda": True, "SER-sdb": True}, ended
    # the positive evidence: the log
    assert "the machine stays in the round up to 60s" in log and "a pulled drawer is back and completed" in log, log
    assert "מגירה 3 חזרה (serial SER-sda, sdc)" in log and "המגירה sdc הושלמה עד הסוף ברקע" in log, log
    assert (tmp_path / "targets" / "sdc" / "dev").read_text().strip() == "sda"     # the report keeps the name it was chosen by
    assert (tmp_path / "targets" / "sdb" / "state").read_text().strip() == "failed"   # the one that failed the write check stays out
    assert _stayed(tmp_path) >= PASS_TAIL + 4, "it was in the round while the drawer was out"


def test_a_drawer_that_never_comes_back_is_waited_for_up_to_the_ceiling_and_not_longer(tmp_path):
    """DRAWER_HOLD_S is the server's lost_wait_seconds (timing.sh). Before: the machine left at the pass's end.
    Now it leaves at the ceiling -- failed, by the pull's reason -- and the server has already been told why."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{LAST_PULLED} DRAWER_HOLD_S=6")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=1" and "PART-RETURNED rc=1" in trace, got
    assert "no drawer came back in 6s -- the machine leaves the round" in log, log
    # whole epoch seconds on both ends: one second of slack below, and well above what the old code did (the pass's end)
    assert PASS_TAIL + 6 - 1 <= _stayed(tmp_path) <= PASS_TAIL + 6 + 15, _stayed(tmp_path)
    assert not any(t.startswith(("DONE", "SET sdc", "chunkcast apply")) for t in trace), trace
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    assert ended and {t["failed"] for t in ended[0]["targets"]} == {True}, ended   # the server knew, while it waited


def test_a_drawer_back_as_another_disk_does_not_join_and_the_wait_ends_at_the_ceiling(tmp_path):
    """The identity rule of #3064 holds here too: another serial in the slot is not the drawer, said once, not written."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{LAST_PULLED} RECV_BACK_LATE=2:sdc:SER-other DRAWER_HOLD_S=8")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=1", got
    assert not any(t.startswith(("DONE", "SET sdc", "chunkcast apply")) for t in trace), trace
    assert log.count("דיסק אחר הוכנס באמצע הסבב (sdc, serial SER-other)") == 1, log
    assert _stayed(tmp_path) >= PASS_TAIL + 8 - 1, "another disk is not a reason to leave early"


def test_the_round_closing_ends_the_wait_at_once(tmp_path):
    """The hold is a wait like the others of the loop: the server closing the round is heard."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{LAST_PULLED} CLOSE_LATE=2 DRAWER_HOLD_S=120")
    assert got["trace"][-1] == "rc=1", got
    assert _stayed(tmp_path) < PASS_TAIL + 25, _stayed(tmp_path)


def test_a_drawer_lost_to_a_write_error_is_not_waited_for(tmp_path):
    """Guard against waiting for what cannot come back: only `pulled` holds. A write error is a drawer that failed, as before."""
    got = _run(tmp_path, [RECEIVE1, DONE], env="PRE_FAILED=sdb RECV_DIES=sda:write REAL_SLEEP=1 TEST_START_CEILING=30 DRAWER_HOLD_S=60")
    assert got["trace"][-1] == "rc=1", got
    assert "stays in the round" not in got["log"], got["log"]
    assert _stayed(tmp_path) < PASS_TAIL + 15, _stayed(tmp_path)


# --- the ceiling is the server's: lost_wait_seconds ---------------------------------------------------------------

def _hold(machine, timing: dict, env: str = "") -> str:
    """DRAWER_HOLD_S after the agent's own `read_answer` took one hello answer -- the path of test_agent_timing_2056_2059.py."""
    run = machine["run"]
    (run / "answer.json").write_text(json.dumps(answer(**timing)), encoding="utf-8")
    out = sh(_path(run) + "unset WAIT_STREAM_STALL_S STREAM_STALL_TIMEOUT IDLE_POWEROFF_S END_POWEROFF_S DRAWER_HOLD_S; "
             f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/agent.log; ' + env
             + agent_sources(*LIBS) + "min_free_take() { :; }\n" + _cut("read_answer")
             + f'RESP={posix(run)!r}/response.json; cp {posix(run)!r}/answer.json "$RESP"; read_answer; echo "hold=$DRAWER_HOLD_S"')
    return next(ln for ln in out.splitlines() if ln.startswith("hold=")).removeprefix("hold=")


@pytest.mark.parametrize("lost", [180, 240, 1800])
def test_the_ceiling_follows_the_servers_lost_wait(fake_machine, lost):
    assert _hold(fake_machine, {"lost_wait_seconds": lost}) == str(lost)


def test_without_the_servers_value_it_is_the_servers_default(fake_machine):
    from server.agent import timing
    assert _hold(fake_machine, {}) == str(timing.RANGES[timing.LOST][0]) == "180"


@pytest.mark.parametrize("bad", [179, 1801, 0, "0240", 240.5, True, "x"])
def test_a_bad_value_is_the_default_and_never_0_or_forever(fake_machine, bad):
    assert _hold(fake_machine, {"lost_wait_seconds": bad}) == "180"


def test_an_environment_override_wins_like_every_other_wait(fake_machine):
    assert _hold(fake_machine, {"lost_wait_seconds": 600}, env="export DRAWER_HOLD_S=7; ") == "7"
