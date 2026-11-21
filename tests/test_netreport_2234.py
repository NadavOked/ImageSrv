"""#2234 -- the network report of the machine card: the layers up to the server.

netreport.sh rides inside the netprobe object (``"report"``). It runs on the real
sh against a fake sysfs/run dir; ``ip``, ``ping``, ``nslookup`` and ``curl`` are
stubs at explicit paths (a host tool is not the initramfs). Every layer is one of
three states (principle 5): measured, ``null`` = not checked, ``{"error"}``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import sizelimit
from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401

pytestmark = requires_native(("bash", BASH))

SERVER = "http://10.44.0.1:8080"


def _stub(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    subprocess.run([BASH, "-c", f"chmod +x {posix(p)!r}"], check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def _report(fm, tmp_path, *, ip: str | None = "echo 'default via 10.44.0.254 dev eth0'",
            ping: str | None = "exit 0", nslookup: str | None = "exit 0",
            curl: str | None = "printf 404; exit 0", server: str = SERVER,
            resolv: str | None = "nameserver 10.44.0.2\n", probe: str | None = "serverid=10.44.0.1\nip=10.44.0.77\n") -> dict:
    sysroot, run = fm["sysroot"], fm["run"]
    env = f"export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(run)!r}; "
    for var, name, body in (("IPCMD", "ip", ip), ("PING", "ping", ping),
                            ("NSLOOKUP", "nslookup", nslookup), ("CURL", "curl", curl)):
        if body is not None:
            env += f"export {var}={posix(_stub(tmp_path, name, body))!r}; "
    (sysroot / "etc").mkdir(exist_ok=True)
    resolv_file = sysroot / "etc/resolv.conf"
    if resolv is not None:
        resolv_file.write_text(resolv)
    if probe is not None:
        (run / "dhcp-probe.eth0").write_text(probe)
    out = sh(env + f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/netprobe.sh; "
             f". {posix(AGENT)}/lib/hw/netreport.sh; netreport_json eth0 {server!r}")
    return json.loads(out)


def test_all_layers_measured(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path)
    assert r["dhcp"] == {"server_id": "10.44.0.1"}
    assert r["gateway"] == {"addr": "10.44.0.254", "reachable": True}
    assert r["dns"] == {"servers": ["10.44.0.2"], "resolves": None}   # the server is an address: no name to look up
    assert r["tcp"] == [{"host": "10.44.0.1", "port": 8080, "ok": True, "curl_rc": 0}]


def test_a_gateway_that_does_not_answer_is_reachable_false_not_missing(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path, ping="exit 1")
    assert r["gateway"] == {"addr": "10.44.0.254", "reachable": False}


def test_no_default_route_is_a_fact_not_a_failure_and_not_ok(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path, ip="exit 0")
    assert r["gateway"] == {"addr": None, "reachable": None}


def test_ip_that_fails_is_an_error_not_no_route(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path, ip="exit 3")
    assert r["gateway"] == {"error": "ip route rc 3"}


def test_a_missing_tool_is_null_never_ok(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path, ip=None, curl=None)
    assert r["gateway"] is None and r["tcp"] is None


def test_curl_000_is_closed_not_open(fake_machine, tmp_path):
    # http=000 counted as "the path is open" is the pattern CLAUDE.md principle 5 names
    r = _report(fake_machine, tmp_path, curl="printf 000; exit 7")
    assert r["tcp"] == [{"host": "10.44.0.1", "port": 8080, "ok": False, "curl_rc": 7}]
    r = _report(fake_machine, tmp_path, curl="printf 000; exit 0")
    assert r["tcp"][0]["ok"] is False


def test_a_named_server_is_looked_up_and_a_failed_lookup_is_false(fake_machine, tmp_path):
    named = "http://imagesrv.college.local:8080"
    assert _report(fake_machine, tmp_path, server=named)["dns"]["resolves"] is True
    assert _report(fake_machine, tmp_path, server=named, nslookup="exit 1")["dns"]["resolves"] is False


def test_no_resolv_conf_and_no_dhcp_record_are_null(fake_machine, tmp_path):
    r = _report(fake_machine, tmp_path, resolv=None, probe=None)
    assert r["dns"] is None and r["dhcp"] is None


def test_a_server_url_that_is_not_http_is_not_checked(fake_machine, tmp_path):
    assert _report(fake_machine, tmp_path, server="ftp://x")["tcp"] is None
    assert _report(fake_machine, tmp_path, server="")["tcp"] is None


def test_netprobe_carries_the_report_and_null_without_the_loader(fake_machine, tmp_path):
    sysroot, run = fake_machine["sysroot"], fake_machine["run"]
    (sysroot / "sys/class/net/eth0/carrier").write_text("1\n")
    base = (f"export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(run)!r} IFACE=eth0 SERVER={SERVER!r}; "
            f"export LLDPSNIFF=none-here ETHTOOL=none-here; . {posix(AGENT)}/lib/core/common.sh; "
            f". {posix(AGENT)}/lib/hw/netprobe.sh; ")
    bare = json.loads(sh(base + 'printf "{\\"x\\":0%s}" "$(_netprobe_fresh)"'))["netprobe"]
    assert bare["report"] is None
    full = json.loads(sh(base + f'. {posix(AGENT)}/lib/hw/netreport.sh; export IPCMD=none-here PING=none-here '
                         f'NSLOOKUP=none-here CURL=none-here; printf "{{\\"x\\":0%s}}" "$(_netprobe_fresh)"'))["netprobe"]
    assert set(full["report"]) == {"dhcp", "gateway", "dns", "tcp"}


def test_the_agent_loads_netreport_and_stays_small():
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/netreport.sh"' in text
    assert sizelimit.assert_within_limit(AGENT / "lib" / "hw" / "netreport.sh") <= 100
    assert sizelimit.count_lines(AGENT / "lib" / "hw" / "netprobe.sh") <= 120
    assert sizelimit.count_lines(AGENT / "imagesrv-agent") <= 300
