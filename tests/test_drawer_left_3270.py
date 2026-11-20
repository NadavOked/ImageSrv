"""#3270 (metal 18/11, cloner 2): a drawer whose disk the kernel DROPPED is a pulled drawer -- not a failed one -- and comes back to the round.

The hardware test: `echo 1 > /sys/block/sda/device/delete` ("disk-pull.sh pull") in the middle of a write, then a SCSI rescan
three minutes later. The kernel found the disk again at once -- as `sdd`, same serial -- and the agent wrote nothing about it:
the drawer stayed red "נכשל" with "הדיסק דחה כתיבה באמצע הכתיבה", the round never looked for it. The 15/11 delete test showed the same.

It is **not** that a round that is not a production line does not take its disk back. It does -- `drawerback.sh` has no such
gate (#2533, #3108); what a production line adds is a NEW disk (#3181). The cause is the verdict: a dropped disk has its
WRITEs refused ("device offline error") while its node is still in sysfs, and with no DevExch or link-down on its port, so
`drawer_dead_reason` (verdict.sh) says "the kernel rejected a write" -- no `pulled` -- and the drawer was never looked at again.
`_db_found` looks only for a `pulled` drawer; the screen and the server's `pulled` flag need the same word.

`drawer_left_mark` (drawerplug.sh) gives the word later, on evidence: the node has left sysfs, or another disk holds its name.
A disk that still answers under its name is a failed disk, as before.

Two layers: the unit (every case of the evidence), and the real `chunk_partition_drawers` over the harness of
test_chunkrecv_2533.py (`RECV_DIES=sdb:write_error_gone` -- the refused write first, the node only after the round failed the drawer).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH
from test_chunkrecv_2533 import DONE, RECEIVE1, _run
from test_cloner_gui import posix, sh

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

# the refused write comes four seconds into the pass, the node leaves half a second after the drawer failed, and it is pushed
# back as sdc four seconds later -- the kernel says so -- while the pass goes on for eight more (as test_drawer_rejoin_3181.py)
GONE = "RECV_DIES=sdb:write_error_gone RECV_PARTIAL=1 REAL_SLEEP=1 TEST_START_CEILING=30"
BACK = f"{GONE} RECV_BACK_AS=sdc PLUG_EVENT=1 RECV_TAIL=8 SIDE_STATE='4 0-1,3-4'"


# --- the unit: when is "failed on a refused write" a disk that left? ------------------------------------------------------

def _mark(tmp_path: Path, *, node: str | None, markers: tuple[str, ...] = ("dead",), state: str = "failed",
          bound: str | None = "SER-A|1000|2") -> tuple[int, bool]:
    """drawer_left_mark for sda. <node> = what sysfs says of sda: None = no node, else the serial it reads (""= unreadable).
    -> (its exit code, whether the target now has `pulled`)."""
    run = tmp_path / "run"; t = run / "targets" / "sda"; t.mkdir(parents=True)
    (t / "state").write_text(state + "\n", newline="\n")
    for m in markers:
        (t / m).write_text("x\n", newline="\n")
    if bound is not None:
        (t / "bound").write_text(bound + "\n", newline="\n")
    sysroot = tmp_path / "sys"; (sysroot / "sys" / "block").mkdir(parents=True)
    if node is not None:
        (sysroot / "sys" / "block" / "sda").mkdir()
    out = sh(f"export RUN_DIR={posix(run)!r} SYSROOT={posix(sysroot)!r} LOG_FILE={posix(tmp_path / 'log')!r}; "
             + agent_sources("common", "sysinfo", "failmark", "drawerplug") +
             f"disk_serial() {{ echo {node or ''}; }}; drawer_left_mark sda; echo rc=$?")
    assert out.returncode == 0, out.stderr
    rc = int(out.stdout.rsplit("rc=", 1)[1])
    return rc, (t / "pulled").exists()


def test_a_node_that_left_sysfs_after_the_kernel_failed_the_drawer_is_a_pull(tmp_path):
    assert _mark(tmp_path, node=None) == (0, True)


def test_another_disk_under_its_name_is_a_pull_too(tmp_path):
    assert _mark(tmp_path, node="SER-OTHER") == (0, True)


@pytest.mark.parametrize("what, kw", [
    ("it still answers under its name: a failed disk", dict(node="SER-A")),
    ("its serial cannot be read: not shown to have left", dict(node="")),
    ("failed by another word than the kernel's (no `dead`)", dict(node=None, markers=())),
    ("one the round chose another disk for (`foreign`)", dict(node=None, markers=("dead", "foreign"))),
    ("one whose completion failed (`back_failed`)", dict(node=None, markers=("dead", "back_failed"))),
    ("already pulled", dict(node=None, markers=("dead", "pulled"))),
    ("still writing", dict(node=None, state="writing")),
    ("no identity was bound to it", dict(node=None, bound=None)),
])
def test_nothing_that_is_not_shown_to_have_left_is_called_pulled(tmp_path, what, kw):
    """The guards: each is the same drawer as the first test, with one thing taken away."""
    rc, pulled = _mark(tmp_path, **kw)
    if "already pulled" in what:
        assert rc == 1 and pulled, what                    # unchanged, and says nothing
    else:
        assert rc == 1 and not pulled, what


# --- the round: the pass, the report, and the disk coming back ----------------------------------------------------------

def test_a_drawer_the_kernel_dropped_is_reported_pulled_and_waited_for(tmp_path):
    """The report (and with it the screen: grey, "נשלף · ממתין להחזרה") says `pulled` beside `failed` -- as it does for a pull the
    port told of -- once the drawer's node is gone."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=GONE)
    assert "GONE sdb" in got["trace"], got["trace"]
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    out = {t["serial"]: t for t in ended[0]["targets"]}["SER-sdb"]
    assert out["failed"] is True and out["pulled"] is True, ended[0]
    assert "pulled, and waited for (#3270)" in got["log"], got["log"]


def test_a_drawer_the_kernel_dropped_comes_back_to_the_round_under_its_new_name(tmp_path):
    """The metal run: out at 8%, back as `sdd` three minutes later. It is found by its serial, joins the pass on the air, and its
    completion is what it missed -- exactly as for a pull the port told of (test_drawer_rejoin_3181.py)."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=BACK)
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0", got
    assert trace.index("GONE sdb") < trace.index("BACK sdc") < trace.index("SET sdc writing"), trace
    assert "DONE sdc" in trace and "DONE sda" in trace, trace
    assert "המגירה sdc מצטרפת לשידור החי של מחיצה 3 (מעבר 1)" in log, log


def test_a_drawer_that_failed_on_a_refused_write_and_never_left_is_not_pulled(tmp_path):
    """The guard of the round: the same refused write, the node stays -- a failed disk, and nobody waits for it (as before)."""
    got = _run(tmp_path, [RECEIVE1, DONE], env="RECV_DIES=sdb:write_error REAL_SLEEP=1 TEST_START_CEILING=30")
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    out = {t["serial"]: t for t in ended[0]["targets"]}["SER-sdb"]
    assert out["failed"] is True and "pulled" not in out, ended[0]
    assert "pulled, and waited for (#3270)" not in got["log"], got["log"]
