"""#1968 -- ethtool --cable-test when the link is slow or dropped.

The test drops the link for seconds, so hello never runs it on a live link
(interfaces.md, netprobe). Verified 22/10: the first version ran it from hello
whenever carrier_changes > 0 -- which is every link that ever came up (the
count is >= 1 from boot) -- i.e. every 900 s, mid-round too. Now: hello keeps
"skipped" on a live link; cable_live_check runs the test from the cloner's idle
branch only, no round open and no task, once per boot, when the link is slow or
carrier_changes ROSE since the first value read. A NIC that cannot test is
"unchecked", never ok.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401
from test_netprobe import _stub, netprobe_of

pytestmark = requires_native(("bash", BASH))


def _nic(sysroot: Path, speed: str, changes: str) -> None:
    nic = sysroot / "sys/class/net/eth0"
    (nic / "carrier").write_text("1\n")
    (nic / "speed").write_text(speed + "\n")
    (nic / "statistics").mkdir(exist_ok=True)
    (nic / "statistics/carrier_changes").write_text(changes + "\n")


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


def test_idle_cloner_tests_a_slow_link_once_and_hello_serves_it(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "2")
    count = tmp_path / "count"
    ethtool = _stub(tmp_path, "ethtool", f"echo x >> {posix(count)!r}; echo 'netlink error: Operation not supported' >&2; exit 95")
    _live(sysroot, run, ethtool)
    _live(sysroot, run, ethtool)
    assert count.read_text().split() == ["x"], "tested more than once per boot"
    assert json.loads((run / "cable-live.json").read_text()) == {"status": "unchecked", "reason": "unsupported"}
    p = netprobe_of(sysroot, extra=f"export IFACE=eth0 RUN_DIR={posix(run)!r}; ", ethtool=ethtool)
    assert p["cable"] == {"status": "unchecked", "reason": "unsupported"}


def test_a_link_that_dropped_since_boot_is_tested_and_the_pair_reported(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "1000", "2")                    # came up once: 2 is the boot value, not a drop
    count = tmp_path / "count"
    ethtool = _stub(tmp_path, "ethtool", f'echo x >> {posix(count)!r}; echo "Pair C code Open, length 12m"')
    _live(sysroot, run, ethtool)
    assert not count.exists(), "a link that only came up was tested"
    _nic(sysroot, "1000", "4")                    # it went down and up again
    _live(sysroot, run, ethtool)
    assert json.loads((run / "cable-live.json").read_text()) == {
        "status": "open", "pairs": [{"pair": "C", "code": "Open", "length_m": 12}]}
    assert not (run / "netprobe.json.at").exists()


def test_never_with_a_round_open_a_task_or_off_the_cloner(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    _nic(sysroot, "100", "9")
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
