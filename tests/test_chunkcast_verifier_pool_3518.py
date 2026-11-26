"""#3518: ה-verifier של מקלט chunkcast אינו עוד הצוואר -- pool של threads, תור גדול יותר, ולחץ חוזר במקום זריקה.

על הברזל (15/11, i5 650) ה-thread היחיד עבד ~45 חתיכות לשנייה בכל קצב כניסה, וזרק את השאר (`busy`) להשלמה ב-HTTP.
כאן המקלט והשולח הם התהליכים האמיתיים על loopback. **ה-verifier מואט בקובץ interposer חיצוני** (`ZSTD_decompress`
ישן לפני שהוא פורס, כמו ה-pwrite האיטי בטסט המשוב): אין מתג בדיקה בקוד הייצור. האימות עצמו (אורך, XXH64, BLAKE3 לפי
`--b3-file`) רץ כרגיל, ולכן אף טסט כאן אינו מוותר עליו.

**בקרה שלילית:** `agent/chunkcast.c` חוזר לבסיס (`a113608b3`) והטסטים נשארים. המקלט אינו אומר שורת `verifier` (אין
`threads`, `deferred`), אינו מכיר `CHUNKCAST_VERIFIERS`, וחתיכה שהתור המלא פגש נזרקת (`busy`) -- כל טסט כאן נופל על
`verifier` חסר או על `busy > 0`. ראו את גוף ה-PR.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from test_chunk_b3_3332 import VECTORS, b3, b3_args

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

CC_ZMAX = (4 << 20) + (64 << 10)        # chunkcast.c: CC_ZMAX
CC_VQ, CC_VQ_MAX, CC_VER_MAX = 4, 32, 8


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("pool-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """raw, 30 chunks of a short pattern: a frame is a few packets, so the wire never limits -- the write pace does."""
    return C.build(tmp_path_factory.mktemp("pool-img"), cc / "fanout", kind="raw", chunks=30, noisy=(),
                   name="p3.windows.pcl.zst")


@pytest.fixture(autouse=True)
def _free_the_memory_drives():
    yield
    for left in Path("/dev/shm").glob(f"pool3518-{os.getpid()}-*.disk"):
        left.unlink()


SLOW_VERIFY = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <unistd.h>
#include <stddef.h>
size_t ZSTD_decompress(void *dst, size_t cap, const void *src, size_t n) {
    size_t (*real)(void *, size_t, const void *, size_t) = dlsym(RTLD_NEXT, "ZSTD_decompress");
    if (getenv("SLOW_VERIFY_MS")) usleep(1000 * (useconds_t)atoi(getenv("SLOW_VERIFY_MS")));
    return real(dst, cap, src, n);
}
'''


@pytest.fixture(scope="module")
def slow(tmp_path_factory):
    d = tmp_path_factory.mktemp("pool-slow")
    src, so = d / "slow.c", d / "slow.so"
    src.write_text(SLOW_VERIFY)
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(src), "-ldl",
                            "-o", str(so)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return so


def line(run, word):
    """The `word k=v ...` line of the receiver's stdout, as a dict of ints. Not said = a failure of the test."""
    found = [x for x in run.out.read_text().splitlines() if x.startswith(word + " ")]
    assert len(found) == 1, (word, run.log())
    return {k: int(v) for k, v in (pair.split("=", 1) for pair in found[0].split()[1:])}


def one_pass(cc, image, tmp_path, *, slow_so=None, slow_ms=0, threads=None, write_kbps, send_extra=()):
    """One sender, one receiver, one target. Returns (receiver, sender, target, state)."""
    env = dict(C.ENV)
    if slow_so:
        env.update(LD_PRELOAD=str(slow_so), SLOW_VERIFY_MS=str(slow_ms))
    if threads is not None:
        env["CHUNKCAST_VERIFIERS"] = str(threads)
    session, port, state = C.new_session(), C.free_port(), tmp_path / "state"
    # The "drive" is memory when the host has it: these tests are about the verifiers, and a disk of a loaded test host that
    # stalls a write makes the writer's own queue overflow (`skipped <drive>`) -- a different drop, with another counter.
    shm = Path("/dev/shm")
    target = image.blank((shm if shm.is_dir() and os.access(shm, os.W_OK) else tmp_path) / f"pool3518-{os.getpid()}-{session}.disk")
    control = C.free_port()
    while control == port:
        control = C.free_port()
    feedback = ("--control-port", str(control))
    r = C.recv(cc, image, [target], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv", env=env,
               extra=(*b3_args(cc, image, tmp_path), *feedback))
    s = C.send(cc, image, f"127.0.0.1:{port}", session, tag=tmp_path / "send", write_kbps=write_kbps,
               extra=(*feedback, *send_extra))
    try:
        assert s.wait(120) == 0, s.log()
        assert r.wait(120) == 0, r.log()
    finally:
        for run in (r, s):
            if run.process.poll() is None:
                run.process.kill()
                run.process.wait()
    drive_behind = [x for x in r.out.read_text().splitlines() if x.startswith("skipped ") and not x.endswith(" 0")]
    assert not drive_behind, ("the drive was behind, which these tests are not about", drive_behind, r.log())
    return r, s, target, state


