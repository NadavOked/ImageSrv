"""#3034: השולח אינו מאבד את הזמן שבו הקריאה מהמקור עיכבה אותו (`agent/chunkcast.c`, `cc_pace`).

הפייסטוס מדד על ברזל (15/11): המעבר הראשון של `chunkcast send` קצר ב-10–14% מהקצב שנקבע. כל חתיכה נקראת ב-`pread`
סינכרוני, ו-`cc_pace` מחק כל פיגור של יותר מ-20ms, ולכן הזמן שהקריאה לקחה לא הושלם. עכשיו הפיגור **שנגרם מקריאה**
נשמר ומושלם בצעדים הבאים, ולא מהר מ-1.5 פעמים הקצב (`CC_CATCHUP_PCT`). פיגור אחר (רשת, המגבלה השנייה) עדיין
נמחק. ובנוסף, החתיכות הבאות מתבקשות מהקובץ מראש (`posix_fadvise(WILLNEED)`, `CC_AHEAD`).

**המקור האיטי מדומה מבחוץ:** ספריית `LD_PRELOAD` קטנה שעוטפת את `pread`, וכל קריאה שלישית של חתיכה ישנה 300ms.
אותו שולח, אותו קובץ, פעם בלי העטיפה ופעם איתה. נבדק שהמעבר עם הקריאות האיטיות כמעט אינו ארוך יותר (ממוצע הקצב
חוזר לקצב שנקבע), שהוא אינו קצר יותר (הממוצע אינו עובר את הקצב), ושבשום חלון של 300ms הזרם אינו עובר את התקרה.

**בקרה שלילית:** `agent/chunkcast.c` חוזר לבסיס (הענף של V4). הפיגור נמחק, והמעבר עם הקריאות האיטיות ארוך בכ-0.6
שניות -- הטסט נופל על ההפרש.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast needs gcc and libzstd; the slow source is LD_PRELOAD")

KBPS = 80_000                   # 10MB/s on the wire: a 4MiB chunk that does not compress is ~0.42 s
SLOW_MS, EVERY = 300, 3         # every third chunk's read takes 0.3 s more

SLOW_PREAD = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>
static int calls;
static void slow(size_t n)
{
    if (n < (1u << 20)) return;                       /* only a chunk's read, not an index or a state */
    if (++calls % atoi(getenv("SLOW_EVERY")) == 0) usleep((useconds_t)atoi(getenv("SLOW_MS")) * 1000);
}
ssize_t pread(int fd, void *b, size_t n, off_t o)
{
    static ssize_t (*real)(int, void *, size_t, off_t);
    if (!real) real = (ssize_t (*)(int, void *, size_t, off_t))dlsym(RTLD_NEXT, "pread");
    slow(n);
    return real(fd, b, n, o);
}
ssize_t pread64(int fd, void *b, size_t n, off_t o)
{
    static ssize_t (*real)(int, void *, size_t, off_t);
    if (!real) real = (ssize_t (*)(int, void *, size_t, off_t))dlsym(RTLD_NEXT, "pread64");
    slow(n);
    return real(fd, b, n, o);
}
"""


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("readlag-bin"))


@pytest.fixture(scope="module")
def slow(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("readlag-so")
    (root / "slow.c").write_text(SLOW_PREAD, encoding="ascii")
    lib = root / "slow.so"
    built = subprocess.run(["gcc", "-O2", "-shared", "-fPIC", "-o", str(lib), str(root / "slow.c"), "-ldl"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return lib


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """8 chunks that do not compress: a frame of ~4MiB each, 3.4 s on the wire at KBPS."""
    return C.build(tmp_path_factory.mktemp("readlag-img"), cc / "fanout", kind="raw", noisy=tuple(range(8)), chunks=8)


class Listener:
    """The stream as a receiver sees it: when each DATA packet came, and its size."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 << 20)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.addr = f"127.0.0.1:{self.sock.getsockname()[1]}"
        self.seen: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                packet = self.sock.recv(2048)
            except socket.timeout:
                continue
            if packet[4] == C.DATA:
                self.seen.append((time.monotonic(), len(packet)))

    def close(self):
        self._stop.set()
        self._thread.join(5)
        self.sock.close()

    def peak_bps(self, window: float = 0.3) -> float:
        """The most bytes a second that came in any `window` of the stream."""
        best, j, total = 0.0, 0, 0
        for t, n in self.seen:
            total += n
            while self.seen[j][0] < t - window:
                total -= self.seen[j][1]
                j += 1
            best = max(best, total / window)
        return best


def _pass(cc, img, tmp_path, name: str, env: dict | None = None) -> tuple[float, Listener]:
    """One pass of the whole partition, timed from the first DATA packet to the last."""
    listener = Listener()
    try:
        run = C._spawn([str(cc / "chunkcast"), "send", "--to", listener.addr, "--session", C.new_session(),
                        "--part", "3", "--pass", "1", "--index", str(img.idx), "--file", str(img.file),
                        "--kbps", str(KBPS), "--write-kbps", "100000000", "--lead-ms", "0", "--tail-ms", "0"],
                       tmp_path / name, {**C.ENV, **(env or {})})
        assert run.wait(60) == 0, run.log()
        deadline = time.monotonic() + 2
        while len(listener.seen) and time.monotonic() < deadline and listener.seen[-1][0] > time.monotonic() - 0.3:
            time.sleep(0.05)                                  # the last packets of the pass are in
    finally:
        listener.close()
    assert listener.seen, run.log()
    return listener.seen[-1][0] - listener.seen[0][0], listener


def test_the_time_a_slow_read_took_is_made_up_and_the_rate_stays_within_its_ceiling(cc, slow, img, tmp_path):
    fast, _ = _pass(cc, img, tmp_path, "fast")
    held, listener = _pass(cc, img, tmp_path, "slow",
                           {"LD_PRELOAD": str(slow), "SLOW_MS": str(SLOW_MS), "SLOW_EVERY": str(EVERY)})
    stalls = img.n // EVERY * SLOW_MS / 1000                  # 0.6 s of reads that did not come
    # made up: the pass with the slow reads is about as long as the one without them -- not `stalls` longer
    assert held - fast < stalls / 3, (fast, held, stalls)
    # and not shorter: on average the stream never goes faster than the rate it was given
    assert held > fast - 0.15, (fast, held)
    # the ceiling while it makes up: 1.5 times the rate, and some room for a loaded host's timing
    rate = KBPS * 1000 / 8
    assert listener.peak_bps() < rate * 1.5 * 1.2, (listener.peak_bps(), rate)


def test_a_lag_that_is_not_a_reads_is_still_not_made_up(cc, img, tmp_path):
    """The other side of the rule: with no slow read, a pass is its rate -- no burst after the start."""
    fast, listener = _pass(cc, img, tmp_path, "plain")
    rate = KBPS * 1000 / 8
    assert listener.peak_bps() < rate * 1.2, (listener.peak_bps(), rate)
    assert fast > sum(img.zlens) / rate * 0.9, fast
