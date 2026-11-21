"""#3535: `CHUNKCAST_READER=thread` -- the sender's source reads on a thread of their own, observed through real UDP,
a real receiver and the libc calls the sender makes.

The sender's `pread` of each chunk sat inside the send loop (metal 16/11: 88 s of a 393 s pass). Thread mode reads the
chunks in the order the pass sends them into a ring of `CHUNKCAST_READQ` slots, and the loop takes whole chunks from it.
It is an experiment behind a switch, so what is pinned here is:

* the default is today's sender (`inline`): the same datagrams, and the same output but for the three words the knobs
  line and the counters now carry;
* `thread` sends the SAME datagrams in the SAME order -- `seq`, `--order mixed`, a repair list, smooth pacing -- and a
  receiver writes the whole partition from it;
* the reads are the ones the pass sends, in the order it sends them, and there is no `posix_fadvise` with it;
* a slow source costs the loop `readq_empty_ms`, not the whole of the read time `inline` waits for;
* a source that fails (an error, or a file that ends inside a chunk) fails the pass by name -- after the whole chunks
  before it and with no packet of its own -- exactly as `inline` does;
* the thread is created once and joined once, on the end of a pass, on an error and on a signal;
* an invalid value, or a combination the thread cannot serve, is refused by name with nothing sent.

Thread libc calls are observed by an LD_PRELOAD shim, as in #3522 and #3523.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time

import pytest

import chunkcast_rig as C
from native import requires_native
from server.rounds import chunklog

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP, a reader thread and LD_PRELOAD observations")

KNOBS = ("CHUNKCAST_AHEAD", "CHUNKCAST_QCAP", "CHUNKCAST_FB_LOW", "CHUNKCAST_FB_HIGH", "CHUNKCAST_PACE",
         "CHUNKCAST_BATCH", "CHUNKCAST_READER", "CHUNKCAST_READQ")
HEAD = "knobs ahead=4 readahead={ra} order={order} fb_low=2 fb_high=6 qcap=8 pace={pace} batch={batch} reader={reader} readq={readq}"
SESSION = "5eed0123456789ab"            # the same in every run, so that the datagrams can be compared whole
PAYLOAD = 1440


def line(**kw):
    return HEAD.format(**{"ra": "legacy", "order": "seq", "pace": "legacy", "batch": 64, "reader": "inline", "readq": 8, **kw})


def env(values=None):
    return {**{k: v for k, v in C.ENV.items() if k not in KNOBS}, **(values or {})}


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("reader-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("reader-img"), cc / "fanout", kind="raw", chunks=10, noisy=(0, 1, 2, 3, 4))


@pytest.fixture(scope="module")
def noisy(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("reader-noisy"), cc / "fanout", kind="raw", chunks=8, noisy=tuple(range(8)))


SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static void note(const char *name, const char *fmt, long long a)
{
    const char *path = getenv(name);
    if (!path) return;
    FILE *f = fopen(path, "a");
    if (!f) _exit(90);
    fprintf(f, fmt, a);
    if (fclose(f)) _exit(91);
}
static off_t short_at = -1;
static ssize_t fake(int fd, void *b, size_t n, off_t o, ssize_t (*real)(int, void *, size_t, off_t))
{
    const char *fail = getenv("READER_FAIL_OFF"), *eof = getenv("READER_EOF_OFF"), *slow = getenv("READER_SLOW_US");
    note("READER_READS", "%lld\n", (long long)o);
    if (slow && n >= (1u << 20)) usleep((useconds_t)atoll(slow));
    if (fail && o == (off_t)atoll(fail)) { errno = EIO; return -1; }
    if (eof && o == (off_t)atoll(eof)) {          /* half of the frame, and then the file ends */
        ssize_t r = real(fd, b, n / 2, o);
        short_at = o + (off_t)(n / 2);
        return r;
    }
    if (short_at >= 0 && o == short_at) return 0;
    return real(fd, b, n, o);
}
ssize_t pread(int fd, void *b, size_t n, off_t o)
{
    return fake(fd, b, n, o, dlsym(RTLD_NEXT, "pread"));
}
ssize_t pread64(int fd, void *b, size_t n, off64_t o)
{
    return fake(fd, b, n, (off_t)o, dlsym(RTLD_NEXT, "pread64"));
}
static void advice(long long off, int how)
{
    if (how == POSIX_FADV_WILLNEED) note("READER_ADVICE", "%lld\n", off);
}
int posix_fadvise(int fd, off_t off, off_t len, int how)
{
    int (*real)(int, off_t, off_t, int) = dlsym(RTLD_NEXT, "posix_fadvise");
    advice((long long)off, how);
    return real(fd, off, len, how);
}
int posix_fadvise64(int fd, off64_t off, off64_t len, int how)
{
    int (*real)(int, off64_t, off64_t, int) = dlsym(RTLD_NEXT, "posix_fadvise64");
    advice((long long)off, how);
    return real(fd, off, len, how);
}
int pthread_create(pthread_t *t, const pthread_attr_t *a, void *(*fn)(void *), void *arg)
{
    int (*real)(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *) = dlsym(RTLD_NEXT, "pthread_create");
    int rc = real(t, a, fn, arg);
    note("READER_THREADS", "create %lld\n", rc);
    return rc;
}
int pthread_join(pthread_t t, void **ret)
{
    int (*real)(pthread_t, void **) = dlsym(RTLD_NEXT, "pthread_join");
    int rc = real(t, ret);
    note("READER_THREADS", "join %lld\n", rc);
    return rc;
}
'''


@pytest.fixture(scope="module")
def shim(tmp_path_factory):
    root = tmp_path_factory.mktemp("reader-shim")
    src, so = root / "shim.c", root / "shim.so"
    src.write_text(SHIM, encoding="ascii")
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(src), "-ldl", "-o", str(so)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return so


class Listener:
    """Every datagram, in arrival order; a local marker drains the queue."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 << 20)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.address = self.sock.getsockname()
        self.addr = f"127.0.0.1:{self.address[1]}"
        self.packets = []
        self.stop = threading.Event()
        self.drained = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stop.is_set():
            try:
                packet = self.sock.recv(65536)
            except socket.timeout:
                continue
            if packet == b"reader-test-drain":
                self.drained.set()
                return
            self.packets.append(packet)

    def close(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
                wake.sendto(b"reader-test-drain", self.address)
            assert self.drained.wait(5), "UDP observer did not drain"
        finally:
            self.stop.set()
            self.thread.join(5)
            self.sock.close()

    def data(self):
        return [p for p in self.packets if len(p) >= 32 and p[4] == C.DATA]

    def chunks(self):
        return list(dict.fromkeys(int.from_bytes(p[16:24], "big") for p in self.data()))

    def seqs(self):
        found = {}
        for p in self.data():
            found.setdefault(int.from_bytes(p[16:24], "big"), []).append(int.from_bytes(p[28:32], "big"))
        return found


def stop(run):
    if run is not None and run.process.poll() is None:
        run.process.kill()
        run.process.wait()


def sender(cc, img, addr, tag, *, values=None, extra=(), stdin=False):
    return C._spawn([str(cc / "chunkcast"), "send", "--to", addr, "--session", SESSION, "--part", "3", "--pass", "1",
                     "--index", str(img.idx), *(["--stdin"] if stdin else ["--file", str(img.file)]),
                     "--kbps", "80000", "--write-kbps", "100000000", "--feedback", "off",
                     "--lead-ms", "0", "--tail-ms", "0", *extra], tag, env(values))


def capture(cc, img, tag, *, values=None, extra=(), rc=0):
    listener, run = Listener(), None
    try:
        run = sender(cc, img, listener.addr, tag, values=values, extra=extra)
        assert run.wait() == rc, run.log()
    finally:
        stop(run)
        listener.close()
    return run, listener


def knobs(run):
    found = [s for s in run.out.read_text().splitlines() if s.startswith("knobs ")]
    assert len(found) == 1, run.log()
    return found[0]


def stats(run):
    found = [s for s in run.out.read_text().splitlines() if s.startswith("sendstats ")]
    assert len(found) == 1, run.log()
    return {k: int(v) for k, v in (f.split("=") for f in found[0].split()[1:])}


def whole(img, listener):
    """Every chunk that left the sender left with all of its packets, once, in order."""
    for k, seqs in listener.seqs().items():
        assert seqs == list(range(-(-img.zlens[k] // PAYLOAD))), (k, seqs[:5], len(seqs))


def mixed(img, indices=None):
    by_size = sorted(range(img.n) if indices is None else indices, key=lambda k: (img.zlens[k], k))
    half = len(by_size) // 2
    light, heavy = sorted(by_size[:half]), sorted(by_size[half:])
    return [k for j, h in enumerate(heavy) for k in ([h, light[j]] if j < len(light) else [h])]


def lines_of(path):
    return path.read_text().splitlines() if path.exists() else []


# --- the default is today's sender ------------------------------------------------------------------------------

def test_the_default_says_inline_with_zero_counters_and_is_the_spelled_out_default(cc, img, tmp_path):
    first, a = capture(cc, img, tmp_path / "default")
    second, b = capture(cc, img, tmp_path / "spelled", values={"CHUNKCAST_READER": "inline", "CHUNKCAST_READQ": "8"})
    assert knobs(first) == line() and knobs(second) == line()
    assert stats(first)["readq_empty_ms"] == 0 and stats(first)["readq_full_ms"] == 0
    assert a.data() == b.data() and a.chunks() == list(range(img.n))


def test_readq_does_nothing_in_inline_mode_but_is_said(cc, img, tmp_path):
    run, listener = capture(cc, img, tmp_path / "q", values={"CHUNKCAST_READQ": "16"})
    assert knobs(run) == line(readq=16)
    assert listener.chunks() == list(range(img.n))


# --- thread mode sends the same datagrams ------------------------------------------------------------------------

def test_thread_sends_the_same_datagrams_as_inline_in_file_order(cc, img, tmp_path):
    _, base = capture(cc, img, tmp_path / "inline")
    run, mine = capture(cc, img, tmp_path / "thread", values={"CHUNKCAST_READER": "thread"})
    assert knobs(run) == line(reader="thread")
    assert mine.data() == base.data() and mine.chunks() == list(range(img.n))
    whole(img, mine)
    assert run.out.read_text().splitlines()[-1].startswith(f"done {img.n} ")


@pytest.mark.parametrize("readq", ["2", "3", "64"])
def test_thread_is_the_same_whatever_the_queue_length(cc, img, tmp_path, readq):
    _, base = capture(cc, img, tmp_path / "inline")
    run, mine = capture(cc, img, tmp_path / "thread", values={"CHUNKCAST_READER": "thread", "CHUNKCAST_READQ": readq})
    assert knobs(run) == line(reader="thread", readq=readq)
    assert mine.data() == base.data()


def test_thread_sends_the_same_datagrams_as_inline_in_mixed_order(cc, img, tmp_path):
    _, base = capture(cc, img, tmp_path / "inline", extra=("--order", "mixed"))
    run, mine = capture(cc, img, tmp_path / "thread", values={"CHUNKCAST_READER": "thread"}, extra=("--order", "mixed"))
    assert knobs(run) == line(ra="on", order="mixed", reader="thread")
    assert mine.chunks() == mixed(img) != list(range(img.n))
    assert mine.data() == base.data()
    whole(img, mine)


def test_thread_works_with_smooth_pacing_and_a_small_batch(cc, img, tmp_path):
    values = {"CHUNKCAST_PACE": "smooth", "CHUNKCAST_BATCH": "4"}
    _, base = capture(cc, img, tmp_path / "inline", values=values)
    run, mine = capture(cc, img, tmp_path / "thread", values={**values, "CHUNKCAST_READER": "thread"})
    assert knobs(run) == line(pace="smooth", batch=4, reader="thread")
    assert mine.data() == base.data()


@pytest.mark.parametrize("order", ["seq", "mixed"])
def test_thread_reads_only_what_the_repair_pass_sends_in_the_order_it_sends(cc, noisy, shim, tmp_path, order):
    pick = tmp_path / "chunks"
    pick.write_text("2-3,5-5\n", encoding="ascii")
    reads = tmp_path / "reads"
    extra = ("--chunks-file", str(pick), *(("--order", "mixed") if order == "mixed" else ()))
    values = {"LD_PRELOAD": str(shim), "READER_READS": str(reads)}
    _, base = capture(cc, noisy, tmp_path / "inline", values=values, extra=extra)
    inline_reads = lines_of(reads)
    reads.unlink()
    _, mine = capture(cc, noisy, tmp_path / "thread", values={**values, "CHUNKCAST_READER": "thread"}, extra=extra)
    want = [2, 3, 5] if order == "seq" else mixed(noisy, [2, 3, 5])
    assert mine.chunks() == want
    assert lines_of(reads) == [str(noisy.offsets[k]) for k in want] == inline_reads
    assert mine.data() == base.data()


def clean(cc, img, tmp_path, order):
    disks = [img.blank(tmp_path / f"disk-{i}") for i in range(2)]
    addr, state = f"127.0.0.1:{C.free_port()}", tmp_path / "state"
    r = C.recv(cc, img, disks, addr, SESSION, state, tag=tmp_path / "recv", env=env(), extra=("--feedback", "off"))
    s = None
    try:
        s = sender(cc, img, addr, tmp_path / "send", values={"CHUNKCAST_READER": "thread"},
                   extra=("--lead-ms", "200", "--tail-ms", "500", *(("--order", order) if order == "mixed" else ())))
        assert s.wait() == 0, s.log()
        assert r.wait() == 0, r.log()
        assert "pass complete" in r.out.read_text(), r.log()
        for disk in disks:
            assert disk.read_bytes() == img.expected()
            assert C.state_of(state)[str(disk)] == {"alive": True, "durable": set(range(img.n)), "reason": ""}
    finally:
        stop(s)
        stop(r)


@pytest.mark.parametrize("order", ["seq", "mixed"])
def test_a_receiver_writes_the_whole_partition_from_a_thread_pass(cc, img, tmp_path, order):
    clean(cc, img, tmp_path, order)


# --- what the reader does not do --------------------------------------------------------------------------------

def test_thread_gives_the_kernel_no_read_ahead_advice_and_inline_still_does(cc, noisy, shim, tmp_path):
    advice = tmp_path / "advice"
    values = {"LD_PRELOAD": str(shim), "READER_ADVICE": str(advice)}
    capture(cc, noisy, tmp_path / "inline", values=values)
    assert len(lines_of(advice)) >= 4           # the legacy loop asks for the chunks ahead (#3034)
    advice.unlink()
    run, _ = capture(cc, noisy, tmp_path / "thread", values={**values, "CHUNKCAST_READER": "thread"})
    assert not advice.exists() or lines_of(advice) == [], lines_of(advice)
    assert "reader=thread" in knobs(run)


# --- the point of it: the loop does not wait for the disk -------------------------------------------------------

def test_a_slow_source_holds_the_loop_for_readq_empty_ms_and_not_for_every_read(cc, noisy, shim, tmp_path):
    slow = {"LD_PRELOAD": str(shim), "READER_SLOW_US": "100000"}        # every chunk's read takes 100 ms more
    inline, _ = capture(cc, noisy, tmp_path / "inline", values=slow)
    thread, listener = capture(cc, noisy, tmp_path / "thread",
                               values={**slow, "CHUNKCAST_READER": "thread", "CHUNKCAST_READQ": "2"})
    base, mine = stats(inline), stats(thread)
    assert base["read_ms"] >= noisy.n * 80, base                         # inline: the loop waits for all of them
    assert mine["read_ms"] >= noisy.n * 80, mine                         # the thread pays them all, on its own
    # the wire takes ~420 ms a chunk, a read 100 ms: after the first chunk the next one is always ready
    assert mine["readq_empty_ms"] <= base["read_ms"] // 2, (base, mine)
    assert mine["readq_full_ms"] >= 200, mine                            # with 2 slots the thread is the one that waits
    assert base["readq_empty_ms"] == base["readq_full_ms"] == 0
    assert listener.chunks() == list(range(noisy.n))


# --- a source that fails ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("how", ["EIO", "EOF"])
@pytest.mark.parametrize("reader", ["inline", "thread"])
def test_a_source_that_fails_fails_the_pass_by_name_after_the_whole_chunks_before_it(cc, img, shim, tmp_path, how, reader):
    values = {"LD_PRELOAD": str(shim), ("READER_FAIL_OFF" if how == "EIO" else "READER_EOF_OFF"): str(img.offsets[3])}
    if reader == "thread":
        values["CHUNKCAST_READER"] = "thread"
    run, listener = capture(cc, img, tmp_path / "fail", values=values, rc=1)
    assert f"{img.file} ended inside chunk 3" in run.err.read_text(), run.log()
    assert listener.chunks() == [0, 1, 2], listener.chunks()           # not a packet of chunk 3, and nothing after it
    whole(img, listener)
    text = run.out.read_text()
    assert "done " not in text and "sendstats " not in text, text


def test_a_thread_failure_in_a_repair_pass_names_the_chunk_in_the_list(cc, img, shim, tmp_path):
    pick = tmp_path / "chunks"
    pick.write_text("1-2,4-4\n", encoding="ascii")
    values = {"LD_PRELOAD": str(shim), "READER_FAIL_OFF": str(img.offsets[4]), "CHUNKCAST_READER": "thread"}
    run, listener = capture(cc, img, tmp_path / "fail", values=values, extra=("--chunks-file", str(pick)), rc=1)
    assert "ended inside chunk 4" in run.err.read_text(), run.log()
    assert listener.chunks() == [1, 2]


# --- the thread ends, always ------------------------------------------------------------------------------------

def threads(path):
    return sorted(lines_of(path))


def test_the_thread_is_created_once_and_joined_once_at_the_end_of_a_pass(cc, img, shim, tmp_path):
    log = tmp_path / "threads"
    capture(cc, img, tmp_path / "inline", values={"LD_PRELOAD": str(shim), "READER_THREADS": str(log)})
    assert threads(log) == []                                           # inline: no thread at all
    capture(cc, img, tmp_path / "thread", values={"LD_PRELOAD": str(shim), "READER_THREADS": str(log),
                                                   "CHUNKCAST_READER": "thread"})
    assert threads(log) == ["create 0", "join 0"]


def test_the_thread_is_joined_when_the_source_fails(cc, img, shim, tmp_path):
    log = tmp_path / "threads"
    values = {"LD_PRELOAD": str(shim), "READER_THREADS": str(log), "READER_FAIL_OFF": str(img.offsets[3]),
              "CHUNKCAST_READER": "thread"}
    capture(cc, img, tmp_path / "fail", values=values, rc=1)
    assert threads(log) == ["create 0", "join 0"]


def test_a_signal_ends_a_pass_whose_thread_is_waiting_for_a_slot_and_the_thread_is_joined(cc, noisy, shim, tmp_path):
    log = tmp_path / "threads"
    listener, run = Listener(), None
    try:
        run = sender(cc, noisy, listener.addr, tmp_path / "term",
                     values={"LD_PRELOAD": str(shim), "READER_THREADS": str(log), "CHUNKCAST_READER": "thread",
                             "CHUNKCAST_READQ": "2"})
        end = time.monotonic() + 20
        while len(listener.packets) < 2000 and time.monotonic() < end:         # inside the first chunk: the queue is full
            assert run.process.poll() is None, run.log()
            threading.Event().wait(0.02)
        assert len(listener.packets) >= 2000, run.log()
        os.kill(run.process.pid, signal.SIGTERM)
        assert run.wait(20) == 6, run.log()
        assert "stopped by a signal" in run.err.read_text(), run.log()
    finally:
        stop(run)
        listener.close()
    assert len(listener.chunks()) < noisy.n
    assert threads(log) == ["create 0", "join 0"]


# --- refused by name, with nothing sent -------------------------------------------------------------------------

INVALID = [("CHUNKCAST_READER", v) for v in ("", "Thread", "threads", "1", "inline ", "thread,inline", "async")] + \
          [("CHUNKCAST_READQ", v) for v in ("0", "1", "65", "x", "-1", "", "08", "4.5")]


@pytest.mark.parametrize("name,value", INVALID)
def test_an_invalid_value_is_refused_by_name_and_nothing_is_sent(cc, img, tmp_path, name, value):
    listener, run = Listener(), None
    try:
        run = sender(cc, img, listener.addr, tmp_path / "invalid", values={name: value})
        assert run.wait() == 2, run.log()
        assert name in run.err.read_text() and "nothing is started" in run.err.read_text(), run.log()
    finally:
        stop(run)
        listener.close()
    assert listener.packets == []


@pytest.mark.parametrize("what", ["ahead", "stdin"])
def test_thread_is_refused_with_what_it_cannot_serve(cc, img, tmp_path, what):
    values = {"CHUNKCAST_READER": "thread", **({"CHUNKCAST_AHEAD": "3"} if what == "ahead" else {})}
    listener, run = Listener(), None
    try:
        run = sender(cc, img, listener.addr, tmp_path / what, values=values, stdin=what == "stdin")
        assert run.wait() == 2, run.log()
        assert "CHUNKCAST_READER=thread" in run.err.read_text() and "nothing is started" in run.err.read_text(), run.log()
    finally:
        stop(run)
        listener.close()
    assert listener.packets == []


# --- what the console's log reader makes of the new fields -------------------------------------------------------

THREAD_KNOBS = line(reader="thread", readq=4).replace("batch=64", "batch=2")
THREAD_STATS = ("sendstats read_ms=912 lag_forgiven_wire=0 lag_forgiven_disk=3 forgiven_ms_wire=0 forgiven_ms_disk=41 "
                "enobufs=0 eagain=1 eintr=0 refused=0 over_ms=7 readq_empty_ms=18 readq_full_ms=2300")


@pytest.mark.parametrize("said", [line(), THREAD_KNOBS, THREAD_STATS])
def test_the_new_fields_are_progress_and_not_what_the_sender_said(tmp_path, said):
    path = tmp_path / "send.log"
    path.write_text(f"{said}\nsent 3 900\npaced 10 20\nchunkcast: send: the file ended inside chunk 4\n{THREAD_STATS}\n"
                    f"paced 12 25\n", encoding="utf-8")
    assert chunklog.log_tail(path) == "chunkcast: send: the file ended inside chunk 4"


def test_a_half_written_reader_field_is_still_a_sentence_of_the_sender(tmp_path):
    path = tmp_path / "send.log"
    half = THREAD_KNOBS.rsplit(" readq=", 1)[0]
    path.write_text(f"{half}\n", encoding="utf-8")
    assert chunklog.log_tail(path) == half