def counters(run):
    """The `rxstats` line (`stray=-` without --from is not a number, and is left out)."""
    text = next(x for x in run.out.read_text().splitlines() if x.startswith("rxstats "))
    return {k: int(v) for k, v in (pair.split("=", 1) for pair in text.split()[1:]) if v != "-"}


def expected_default_threads() -> int:
    return min(4, max(1, len(os.sched_getaffinity(0)) // 2))


def memory_fit() -> int:
    for text in Path("/proc/meminfo").read_text().splitlines():
        if text.startswith("MemAvailable:"):
            return int(text.split()[1]) * 1024 // 16 // CC_ZMAX
    raise AssertionError("MemAvailable is not in /proc/meminfo: this host cannot say what the receiver would")


# --- how many, and how long ---------------------------------------------------------------------------------------

def test_the_default_is_half_the_cores_between_one_and_four_and_a_queue_that_grows_with_them(cc, img, tmp_path):
    r, _s, _t, _st = one_pass(cc, img, tmp_path, write_kbps=800_000)
    said = line(r, "verifier")
    n = expected_default_threads()
    assert said["threads"] == n, said
    assert said["queue"] == max(CC_VQ, min(CC_VQ * n + CC_VQ, CC_VQ_MAX, memory_fit())), said
    assert counters(r)["good"] == img.n and counters(r)["busy"] == 0, r.log()
    assert f"{n} verifier" in r.err.read_text(), r.log()


@pytest.mark.parametrize("given,want", [("3", 3), ("1", 1), (str(CC_VER_MAX), CC_VER_MAX), ("99", CC_VER_MAX)])
def test_chunkcast_verifiers_is_taken_and_never_above_the_most(cc, img, tmp_path, given, want):
    r, _s, _t, _st = one_pass(cc, img, tmp_path, threads=given, write_kbps=800_000)
    assert line(r, "verifier")["threads"] == want, r.log()


@pytest.mark.parametrize("bad", ["0", "-2", "abc", ""])
def test_a_value_that_is_not_a_count_is_the_default_not_a_failure(cc, img, tmp_path, bad):
    r, _s, _t, _st = one_pass(cc, img, tmp_path, threads=bad, write_kbps=800_000)
    assert line(r, "verifier")["threads"] == expected_default_threads(), r.log()


# --- a slow verifier, many chunks ---------------------------------------------------------------------------------

#: a chunk every 20 ms (4MiB at 1,677,722 kbit/s); the verifier needs 100 ms a chunk
FAST_ARRIVAL, SLOW_MS = 1_677_722, 100


def test_one_verifier_cannot_keep_up_and_says_so_by_dropping_what_it_counts(cc, img, tmp_path, slow):
    """The control for the next test: the interposer really makes ONE verifier the limit -- ~10 chunks a second
    against 50 -- so chunks are dropped, and none of them vanishes uncounted."""
    r, _s, target, state = one_pass(cc, img, tmp_path, slow_so=slow, slow_ms=SLOW_MS, threads=1,
                                    write_kbps=FAST_ARRIVAL)
    c = counters(r)
    assert c["busy"] > 0, r.log()
    assert c["good"] + c["busy"] + c["evicted"] + c["skipped"] == img.n, c
    durable = C.state_of(state)[str(target)]["durable"]
    assert len(durable) == c["good"], (c, len(durable))
    assert target.read_bytes() == img.expected(durable)


def test_enough_verifiers_take_the_same_stream_without_dropping_a_chunk(cc, img, tmp_path, slow):
    r, _s, target, state = one_pass(cc, img, tmp_path, slow_so=slow, slow_ms=SLOW_MS, threads=CC_VER_MAX,
                                    write_kbps=FAST_ARRIVAL)
    c = counters(r)
    assert line(r, "verifier")["threads"] == CC_VER_MAX
    assert c["busy"] == 0 and c["evicted"] == 0 and c["good"] == img.n, (c, r.log())
    assert C.state_of(state)[str(target)]["durable"] == set(range(img.n))
    assert target.read_bytes() == img.expected()


# --- a full queue ------------------------------------------------------------------------------------------------

#: a chunk every 4 ms (4MiB at 8,388,608 kbit/s): a burst
BURST = 8_388_608


def test_a_whole_chunk_that_finds_the_queue_full_waits_in_its_slot_and_is_not_dropped(cc, tmp_path, slow):
    """One verifier (400 ms a chunk) and a burst of 11: one in the verifier, eight in the queue (CC_VQ * 1 + CC_VQ), and
    two whole chunks with no room -- which before #3518 were dropped (`busy`), and now wait and are counted."""
    burst = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=11, noisy=(), name="p3.windows.pcl.zst")
    r, _s, target, state = one_pass(cc, burst, tmp_path, slow_so=slow, slow_ms=400, threads=1, write_kbps=BURST)
    c, v = counters(r), line(r, "verifier")
    assert v["queue"] == 2 * CC_VQ and v["deferred"] >= 1, (v, r.log())
    assert c["busy"] == 0 and c["evicted"] == 0 and c["good"] == burst.n, (c, r.log())
    assert C.state_of(state)[str(target)]["durable"] == set(range(burst.n))
    assert target.read_bytes() == burst.expected()


def test_past_every_buffer_a_chunk_is_dropped_and_counted_and_the_rest_is_exact(cc, tmp_path, slow):
    burst = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=24, noisy=(), name="p3.windows.pcl.zst")
    r, _s, target, state = one_pass(cc, burst, tmp_path, slow_so=slow, slow_ms=400, threads=1, write_kbps=BURST)
    c = counters(r)
    assert c["busy"] > 0, (c, r.log())
    assert c["good"] + c["busy"] + c["evicted"] + c["skipped"] == burst.n, c
    durable = C.state_of(state)[str(target)]["durable"]
    assert len(durable) == c["good"] and target.read_bytes() == burst.expected(durable)


