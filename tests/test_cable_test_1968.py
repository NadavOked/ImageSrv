"""#1968 -- ethtool --cable-test when the link is slow or dropped. #3663 (A10): the brain decides, the agent runs.

The test drops the link for seconds, so hello never runs it on a live link
(interfaces.md, netprobe). Verified 22/10: the first version ran it from hello
whenever carrier_changes > 0 -- which is every link that ever came up (the
count is >= 1 from boot) -- i.e. every 900 s, mid-round too. Now: hello keeps
"skipped" on a live link; cable_live_check runs the test from the cloner's idle
branch only, no round open and no task, once per boot -- and **only when the
server's brain said RUN** (the hello answer's link_verdict.cable_test, rule K-CABLE:
server/brain/linkverdict.py, tests/test_link_verdict_3663.py). Whether the link is
slow or dropped is the brain's judgement and is tested there; what stays here is that
the agent runs exactly what it was told, and nothing when it was told nothing. A NIC
that cannot test is "unchecked", never ok.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from test_netprobe import _stub, netprobe_of

pytestmark = requires_native(("bash", BASH))


def _nic(sysroot: Path, speed: str, changes: str | None) -> None:
    nic = sysroot / "sys/class/net/eth0"
    (nic / "carrier").write_text("1\n")
    (nic / "speed").write_text(speed + "\n")
    if changes is not None:
        (nic / "carrier_changes").write_text(changes + "\n")


def _verdict(run: Path, cable_test: str | None, why: str = "slow: 100Mb/s") -> None:
    """The last hello answer, as the agent keeps it. ``None`` = an answer with no link_verdict (an old server, a rule that failed)."""
    body: dict = {"schema": 1}
    if cable_test is not None:
        body["link_verdict"] = {"banner": "NONE", "cable_test": cable_test, "cable_why": why}
    (run / "response.json").write_text(json.dumps(body), newline="\n")


def _live(sysroot: Path, run: Path, ethtool: Path, env: str = "") -> str:
    return sh(f"export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(run)!r} ETHTOOL={posix(ethtool)!r} "
              f"LOG_FILE={posix(run / 'log')!r} IFACE=eth0 D_ROLE=cloner D_SESSION_STATE=none D_TASK=null {env}; "
              f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/netprobe.sh; . {posix(AGENT)}/lib/hw/cablelive.sh; "
              "cable_live_check; echo ok")


def test_hello_never_tests_a_live_link_even_slow_and_flapping(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    _nic(sysroot, "100", "7")
    called = tmp_path / "ethtool.called"
    ethtool = _stub(tmp_path, "ethtool", f"echo ran > {posix(called)!r}; exit 0")
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", ethtool=ethtool)
    assert p["cable"] == {"skipped": "link up"}
    assert not called.exists(), "ethtool --cable-test ran on a live link from hello"


def test_idle_cloner_tests_the_link_once_when_the_brain_said_run_and_hello_serves_it(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "2")
    _verdict(run, "RUN")
    count = tmp_path / "count"
    ethtool = _stub(tmp_path, "ethtool", f"echo x >> {posix(count)!r}; echo 'netlink error: Operation not supported' >&2; exit 95")
    _live(sysroot, run, ethtool)
    _live(sysroot, run, ethtool)   # the verdict is still RUN until the next hello says the test ran: never twice
    assert count.read_text().split() == ["x"], "tested more than once per boot"
    assert json.loads((run / "cable-live.json").read_text()) == {"status": "unchecked", "reason": "unsupported"}
    p = netprobe_of(sysroot, extra=f"export IFACE=eth0 RUN_DIR={posix(run)!r}; ", ethtool=ethtool)
    assert p["cable"] == {"status": "unchecked", "reason": "unsupported"}
    assert "slow: 100Mb/s" in (run / "agent.log").read_text(), "the log does not say why the brain asked for it"


def test_the_pair_is_reported_when_the_brain_said_run(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "1000", "4")
    _verdict(run, "RUN", "the link dropped (2 -> 4)")
    ethtool = _stub(tmp_path, "ethtool", 'echo "Pair C code Open, length 12m"')
    _live(sysroot, run, ethtool)
    assert json.loads((run / "cable-live.json").read_text()) == {
        "status": "open", "pairs": [{"pair": "C", "code": "Open", "length_m": 12}]}
    assert not (run / "netprobe.json.at").exists()


@pytest.mark.parametrize("cable_test", [None, "NOT_NOW", "TESTED", "HEALTHY", "NOT_MEASURED", "run", "", "RUNNING"])
def test_the_agent_holds_no_threshold_a_slow_dropped_link_is_not_tested_without_a_run(fake_machine, tmp_path, cable_test):
    """The link is 100 Mb/s and carrier_changes rose (the old agent tested it on its own): nothing but the brain's RUN runs the test."""
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "9")
    (run / "carrier0.eth0").write_text("2\n")
    _verdict(run, cable_test)
    called = tmp_path / "ethtool.called"
    ethtool = _stub(tmp_path, "ethtool", f"echo ran > {posix(called)!r}; exit 0")
    _live(sysroot, run, ethtool)
    assert not called.exists(), f"link_verdict.cable_test={cable_test!r} ran the cable test"
    assert not (run / "cable-live.json").exists()


def test_no_verdict_at_all_is_no_test(fake_machine, tmp_path):
    """No response.json yet, an unreadable one, or an answer from an old server: the test drops the link, so what was not decided is not done."""
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "9")
    called = tmp_path / "ethtool.called"
    ethtool = _stub(tmp_path, "ethtool", f"echo ran > {posix(called)!r}; exit 0")
    _live(sysroot, run, ethtool)                                           # no file
    (run / "response.json").write_text("{not json", newline="\n")
    _live(sysroot, run, ethtool)                                           # not json
    assert not called.exists()


def test_never_with_a_round_open_a_task_or_off_the_cloner_even_when_the_brain_said_run(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "9")
    _verdict(run, "RUN")
    called = tmp_path / "called"
    ethtool = _stub(tmp_path, "ethtool", f"echo ran > {posix(called)!r}")
    for env in ("D_SESSION_STATE=open", "D_TASK=restore", "D_ROLE=build"):
        _live(sysroot, run, ethtool, env)
        assert not called.exists(), env


def test_a_failed_test_on_a_down_link_is_still_an_error_not_unchecked(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("0\n")
    ethtool = _stub(tmp_path, "ethtool", "echo 'netlink error: No such device' >&2; exit 19")
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", ethtool=ethtool)
    assert p["cable"] == {"error": "ethtool --cable-test rc 19"}


def test_cablelive_holds_no_threshold_and_no_counter_comparison():
    """#3663: the 1000 and the carrier_changes comparison moved to server/brain/linkverdict.py (K-CABLE)."""
    text = (AGENT / "lib/hw/cablelive.sh").read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for gone in ("CABLE_TEST_MIN_MBPS", "carrier_changes", "carrier0", "-lt 1000", "1000"):
        assert gone not in code, f"cablelive.sh still decides: {gone!r}"
