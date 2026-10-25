"""#1970 -- SMART short self-test of the drawers while the cloner idles.

Nadav 22/10: the result joins the SMART verdict (a dying disk shows before it is
written); a round that starts cancels it. Verified 22/10: the round's cancel
first STOPS the background loop and waits for it, and only then aborts (-X) --
the first version sent -X and then killed the loop, so a loop between the two
could start a test after the abort, under the write. A cancel during the wait
leaves the verdict alone; a test the disk refused is logged and judged as before.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import run_sh

pytestmark = requires_native(("bash", BASH))


def _env(tmp_path: Path, smartctl_body: str = "exit 0") -> str:
    run, bindir = tmp_path / "run", tmp_path / "bin"
    (run / "smart").mkdir(parents=True, exist_ok=True)
    bindir.mkdir(exist_ok=True)
    tool = bindir / "smartctl"
    tool.write_text(f'#!/bin/sh\necho "$*" >> {posix(tmp_path / "calls")!r}\n{smartctl_body}\n',
                    encoding="utf-8", newline="\n")
    run_sh(f"chmod +x {posix(tool)!r}")
    return (f"export PATH={posix(bindir)!r}:$PATH RUN_DIR={posix(run)!r} DEVROOT=/fake-dev SMARTCTL=smartctl "
            "D_ROLE=cloner; "   # common.sh: LOG_FILE=$RUN_DIR/agent.log
            f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/smartselftest.sh; "
            "list_drawers() { echo sda; echo sdb; }; "
            f'smart_probe() {{ echo "probe $1" >> {posix(tmp_path / "calls")!r}; echo fail; }}; ')


def _calls(tmp_path: Path) -> list[str]:
    p = tmp_path / "calls"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_each_drawer_is_tested_once_and_its_verdict_read_again(tmp_path):
    (tmp_path / "run/smart").mkdir(parents=True)
    (tmp_path / "run/smart/sda.v").write_text("ok\n")
    run_sh(_env(tmp_path) + "SMART_SHORT_WAIT_S=0; smart_selftest_one sda; smart_selftest_one sda")
    assert _calls(tmp_path) == ["-t short /fake-dev/sda", "probe sda"]
    assert not (tmp_path / "run/smart/sda.v").exists(), "the cached verdict was not dropped"
    assert "SMART after the short self-test: fail" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")


def test_a_test_the_disk_refuses_leaves_the_verdict_and_says_so(tmp_path):
    (tmp_path / "run/smart").mkdir(parents=True)
    (tmp_path / "run/smart/sda.v").write_text("ok\n")
    run_sh(_env(tmp_path, "exit 4") + "SMART_SHORT_WAIT_S=0; smart_selftest_one sda")
    assert _calls(tmp_path) == ["-t short /fake-dev/sda"]
    assert (tmp_path / "run/smart/sda.v").exists()
    assert "was not started (rc=4)" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")


def test_round_cancel_stops_the_loop_before_the_abort_and_nothing_starts_after(tmp_path):
    """The round's cancel comes while `smartctl -t short` on sda is still being
    issued (2 s, a slow disk). The abort (-X) must come AFTER that command ended
    -- an abort sent first is followed by a test that starts under the write.
    sdb never starts, and the loop is gone when the cancel returns."""
    calls = posix(tmp_path / "calls")
    body = f'[ "$1" = -t ] && sleep 2; echo "end $*" >> {calls!r}'
    out = run_sh(_env(tmp_path, body) + f"""
SMART_SHORT_WAIT_S=30; smart_selftest_idle_start
i=0; while [ ! -s {calls!r} ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
p=$_smart_selftest_pid; smart_selftest_cancel; echo CANCELLED >> {calls!r}
kill -0 "$p" 2>/dev/null && echo ALIVE
sleep 3; echo "rc=$?"
""", timeout=60)
    assert _calls(tmp_path) == ["-t short /fake-dev/sda", "end -t short /fake-dev/sda",
                                "-X /fake-dev/sda", "end -X /fake-dev/sda", "CANCELLED"], _calls(tmp_path)
    assert "ALIVE" not in out


def test_an_abort_that_is_not_confirmed_is_said_aloud(tmp_path):
    (tmp_path / "run/smart").mkdir(parents=True)
    (tmp_path / "run/smart/sda.selftest").write_text("")
    run_sh(_env(tmp_path, "exit 4") + "smart_selftest_cancel")
    assert "abort not confirmed (rc=4)" in (tmp_path / "run/agent.log").read_text(encoding="utf-8")


def test_not_a_cloner_runs_nothing(tmp_path):
    run_sh(_env(tmp_path) + "D_ROLE=build; SMART_SHORT_WAIT_S=0; smart_selftest_idle_start; sleep 1")
    assert _calls(tmp_path) == []
