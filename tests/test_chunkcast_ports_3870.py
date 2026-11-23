"""#3870: the rig's UDP ports (`chunkcast_rig.free_port`) cannot be taken by anything else on the host.

The chunkcast tests failed at random in a loaded parallel run on the testrunner, one of them with
`send: invalid feedback bounds/control port or cannot bind control socket` (r44a bt4, 19/11). `free_port`
bound 127.0.0.1:0, closed the socket and returned the port: a port from the kernel's ephemeral range,
free only at that instant. Every sender's data socket and every receiver's feedback socket is auto-bound
from that same range, and the default control port (P+1) was never checked at all.

Now a port is P of a block P..P+3 below the ephemeral range, whose lock file is held by the process it
was handed to until that process exits.

**Negative control:** `tests/chunkcast_rig.py` back at the base: `free_port` returns ports inside the
ephemeral range (the first test fails on the value) and two processes are handed the same ports (the second).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native(posix=True, why="the port locks are flock(2) and the range is Linux's")
TESTS = Path(__file__).resolve().parent
EACH = 400          # a block each -- of 3,404; with ports from the ephemeral range two such sets almost surely meet
HOLD = f"""
import sys
sys.path.insert(0, {str(TESTS)!r})
import chunkcast_rig as C
print(" ".join(str(C.free_port()) for _ in range({EACH})), flush=True)
sys.stdin.read()            # the ports stay this process's until the test lets it go
"""


def test_a_port_and_the_three_after_it_lie_below_the_range_the_kernel_hands_out():
    low = int(Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()[0])
    port = C.free_port()
    assert 1024 <= port and port + 3 < low, (port, low)


def test_two_processes_are_never_handed_the_same_ports_while_both_live():
    first = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        held = first.stdout.readline().split()
        assert len(held) == EACH, (first.poll(), held[:5])
        second = subprocess.run([sys.executable, "-c", HOLD], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=60)
        assert second.returncode == 0, second.stderr
        taken = {p + k for p in map(int, held) for k in range(4)}
        again = sorted({int(p) for p in second.stdout.split()} & taken)
        assert len(second.stdout.split()) == EACH and not again, again[:10]
    finally:
        first.stdin.close()
        first.wait(10)
