"""#2527 -- rescuecat: the source reader of a copy from a dying disk.

The tool is compiled (-Werror) and run for real against a regular file whose reads a
preloaded shim makes fail like a bad disk does: EIO at chosen byte ranges (for the first N
reads of a unit, or for good), or ENODEV from an offset on (the disk is gone). The shim also
strips O_DIRECT (a tmpfs file refuses it) after writing the flags it saw to the log, so the
test can say that the source was opened read-only and direct.

What is pinned (Nadav 15/11: a dying disk is a part of v1; CLAUDE.md principle 5):
* an unread sector is zeros in the stream AND a range in the map -- by address, never only a count;
* the first pass asks a bad unit once: a dying disk is not hammered while the rest is read;
* the retry asks the unread units again, round after round (never one unit many times in a row,
  odd rounds forward and even backward), and writes only the ones that read -- into every target;
* a disk that stopped answering ends the copy short: no "complete" in the map, a non-zero exit;
* a map without "# complete" is no evidence (lcraw.sh lc_rescue_count).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "rescuecat.c"

pytestmark = requires_native("gcc", posix=True, why="rescuecat צריך gcc ו-pread על קובץ")

SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

/* RC_FAIL="from:len:okafter;..." bytes -- a read touching the range fails with EIO until its
 * okafter-th earlier attempt at the same offset (0 = never reads). RC_DEAD_FROM=off -- ENODEV from there on.
 * RC_LOG=file -- every open's flags and every pread's offset and length. */
static void note(const char *fmt, ...)
{
    const char *p = getenv("RC_LOG"); FILE *f; va_list ap;
    if (!p || !(f = fopen(p, "a"))) return;
    va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap); fclose(f);
}

static int open_any(const char *name, const char *path, int flags, mode_t m)
{
    int (*real)(const char *, int, ...) = dlsym(RTLD_NEXT, name);
    note("open %s acc=%d direct=%d\n", path, flags & O_ACCMODE, !!(flags & O_DIRECT));
    return real(path, flags & ~O_DIRECT, m);
}
int open(const char *path, int flags, ...)
{
    mode_t m = 0; va_list ap;
    if (flags & O_CREAT) { va_start(ap, flags); m = va_arg(ap, mode_t); va_end(ap); }
    return open_any("open", path, flags, m);
}
int open64(const char *path, int flags, ...)
{
    mode_t m = 0; va_list ap;
    if (flags & O_CREAT) { va_start(ap, flags); m = va_arg(ap, mode_t); va_end(ap); }
    return open_any("open64", path, flags, m);
}

static struct { long long off; int n; } seen[8192]; static int nseen;

ssize_t pread(int fd, void *buf, size_t n, off_t off)
{
    ssize_t (*real)(int, void *, size_t, off_t) = dlsym(RTLD_NEXT, "pread");
    const char *dead = getenv("RC_DEAD_FROM"), *spec = getenv("RC_FAIL");
    int attempt = 0;
    note("pread %lld %zu\n", (long long)off, n);
    if (dead && (long long)off + (long long)n > atoll(dead)) { errno = ENODEV; return -1; }
    if (n <= 4096) {
        for (int i = 0; i < nseen; i++) if (seen[i].off == off) attempt = ++seen[i].n;
        if (!attempt && nseen < 8192) { seen[nseen].off = off; seen[nseen].n = 1; nseen++; attempt = 1; }
    }
    for (const char *s = spec; s && *s; ) {
        long long a, l; int k, used = 0;
        if (sscanf(s, "%lld:%lld:%d%n", &a, &l, &k, &used) < 3) break;
        if ((long long)off < a + l && (long long)off + (long long)n > a && (k == 0 || n > 4096 || attempt <= k)) { errno = EIO; return -1; }
        s += used; if (*s == ';') s++;
    }
    return real(fd, buf, n, off);
}
ssize_t pread64(int fd, void *buf, size_t n, off_t off) { return pread(fd, buf, n, off); }
'''

MiB = 1 << 20
UNIT = 4096


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    d = tmp_path_factory.mktemp("rescuecat")
    subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(d / "rescuecat"), str(SOURCE)],
                   check=True, stdin=subprocess.DEVNULL)
    (d / "shim.c").write_text(SHIM, newline="\n")
    subprocess.run(["gcc", "-shared", "-fPIC", "-O1", "-Wall", "-o", str(d / "shim.so"), str(d / "shim.c"), "-ldl"],
                   check=True, stdin=subprocess.DEVNULL)
    return d


def source(tmp_path: Path, size: int = 3 * MiB) -> tuple[Path, bytes]:
    data = bytes((i * 7 + (i >> 9)) % 251 + 1 for i in range(size))   # no zero byte: a zero is a lost sector
    p = tmp_path / "disk.img"
    p.write_bytes(data)
    return p, data


def rescue(tools: Path, tmp_path: Path, mode: str, *args: str, fail: str = "", dead: str = "", stdout=subprocess.PIPE):
    env = {**os.environ, "LD_PRELOAD": str(tools / "shim.so"), "RC_FAIL": fail, "RC_LOG": str(tmp_path / "shim.log")}
    if dead:
        env["RC_DEAD_FROM"] = dead
    return subprocess.run([str(tools / "rescuecat"), mode, *args], env=env, stdout=stdout, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, timeout=60)


def ranges(m: Path) -> list[tuple[int, int, int]]:
    return [tuple(int(x) for x in l.split()) for l in m.read_text().splitlines() if l and not l.startswith("#")]


def reads_at(tmp_path: Path, off: int, n: int = UNIT) -> int:
    return sum(1 for l in (tmp_path / "shim.log").read_text().splitlines() if l == f"pread {off} {n}")


def test_a_clean_disk_is_streamed_whole_and_the_map_says_complete(tools, tmp_path):
    src, data = source(tmp_path)
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"))
    assert r.returncode == 0, r.stderr
    assert r.stdout == data
    assert ranges(tmp_path / "map") == [] and "# complete\n" in (tmp_path / "map").read_text()
    assert b"unread_sectors=0 ranges=0" in r.stderr


def test_the_source_is_opened_read_only_and_direct(tools, tmp_path):
    src, _ = source(tmp_path)
    rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"))
    assert f"open {src} acc=0 direct=1\n" in (tmp_path / "shim.log").read_text()   # O_RDONLY, and #2158's O_DIRECT


def test_an_unread_sector_is_zeros_in_the_stream_and_a_range_in_the_map(tools, tmp_path):
    src, data = source(tmp_path)
    bad = MiB + 2 * UNIT                                   # inside the second block: only its unit is lost
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"),
               fail=f"{bad + 100}:200:0")                  # 200 bytes of the unit fail: the whole unit is the loss
    assert r.returncode == 0, r.stderr
    assert len(r.stdout) == len(data)
    assert r.stdout == data[:bad] + bytes(UNIT) + data[bad + UNIT:]
    assert ranges(tmp_path / "map") == [(bad, UNIT, 5)]    # by address, with errno EIO
    assert b"unread_sectors=8 ranges=1" in r.stderr


def test_neighbouring_bad_units_and_a_block_boundary_are_one_range(tools, tmp_path):
    src, data = source(tmp_path)
    start = MiB - UNIT                                     # the last unit of block 0 and the first of block 1
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"), fail=f"{start}:{2 * UNIT}:0")
    assert r.returncode == 0, r.stderr
    assert ranges(tmp_path / "map") == [(start, 2 * UNIT, 5)]
    assert r.stdout[:start] == data[:start] and r.stdout[start + 2 * UNIT:] == data[start + 2 * UNIT:]


def test_the_first_pass_asks_a_bad_unit_once(tools, tmp_path):
    """Negative control of the retry's reason: a dying disk is not asked again in the first pass --
    the second chance comes after everything readable is in."""
    src, _ = source(tmp_path)
    bad = MiB + 2 * UNIT
    rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"), fail=f"{bad}:{UNIT}:0")
    assert reads_at(tmp_path, bad) == 1


def test_a_disk_that_stopped_answering_is_a_short_stream_not_zeros_to_the_end(tools, tmp_path):
    src, data = source(tmp_path)
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"), dead=str(MiB))
    assert r.returncode == 4, r.stderr
    assert len(r.stdout) < len(data) and b"stopped answering" in r.stderr
    text = (tmp_path / "map").read_text()
    assert "# complete" not in text and "# aborted" in text   # no evidence of a finished read


def test_too_many_unread_bytes_in_a_row_is_the_same(tools, tmp_path):
    src, _ = source(tmp_path)
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"), "-L", str(8 * UNIT),
               fail=f"{MiB}:{2 * MiB}:0")
    assert r.returncode == 4, r.stderr
    assert "# complete" not in (tmp_path / "map").read_text()


def test_a_consumer_that_went_away_ends_the_read_with_no_map(tools, tmp_path):
    src, _ = source(tmp_path)
    env = {**os.environ, "LD_PRELOAD": str(tools / "shim.so")}
    p = subprocess.Popen([str(tools / "rescuecat"), "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    p.stdout.read(10)
    p.stdout.close()
    assert p.wait(timeout=30) == 2
    assert not (tmp_path / "map").exists()


def test_a_size_that_is_not_whole_4k_is_read_by_sector(tools, tmp_path):
    src, data = source(tmp_path, 3 * MiB + 512)
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(tmp_path / "map"))
    assert r.returncode == 0 and r.stdout == data, r.stderr


# --- the retry --------------------------------------------------------------------------

def damaged(tools: Path, tmp_path: Path, fail: str, n_targets: int = 2):
    """A first pass over a source whose units fail as `fail` says; the targets hold its stream."""
    src, data = source(tmp_path)
    m = tmp_path / "map"
    r = rescue(tools, tmp_path, "read", "-s", str(src), "-b", "4096", "-m", str(m), fail=fail)
    assert r.returncode == 0, r.stderr
    targets = []
    for i in range(n_targets):
        t = tmp_path / f"target{i}"
        t.write_bytes(r.stdout)
        targets.append(t)
    return src, data, m, targets


def retry(tools, tmp_path, src, m, targets, fail, *extra):
    args = ["-s", str(src), "-b", "4096", "-m", str(m), *extra]
    for t in targets:
        args += ["-t", str(t)]
    return rescue(tools, tmp_path, "retry", *args, fail=fail)


def test_the_retry_writes_what_reads_into_every_target_and_leaves_the_rest_unread(tools, tmp_path):
    a, b = MiB + 2 * UNIT, 2 * MiB + 4 * UNIT
    fail = f"{a}:{UNIT}:1;{b}:{UNIT}:0"            # a reads from the retry's second round; b never does
    src, data, m, targets = damaged(tools, tmp_path, fail)
    assert ranges(m) == [(a, UNIT, 5), (b, UNIT, 5)]
    r = retry(tools, tmp_path, src, m, targets, fail, "-n", "3")
    assert r.returncode == 0 and r.stdout == b"", r.stderr
    assert ranges(m) == [(b, UNIT, 5)] and "# complete\n" in m.read_text()
    for t in targets:
        got = t.read_bytes()
        assert got[a:a + UNIT] == data[a:a + UNIT]                 # recovered: the real bytes
        assert got[b:b + UNIT] == bytes(UNIT)                      # still unread: still zeros, never claimed
        assert got[:a] == data[:a] and got[b + UNIT:] == data[b + UNIT:]   # nothing else touched
    assert b"unread_sectors=8 ranges=1" in r.stderr


def test_the_retry_goes_round_after_round_never_one_unit_many_times_in_a_row(tools, tmp_path):
    a, b = MiB + 2 * UNIT, 2 * MiB + 4 * UNIT
    fail = f"{a}:{UNIT}:0;{b}:{UNIT}:0"
    src, _, m, targets = damaged(tools, tmp_path, fail, 1)
    (tmp_path / "shim.log").unlink()
    r = retry(tools, tmp_path, src, m, targets, fail, "-n", "3")
    assert r.returncode == 0, r.stderr
    asked = [int(l.split()[1]) for l in (tmp_path / "shim.log").read_text().splitlines() if l.startswith("pread")]
    assert asked == [a, b, b, a, a, b], asked                      # forward, backward, forward
    assert ranges(m) == [(a, UNIT, 5), (b, UNIT, 5)]


def test_a_unit_that_reads_only_in_the_last_round_is_recovered(tools, tmp_path):
    a = MiB + 2 * UNIT
    fail = f"{a}:{UNIT}:2"   # asked in its own process: rounds 1 and 2 fail, round 3 reads
    src, data, m, targets = damaged(tools, tmp_path, fail, 1)
    r = retry(tools, tmp_path, src, m, targets, fail, "-n", "3")
    assert r.returncode == 0 and ranges(m) == [], r.stderr
    assert targets[0].read_bytes() == data


def test_a_target_that_stopped_taking_writes_is_named_and_the_others_still_get_them(tools, tmp_path):
    a = MiB + 2 * UNIT
    fail = f"{a}:{UNIT}:1"
    src, data, m, targets = damaged(tools, tmp_path, fail, 1)
    r = retry(tools, tmp_path, src, m, [Path("/dev/full"), *targets], fail, "-n", "2")
    assert r.returncode == 0, r.stderr
    assert r.stdout.decode().split() == ["failed", "/dev/full", "28"], r.stdout   # ENOSPC
    assert targets[0].read_bytes()[a:a + UNIT] == data[a:a + UNIT]


def test_the_retry_says_the_round_it_is_in_for_the_screen(tools, tmp_path):
    """-P: "X N" written whole at the start of each round (the status bar's "סבב X מתוך N"); the
    last one is the last round started. Without -P nothing is written."""
    a = MiB + 2 * UNIT
    fail = f"{a}:{UNIT}:0"
    src, _, m, targets = damaged(tools, tmp_path, fail, 1)
    prog = tmp_path / "round"
    r = retry(tools, tmp_path, src, m, targets, fail, "-n", "3", "-P", str(prog))
    assert r.returncode == 0, r.stderr
    assert prog.read_text() == "3 3\n" and not (tmp_path / "round.tmp").exists()
    prog.unlink()
    retry(tools, tmp_path, src, m, targets, fail, "-n", "2")
    assert not prog.exists()


def test_the_retry_time_budget_leaves_the_map_whole_and_says_so(tools, tmp_path):
    a = MiB + 2 * UNIT
    fail = f"{a}:{UNIT}:1"
    src, data, m, targets = damaged(tools, tmp_path, fail, 1)
    r = retry(tools, tmp_path, src, m, targets, fail, "-n", "3", "-T", "-1")
    assert r.returncode == 0 and b"time budget ended" in r.stderr, r.stderr
    assert ranges(m) == [(a, UNIT, 5)] and "# complete\n" in m.read_text()   # nothing tried, nothing claimed
    assert targets[0].read_bytes()[a:a + UNIT] == bytes(UNIT)


def test_a_disk_that_vanished_during_the_retry_leaves_the_map_as_it_was(tools, tmp_path):
    a = MiB + 2 * UNIT
    fail = f"{a}:{UNIT}:0"
    src, _, m, targets = damaged(tools, tmp_path, fail, 1)
    before = m.read_text()
    env = {**os.environ, "LD_PRELOAD": str(tools / "shim.so"), "RC_DEAD_FROM": "0"}
    r = subprocess.run([str(tools / "rescuecat"), "retry", "-s", str(src), "-b", "4096", "-m", str(m), "-n", "2",
                        "-t", str(targets[0])], env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 4 and m.read_text() == before, r.stderr


def test_usage_errors_are_exit_1(tools, tmp_path):
    for args in (["read"], ["read", "-s", "x", "-m", "y", "-b", "1000"], ["retry", "-s", "x", "-m", "y", "-b", "512"], ["bogus"]):
        r = subprocess.run([str(tools / "rescuecat"), *args], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        assert r.returncode == 1, (args, r.stderr)
