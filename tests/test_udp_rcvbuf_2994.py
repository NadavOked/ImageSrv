"""udp-receiver's socket receive buffer: asked for, read back, reported (#2994, #2555).

On 12/11 cloner 2 (HP 8100) dropped 3.94-3.96% of every wave in its own kernel
(RcvbufErrors ~ the sender's re-xmits, #2028), and the learned rate stayed at
600m. udp-receiver (udpcast 20120424, Debian 13) sets no SO_RCVBUF on Linux
without --rcvbuf, so its socket had rmem_default. The agent now asks for
UDPCAST_RCVBUF bytes on every multicast stream, raises rmem_max to the ask once
per boot (never lowers it), and reads rmem_max back before every stream: the
kernel grants min(ask, rmem_max) * 2, and a cut is said in the log and in the
round report (udp_drops.rcvbuf_bytes) -- not assumed (principle 5).

Whether the bigger buffer removes the drops is a measurement on the metal
(#2994), not something these tests can say.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))

ASK = 16777216
STOCK = 212992   # rmem_max / rmem_default of a stock kernel


def _box(tmp_path: Path, rmem_max: int | None) -> Path:
    core = tmp_path / "root/proc/sys/net/core"
    core.mkdir(parents=True)
    if rmem_max is not None:
        (core / "rmem_max").write_text(f"{rmem_max}\n", encoding="utf-8", newline="\n")
    (tmp_path / "run").mkdir()
    return core / "rmem_max"


def _sh(tmp_path: Path, body: str, env: str = "") -> subprocess.CompletedProcess[str]:
    """The receiver path as the agent loads it: common, waits, restore, rcvbuf;
    udp-receiver is a stub that records its argv and writes nothing."""
    stubs = tmp_path / "stubs"
    script = (
        'export PATH="/usr/bin:$PATH"\n'
        f"mkdir -p {posix(stubs)!r}\n"
        f"cat > {posix(stubs)}/udp-receiver <<'STUB'\n#!/bin/sh\n"
        'printf "%s\\n" "$@" >> "$RUN_DIR/udp.argv"; echo --- >> "$RUN_DIR/udp.argv"\nSTUB\n'
        f"chmod 0755 {posix(stubs)}/udp-receiver\n"
        f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"\n'
        f"export RUN_DIR={posix(tmp_path / 'run')!r} SYSROOT={posix(tmp_path / 'root')!r} "
        f"LOG_FILE={posix(tmp_path / 'run/agent.log')!r} IFACE=eth0 {env}\n"
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/restore/restore.sh\n"
        # Sourced when it is there, so that the agent before #2994 fails these tests on
        # what it does (no --rcvbuf, no report), not on a missing file (bakara-shlilit).
        f"[ ! -f {posix(AGENT)}/lib/core/rcvbuf.sh ] || . {posix(AGENT)}/lib/core/rcvbuf.sh\n"
        + body
    )
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


STREAM = 'stream_source multicast http://s img_1 p1.zst 600 > "$RUN_DIR/stream.out"\n'


def _argvs(tmp_path: Path) -> list[list[str]]:
    text = (tmp_path / "run/udp.argv").read_text(encoding="utf-8")
    return [chunk.split() for chunk in text.split("---") if chunk.strip()]


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _log(tmp_path: Path) -> str:
    return (tmp_path / "run/agent.log").read_text(encoding="utf-8")


def _report(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "run/udp_drops.json").read_text(encoding="utf-8"))


def test_the_receiver_asks_for_the_buffer_and_rmem_max_is_raised_to_it(tmp_path):
    rmem = _box(tmp_path, STOCK)
    _sh(tmp_path, STREAM)
    (argv,) = _argvs(tmp_path)
    assert _flag(argv, "--rcvbuf") == str(ASK), argv
    assert rmem.read_text(encoding="utf-8").strip() == str(ASK)
    assert f"udp rcvbuf: asked {ASK}, the socket gets {2 * ASK} bytes (rmem_max {ASK})" in _log(tmp_path)
    assert (tmp_path / "run/stream.out").read_text(encoding="utf-8") == "", "a log line went into the stream"


def test_a_bigger_rmem_max_is_never_lowered(tmp_path):
    rmem = _box(tmp_path, 4 * ASK)
    _sh(tmp_path, STREAM)
    assert rmem.read_text(encoding="utf-8").strip() == str(4 * ASK)
    assert f"the socket gets {2 * ASK} bytes (rmem_max {4 * ASK})" in _log(tmp_path)


def test_a_cut_is_said_and_reported_with_what_the_socket_got(tmp_path):
    """rmem_max that stays below the ask (the write refused, or lowered by hand
    after the boot's raise -- the A/B lever on the metal): the kernel grants
    rmem_max * 2, and the log and the report say so."""
    rmem = _box(tmp_path, 106496)
    (tmp_path / "run/rcvbuf.raised").write_text("", encoding="utf-8")   # this boot already raised it
    _sh(tmp_path, STREAM + "udp_drops_add 37 40\n")
    assert rmem.read_text(encoding="utf-8").strip() == "106496", "a value set after the raise was undone"
    assert f"WARNING: udp rcvbuf: asked {ASK}, cut to rmem_max 106496 -- the socket gets {STOCK} bytes" \
        in _log(tmp_path)
    assert _report(tmp_path) == {"rcvbuf": 37, "inerrors": 40,
                                 "rcvbuf_asked": ASK, "rcvbuf_bytes": STOCK}


def test_rmem_max_not_read_is_null_never_a_size(tmp_path):
    _box(tmp_path, None)
    _sh(tmp_path, STREAM + "udp_drops_add 0 0\n")
    (argv,) = _argvs(tmp_path)
    assert _flag(argv, "--rcvbuf") == str(ASK), "the ask does not depend on the read"
    assert "rmem_max not read -- what the socket got is unknown" in _log(tmp_path)
    assert _report(tmp_path)["rcvbuf_bytes"] is None


def test_the_report_keeps_the_smallest_grant_and_one_unread_stream_makes_it_null(tmp_path):
    rmem = _box(tmp_path, STOCK)
    lower = f"echo 106496 > {posix(rmem)!r}\n"
    _sh(tmp_path, STREAM + "udp_drops_add 1 1\n" + lower + STREAM + "udp_drops_add 2 2\n"
        + f"echo {ASK} > {posix(rmem)!r}\n" + STREAM + "udp_drops_add 3 3\n")
    assert _report(tmp_path) == {"rcvbuf": 6, "inerrors": 6, "rcvbuf_asked": ASK, "rcvbuf_bytes": STOCK}
    _sh(tmp_path, f"rm -f {posix(rmem)!r}\n" + STREAM + "udp_drops_add 0 0\n"
        + f"echo {ASK} > {posix(rmem)!r}\n" + STREAM + "udp_drops_add 0 0\n")
    assert _report(tmp_path)["rcvbuf_bytes"] is None


def test_a_new_restore_starts_its_own_grant(tmp_path):
    _box(tmp_path, None)
    _sh(tmp_path, STREAM + "udp_drops_add 0 0\nudp_drops_reset\n"
        f"echo {ASK} > \"$SYSROOT/proc/sys/net/core/rmem_max\"\n" + STREAM + "udp_drops_add 0 0\n")
    assert _report(tmp_path)["rcvbuf_bytes"] == 2 * ASK


def test_zero_asks_for_nothing_the_agent_before_2994(tmp_path):
    rmem = _box(tmp_path, STOCK)
    _sh(tmp_path, STREAM + "udp_drops_add 5 5\n", env="UDPCAST_RCVBUF=0")
    (argv,) = _argvs(tmp_path)
    assert "--rcvbuf" not in argv, argv
    assert rmem.read_text(encoding="utf-8").strip() == str(STOCK)
    assert "udp rcvbuf: not asked (UDPCAST_RCVBUF=0)" in _log(tmp_path)
    assert _report(tmp_path) == {"rcvbuf": 5, "inerrors": 5}


def test_unicast_asks_nothing(tmp_path):
    rmem = _box(tmp_path, STOCK)
    _sh(tmp_path, 'http_get_stream() { :; }\nstream_source unicast http://s img_1 p1.zst 600 > /dev/null\n')
    assert not (tmp_path / "run/rcvbuf.min").exists()
    assert rmem.read_text(encoding="utf-8").strip() == str(STOCK)


def test_the_server_keeps_the_drops_of_a_report_that_carries_the_buffer():
    """The two new keys ride beside the counters; the server's check of udp_drops
    must not read them as a malformed value ("not read", principle 5)."""
    from server.agent.reports import well_formed_udp_drops
    got = well_formed_udp_drops({"udp_drops": {"rcvbuf": 37, "inerrors": 40,
                                               "rcvbuf_asked": ASK, "rcvbuf_bytes": None}})
    assert got == {"rcvbuf": 37, "inerrors": 40}