# --- the sender hears of it --------------------------------------------------------------------------------------

START, MIN, MAX = 800_000, 200_000, 1_600_000


def rate_lines(run):
    return [(int(x.split()[1]), x.split()[2]) for x in run.out.read_text().splitlines() if x.startswith("rate ")]


def test_a_slow_verifier_makes_the_sender_slow_down_before_the_queue_and_the_slots_run_out(cc, img, tmp_path, slow):
    """One verifier (60 ms a chunk) against a chunk every 42 ms at the start: the queue's depth is in the feedback's
    backlog, the sender's rate falls (`decrease`), and not one chunk is dropped for it."""
    r, s, _t, _st = one_pass(cc, img, tmp_path, slow_so=slow, slow_ms=60, threads=1, write_kbps=START,
                             send_extra=("--rate-min", str(MIN), "--rate-max", str(MAX)))
    seen = rate_lines(s)
    assert any(reason == "decrease" for _, reason in seen) and min(rate for rate, _ in seen) < START, s.log()
    assert line(r, "verifier")["threads"] == 1, r.log()
    assert counters(r)["busy"] == 0 and counters(r)["good"] == img.n, r.log()


# --- BLAKE3 on every code path the build can pick ------------------------------------------------------------------

B3 = Path(__file__).resolve().parent.parent / "agent" / "blake3"
PATHS = (
    ("portable", ("-DBLAKE3_NO_SSE41", "-DBLAKE3_NO_SSE2"), ()),
    ("sse2", ("-DBLAKE3_NO_SSE41",), ("blake3_sse2_x86-64_unix.S",)),
    ("sse41", (), ("blake3_sse41_x86-64_unix.S", "blake3_sse2_x86-64_unix.S")),
)


