"""#3270 (metal 18/11, cloner 2, n=2): a drawer that came back is not failed again by the kernel's lines from BEFORE it came back.

`since` (progress.sh: the uptime at target_init) is the window of every dmesg verdict (`drawer_dead_reason`, verdict.sh). The refused
WRITE that made the drawer `pulled` is inside it. The disk came back under the SAME name (`sdc`), the agent found it by its serial
(`_db_join`, drawerback.sh) -- and four seconds later the watch of its completion read that same line (same sector, same text),
called it a new failure, and wiped the GPT of the drawer it was completing (lab/metal-1118-r39/runs/B/agent-after-back1.log, 20:37:15 and 20:38:08).

The fix is not about the device LETTER (a disk that came back as another letter has the same window, and a letter another disk
held before can match an old line too): the window opens at the moment the drawer is found again (`drawer_window_open`,
drawerplug.sh), and only the kernel's lines after it count.

The real `_db_join` and the real `drawer_dead_reason`, over a faked sysfs, uptime and dmesg.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlib import agent_sources
from native import requires_native
from test_agent import BASH
from test_cloner_gui import posix, sh

pytestmark = requires_native(("bash", BASH), why="the agent's own functions run under bash")

OLD = "[  500.123456] blk_update_request: device offline error, dev sdc, sector 27432712 op 0x1:(WRITE) flags 0x800 phys_seg 1 prio class 2"
FRESH = "[ 1002.500000] blk_update_request: device offline error, dev sdc, sector 99 op 0x1:(WRITE) flags 0x800 phys_seg 1 prio class 2"


def _back(tmp_path: Path, *, back_as: str = "sdc", dmesg: tuple[str, ...] = (OLD,), uptime: str | None = "1000.50 900.00",
          join: bool = True) -> dict:
    """sdc was pulled mid-write (its refused WRITE at 500 s), and its disk is back as <back_as> at uptime 1000.5 s.
    -> the verdict of drawer_dead_reason for <back_as> over <dmesg> (its exit code and what it printed), and `since` as it is."""
    run = tmp_path / "run"; t = run / "targets" / "sdc"; t.mkdir(parents=True)
    for name, text in (("state", "failed"), ("pulled", ""), ("dead", "x"), ("bound", "SER-A|1000|2"), ("since", "100.00"),
                       ("counter.index", "1"), ("feed.bytes", "0")):
        (t / name).write_text(text + "\n", newline="\n")
    sysroot = tmp_path / "sys"; (sysroot / "sys" / "block" / back_as).mkdir(parents=True); (sysroot / "proc").mkdir()
    if uptime is not None:
        (sysroot / "proc" / "uptime").write_text(uptime + "\n", newline="\n")
    kmsg = tmp_path / "dmesg"; kmsg.write_text("\n".join(dmesg) + "\n", newline="\n")
    out = sh(
        f"export RUN_DIR={posix(run)!r} SYSROOT={posix(sysroot)!r} LOG_FILE={posix(tmp_path / 'log')!r}; "
        + agent_sources("common", "verdict", "drawerback", "drawerplug") +
        # what _db_join calls and this test is not about: the target's identity, its counter, the completion, the notes
        "target_bound_serial() { echo SER-A; }; disk_port() { echo 2; }; target_counter() { :; }; target_bind() { :; }; "
        "target_ident_keep() { :; }; target_set() { echo \"$2\" > \"$RUN_DIR/targets/$1/state\"; }; _db_start() { :; }; _db_note() { :; }; "
        + (f"_db_join sdc {back_as} sdc || echo JOIN-FAILED; " if join else "") +
        f"_t=$RUN_DIR/targets/{back_as if join else 'sdc'}; "
        f"echo \"since=$(cat $_t/since)\"; _r=$(drawer_dead_reason {back_as if join else 'sdc'} 1 {posix(kmsg)!r}); echo \"rc=$?\"; echo \"why=$_r\"")
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    got["log"] = out.stdout   # `log` prints to the console while no screen owns it (common.sh)
    assert "JOIN-FAILED" not in out.stdout, out.stdout
    return got


def test_the_kernel_line_that_failed_it_before_the_pull_is_not_a_new_failure_after_the_return(tmp_path):
    """The metal sequence, same name: the old refused WRITE is in dmesg and after `since`'s old value -- and the drawer is back."""
    got = _back(tmp_path)
    assert got["rc"] == "1", got          # no evidence of death: drawer_dead_reason found nothing since the drawer came back
    assert got["why"] == "", got
    assert float(got["since"]) >= 1000.0, got   # the window opened when the drawer was found again, not at target_init


def test_a_drawer_back_under_another_letter_has_the_same_window(tmp_path):
    """Another letter: the target followed it (`mv`), and an old line of whoever held that letter before can match as well."""
    old_of_sdd = OLD.replace("dev sdc", "dev sdd")
    run_dmesg = (old_of_sdd,)
    got = _back(tmp_path, back_as="sdd", dmesg=run_dmesg)
    assert got["rc"] == "1" and float(got["since"]) >= 1000.0, got


def test_a_write_the_kernel_refuses_after_the_return_still_fails_the_drawer(tmp_path):
    """The guard: the window is not 'never again'. A fresh refused WRITE after the return is evidence, in the same run."""
    got = _back(tmp_path, dmesg=(OLD, FRESH))
    assert got["rc"] == "0", got
    assert "הקרנל דחה כתיבה לכונן" in got["why"] and "sector 99" in got["why"], got   # the fresh line, not the old one


def test_without_the_return_the_old_line_is_the_evidence_it_always_was(tmp_path):
    """The guard of the guard: before the drawer is found again the same line IS the verdict (so the test above is about the return)."""
    got = _back(tmp_path, join=False)
    assert got["rc"] == "0" and "sector 27432712" in got["why"], got


def test_an_uptime_that_is_not_read_keeps_the_old_window_and_says_so(tmp_path):
    """Principle 5: not read is not 'a fresh start'. The old window stays, and the log says the old lines may still count."""
    got = _back(tmp_path, uptime=None)
    assert got["since"] == "100.00", got
    assert "the uptime was not read" in got["log"], got
