"""‏#1048 — כבל (ethtool) ו-LLDP ב-hello: נמדד · null · error; מטמון + pending.

רץ על ה-sh האמיתי מול sysfs מזויף. ‏ethtool ו-lldpsniff מזויפים בנתיב
מפורש (`ETHTOOL=`, `LLDPSNIFF=`): כלי המארח אינו "ארוז ב-initramfs".
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import sizelimit
from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401

pytestmark = requires_native(("bash", BASH))

REPO = Path(__file__).resolve().parent.parent


def _chmod_x(path: Path) -> None:
    subprocess.run(
        [BASH, "-c", f"chmod +x {posix(path)!r}"],
        check=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _stub(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    _chmod_x(p)
    return p


def netprobe_of(sysroot: Path, extra: str = "", ethtool: Path | None = None,
                lldp: Path | None = None, fresh: bool = True) -> dict:
    env = f"export SYSROOT={posix(sysroot)!r}; "
    if ethtool is not None:
        env += f"export ETHTOOL={posix(ethtool)!r}; "
    if lldp is not None:
        env += f"export LLDPSNIFF={posix(lldp)!r}; "
    fn = "_netprobe_fresh" if fresh else "netprobe_json"
    out = sh(
        env + extra
        + f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/netprobe.sh; "
        + r'printf "{\"x\":0%s}" "$(' + fn + ')"'
    )
    return json.loads(out)["netprobe"]


def test_netprobe_sh_stays_under_120_and_agent_stays_300():
    n = sizelimit.assert_within_limit(AGENT / "lib" / "hw" / "netprobe.sh")
    assert n <= 120, n
    assert sizelimit.count_lines(AGENT / "imagesrv-agent") <= 300
    assert sizelimit.count_lines(AGENT / "lib" / "hw" / "probe.sh") == 280


def test_the_agent_loads_netprobe_sh():
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/netprobe.sh"' in text


def test_carrier_up_skips_cable_test(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    called = tmp_path / "ethtool.called"
    ethtool = _stub(tmp_path, "ethtool", f"echo ran > {posix(called)!r}; exit 0")
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", ethtool=ethtool)
    assert p["cable"] == {"skipped": "link up"}
    assert not called.exists(), "ethtool --cable-test must not run on a live link"


def test_carrier_down_open_pair(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("0\n")
    ethtool = _stub(tmp_path, "ethtool", 'echo "Pair A code Open, length 12m"')
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", ethtool=ethtool)
    assert p["cable"]["status"] == "open"
    assert p["cable"]["pairs"] == [{"pair": "A", "code": "Open", "length_m": 12}]


def test_ethtool_failure_is_error_not_ok(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("0\n")
    ethtool = _stub(tmp_path, "ethtool", "echo nope >&2; exit 1")
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", ethtool=ethtool)
    assert p["cable"]["error"]
    assert "rc 1" in p["cable"]["error"]


def test_ethtool_absent_is_null(fake_machine):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("0\n")
    p = netprobe_of(sysroot, extra="export IFACE=eth0 ETHTOOL=ethtool-not-here; ")
    assert p["cable"] is None


@pytest.mark.parametrize("rc, body, check", [
    (0, '{"switch":"sw-lab-1","chassis":"aa:bb:cc:dd:ee:ff","port":"Gi1/0/12","port_desc":"lab","ttl":120}',
     lambda v: v["switch"] == "sw-lab-1" and v["port"] == "Gi1/0/12"),
    (2, '{"unheard":true,"waited_s":35}', lambda v: v.get("unheard") is True and v["waited_s"] == 35),
    (3, '{"error":"no such interface"}', lambda v: v.get("error") == "no such interface"),
], ids=["heard", "unheard", "listen-error"])
def test_lldpsniff_three_states(fake_machine, tmp_path, rc, body, check):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    sniffer = _stub(tmp_path, "lldpsniff", f"printf '%s\\n' '{body}'; exit {rc}")
    p = netprobe_of(sysroot, extra="export IFACE=eth0; ", lldp=sniffer)
    assert check(p["lldp"]), p["lldp"]


def test_lldpsniff_absent_is_null(fake_machine):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    p = netprobe_of(sysroot, extra="export IFACE=eth0 LLDPSNIFF=lldpsniff-not-here; ")
    assert p["lldp"] is None


def test_first_hello_with_run_dir_is_pending_then_cache(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    run = fake_machine["run"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("0\n")
    ethtool = _stub(tmp_path, "ethtool", 'echo "Pair A code Open, length 12m"')
    sniffer = _stub(tmp_path, "lldpsniff",
                    'printf \'{"switch":"sw","chassis":null,"port":"Gi1/0/1","port_desc":null,"ttl":120}\\n\'; exit 0')
    extra = (f"export IFACE=eth0 RUN_DIR={posix(run)!r} NETPROBE_TTL=900; ")
    first = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
    assert first == {"pending": True}
    deadline = time.monotonic() + 5
    cache = run / "netprobe.json"
    while time.monotonic() < deadline and not cache.exists():
        time.sleep(0.05)
    assert cache.exists(), "background netprobe did not write the cache"
    second = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
    assert second["cable"]["status"] == "open"
    assert second["lldp"]["switch"] == "sw"
    # TTL: mutating the stub must not be visible until the cache expires.
    ethtool.write_text("#!/bin/sh\necho 'Pair B code Short, length 1m'\n", encoding="utf-8", newline="\n")
    _chmod_x(ethtool)
    third = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
    assert third["cable"]["status"] == "open"
    (run / "netprobe.json.at").write_text("0")
    # expired: the next call serves stale and starts a background refresh.
    stale = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
    assert stale["cable"]["status"] == "open"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        later = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
        if later.get("cable", {}).get("status") == "short":
            break
        time.sleep(0.05)
    else:
        later = netprobe_of(sysroot, extra=extra, ethtool=ethtool, lldp=sniffer, fresh=False)
    assert later["cable"]["status"] == "short"
    assert later["cable"]["pairs"][0]["pair"] == "B"


def test_without_run_dir_answers_immediately(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    sniffer = _stub(tmp_path, "lldpsniff", 'printf \'{"unheard":true,"waited_s":35}\\n\'; exit 2')
    p = netprobe_of(sysroot, extra="export IFACE=eth0 RUN_DIR=/nonexistent/dir; ",
                    lldp=sniffer, fresh=False)
    assert p["cable"]["skipped"] == "link up"
    assert p["lldp"]["unheard"] is True


def test_hello_schema_2_carries_netprobe(fake_machine, tmp_path):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    hello = json.loads(sh(
        f'export SYSROOT={posix(sysroot)!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR=/nonexistent/dir IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'. {posix(AGENT)}/lib/hw/netprobe.sh; build_hello'
    ))
    assert hello["schema"] == 2
    assert "netprobe" in hello
    assert hello["netprobe"]["cable"]["skipped"] == "link up"