@pytest.mark.parametrize("name,flags,asm", PATHS, ids=[p[0] for p in PATHS])
def test_blake3_is_the_specs_on_each_simd_path_the_dispatcher_can_choose(tmp_path, name, flags, asm):
    """blake3_dispatch.c picks, at run time, the best of the code compiled in (cpuid): SSE4.1, else SSE2, else portable.
    Each is built alone here, so each is what runs; all must give the official vectors (BLAKE3 1.8.1, `i % 251`)."""
    out = tmp_path / "chunkcast"
    cmd = ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", *flags, "-o", str(out),
           str(C.REPO / "agent/chunkcast.c"), str(B3 / "blake3_one.c"), *(str(B3 / f) for f in asm), "-l:libzstd.so.1"]
    done = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stdout + done.stderr
    for length, want in VECTORS:
        assert b3(tmp_path, "b3sum", bytes(i % 251 for i in range(length))).decode() == want + "\n", (name, length)



# --- the same pass, many times, with several verifiers ---------------------------------------------------------------

REPEATS = 50


@pytest.mark.parametrize("threads", [2, 3, 4])
def test_a_damaged_chunk_among_short_ones_loses_no_other_chunk_in_fifty_passes_with_several_verifiers(cc, tmp_path, threads):
    """#3519, 16/11: in a full parallel run `test_a_packet_damaged_on_the_wire_fails_its_chunk_only` ended once with chunk 2 missing
    next to the damaged chunk 1, and the question was whether several verifiers race over what is durable. That test's chunk 2
    is ~2,900 packets of one wire, where a single datagram the kernel drops is enough; here every chunk is a few packets, so a
    missing chunk cannot be the wire's. Fifty passes a thread count (half of them in reversed windows of 5), chunk 1 damaged each
    time: the verifiers must fail that chunk and ONLY it -- every other chunk is durable and exact, or counted
    (`busy`/`evicted`/`skipped`) -- never just missing.

    **Negative control:** `agent/chunkcast.c` at the base (a single verifier thread, no `CHUNKCAST_VERIFIERS`): the receiver says
    no `verifier` line, and this test fails on it before the first pass is judged."""
    image = C.build(tmp_path / "img", cc / "fanout", chunks=12.5, noisy=())
    env = dict(C.ENV, CHUNKCAST_VERIFIERS=str(threads))

    def damage(receiver, kind, chunk, seq, packet):
        if kind == C.DATA and chunk == 1 and seq == 0:
            return [packet[:40] + bytes([packet[40] ^ 0x55]) + packet[41:]]
        return [packet]

    have = set(range(image.n)) - {1}
    shm = Path("/dev/shm")
    home = shm if shm.is_dir() and os.access(shm, os.W_OK) else tmp_path
    for i in range(REPEATS):
        tag = tmp_path / f"p{i}"
        tag.mkdir()
        relay = C.Relay(1, damage, reverse=5 if i % 2 else 0)
        session, state = C.new_session(), tag / "state"
        target = image.blank(home / f"pool3518-{os.getpid()}-{session}.disk")
        # #3870: each pass's disk goes as soon as that pass is judged. Kept to the end of the test, 50 passes x ~61MB per
        # thread count piled up in /dev/shm (RAM, 14GB on tr1); two runs of this file at once filled it, and a pass failed
        # on ENOSPC in `blank()` -- the host, not the verifiers (1120-061801-argus-r44d2-rr-base2-10322, tr1).
        try:
            try:
                r = C.recv(cc, image, [target], relay.listen(0), session, state, tag=tag / "recv", env=env)
                s = C.send(cc, image, relay.addr, session, tag=tag / "send")
                assert s.wait(60) == 0, s.log()
                assert r.wait(60) == 0, r.log()
            finally:
                relay.close()
            assert line(r, "verifier")["threads"] == threads, (i, r.log())
            c = counters(r)
            durable = C.state_of(state)[str(target)]["durable"]
            assert c["bad_chunk"] == 1 and 1 not in durable, (i, c, r.log())
            counted = c["busy"] + c["evicted"] + c["skipped"]
            if counted:                 # a drop the receiver says it made: every chunk is still accounted for
                assert c["good"] + c["bad_chunk"] + counted == image.n, (i, c, r.log())
            else:                       # nothing the receiver says it dropped: nothing may be missing
                assert durable == have, (i, "missing", sorted(have - durable), c, r.log())
                assert target.read_bytes() == image.expected(have), (i, r.log())
        finally:
            target.unlink(missing_ok=True)
