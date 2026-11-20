"""#3535: `CHUNKCAST_PACE=smooth` and `CHUNKCAST_BATCH`, observed through real UDP on loopback.

Metal (16/11) showed the sender emitting bursts: 64 packets per `sendmmsg`, then one sleep, and the disk
limiter sleeping after a whole 4MiB chunk. The two knobs are experiments, so what is pinned here is:

* the default (`legacy`, 64) is today's sender: the same datagrams in the same order, and the same bursts;
* `smooth` sends the SAME datagrams, keeps the average rate, counts the write limit in TIME (decompressed bytes
  against `--write-kbps`, wire bytes against `--kbps`, the slower rules), and cuts the burst in 1 ms;
* an invalid value is refused by name, with nothing sent.

Timing is read at the receiver, from the kernel's own receive stamps (SO_TIMESTAMPNS): the thread that reads
the socket may be late, the stamp is not.
"""
from __future__ import annotations

import re
import socket
import struct
import threading

import pytest

import chunkcast_rig as C
from native import requires_native
from server.rounds import chunklog

pytestmark = requires_native("gcc", "zstd", posix=True, why="real chunkcast UDP and kernel receive stamps")

KNOBS = ("CHUNKCAST_AHEAD", "CHUNKCAST_QCAP", "CHUNKCAST_FB_LOW", "CHUNKCAST_FB_HIGH", "CHUNKCAST_PACE", "CHUNKCAST_BATCH",
         "CHUNKCAST_READER", "CHUNKCAST_READQ")
LEGACY_LINE = "knobs ahead=4 readahead=legacy order=seq fb_low=2 fb_high=6 qcap=8 pace=legacy batch=64 reader=inline readq=8"
SO_TIMESTAMPNS = getattr(socket, "SO_TIMESTAMPNS", 35)      # Linux; the cmsg type is the same number
DRAIN = b"pace-test-drain"
WIRE_HEAD = 32                                              # CC_HDR + CC_DATA_HDR: what a packet adds to its payload
CC_PKT = 1472                                               # a full packet on the wire (UDP payload)
KBPS = 100_000                                              # 12,500 bytes in a ms: 8.5 full packets (the rig's rmem_max is small)
MS = 1_000_000


def env(values=None):
    return {**{k: v for k, v in C.ENV.items() if k not in KNOBS}, **(values or {})}


class Stamped:
    """Every datagram with the time the kernel queued it."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 << 20)
        self.sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.address = self.sock.getsockname()
        self.addr = f"127.0.0.1:{self.address[1]}"
        self.packets = []                   # (ns, datagram)
        self.stop = threading.Event()
        self.drained = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stop.is_set():
            try:
                data, ancillary, _, _ = self.sock.recvmsg(65536, 256)
            except socket.timeout:
                continue
            if data == DRAIN:
                self.drained.set()
                return
            stamp = next((struct.unpack("=qq", body[:16]) for level, kind, body in ancillary
                          if level == socket.SOL_SOCKET and kind == SO_TIMESTAMPNS), None)
            self.packets.append((stamp[0] * 1_000_000_000 + stamp[1] if stamp else None, data))

    def close(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wake:
                wake.sendto(DRAIN, self.address)
            assert self.drained.wait(5), "UDP observer did not drain"
        finally:
            self.stop.set()
            self.thread.join(5)
            self.sock.close()

    def of(self, kind):
        found = [(t, d) for t, d in self.packets if len(d) >= 32 and d[4] == kind]
        assert all(t is not None for t, _ in found), "the kernel gave no receive stamps"
        return found

    def data(self):
        return [d for _, d in self.of(C.DATA)]

    def stamps(self):
        return sorted(t for t, _ in self.of(C.DATA))

    def seconds(self):
        """From the first data packet to the END: the whole schedule, the last sleep included."""
        ends = [t for t, _ in self.of(C.END)]
        assert ends, "no END packet"
        return (max(ends) - self.stamps()[0]) / 1e9


def burst(stamps, window=MS):
    """Most packets inside any one window (1 ms)."""
    best = lo = 0
    for hi, t in enumerate(stamps):
        while t - stamps[lo] >= window:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def crowded(stamps, cap, window=MS):
    """The share of packets after which the next millisecond holds more than `cap` packets.

    The most in one window (`burst`) is what ONE stall of the host sets: the kernel stamps a datagram when it is
    delivered, and a runner that holds the sender (or the softirq) for a few ms hands over several batches at once.
    A sender that sends 64 at a time is crowded after most of its packets; one stall crowds a handful."""
    over = hi = 0
    for lo, t in enumerate(stamps):
        while hi < len(stamps) and stamps[hi] - t < window:
            hi += 1
        over += hi - lo > cap
    return over / len(stamps)


def stop(run):
    if run is not None and run.process.poll() is None:
        run.process.kill()
        run.process.wait()


def sender(cc, img, addr, tag, session, *, kbps, write_kbps, values=None):
    return C._spawn([str(cc / "chunkcast"), "send", "--to", addr, "--session", session, "--part", "3", "--pass", "1",
                     "--index", str(img.idx), "--file", str(img.file), "--kbps", str(kbps),
                     "--write-kbps", str(write_kbps), "--feedback", "off", "--lead-ms", "0", "--tail-ms", "0"],
                    tag, env(values))


class Sent:
    def __init__(self, run, listener):
        self.run, self.listener = run, listener
        lines = run.out.read_text().splitlines()
        self.knobs = [s for s in lines if s.startswith("knobs ")]
        stats = [s for s in lines if s.startswith("sendstats ")]
        paced = [s for s in lines if s.startswith("paced ")]
        assert len(self.knobs) == 1 and len(stats) == 1 and paced, run.log()
        self.stats = dict(f.split("=") for f in stats[0].split()[1:])
        self.paced = tuple(int(x) for x in paced[-1].split()[1:])      # (wire_ms, disk_ms), the sender's own sleeps

    @property
    def over_ms(self):
        return int(self.stats["over_ms"])


def transmit(cc, img, tmp, name, session, *, kbps, write_kbps, values=None):
    listener, run = Stamped(), None
    try:
        run = sender(cc, img, listener.addr, tmp / name, session, kbps=kbps, write_kbps=write_kbps, values=values)
        assert run.wait() == 0, run.log()
    finally:
        stop(run)
        listener.close()
    assert listener.data(), run.log()
    return Sent(run, listener)


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("pace-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    # three noisy chunks (~2,900 packets each) between three chunks of a pattern (one packet each, 4MiB to write)
    return C.build(tmp_path_factory.mktemp("pace-img"), cc / "fanout", kind="raw", chunks=6, noisy=(0, 2, 4))


@pytest.fixture(scope="module")
def wire_limited(cc, img, tmp_path_factory):
    """The wire is the limit (--write-kbps far above it): the default, the spelled-out default, smooth."""
    tmp, session = tmp_path_factory.mktemp("pace-wire"), C.new_session()
    kw = dict(kbps=KBPS, write_kbps=100_000_000)
    return {"default": transmit(cc, img, tmp, "default", session, **kw),
            "legacy": transmit(cc, img, tmp, "legacy", session, values={"CHUNKCAST_PACE": "legacy", "CHUNKCAST_BATCH": "64"}, **kw),
            "smooth": transmit(cc, img, tmp, "smooth", session, values={"CHUNKCAST_PACE": "smooth"}, **kw)}


def test_default_is_legacy_64_and_says_so(wire_limited):
    assert wire_limited["default"].knobs == [LEGACY_LINE]
    assert wire_limited["legacy"].knobs == [LEGACY_LINE]
    assert wire_limited["smooth"].knobs == [LEGACY_LINE.replace("pace=legacy", "pace=smooth")]


def test_default_is_the_spelled_out_default_datagram_for_datagram(wire_limited):
    first, second = wire_limited["default"].listener.data(), wire_limited["legacy"].listener.data()
    assert first == second and first


def test_default_still_sends_the_old_bursts(wire_limited):
    # one 64-packet sendmmsg, then one sleep: most of a batch lands inside a single millisecond
    assert burst(wire_limited["default"].listener.stamps()) >= 40
    assert wire_limited["default"].over_ms >= 50 and wire_limited["legacy"].over_ms >= 50


def test_smooth_sends_the_same_datagrams_in_the_same_order(wire_limited):
    assert wire_limited["smooth"].listener.data() == wire_limited["default"].listener.data()


def test_smooth_cuts_the_burst_in_a_millisecond(wire_limited):
    old, new = burst(wire_limited["default"].listener.stamps()), burst(wire_limited["smooth"].listener.stamps())
    # the ceiling is 12,500 bytes a ms (8.5 packets); up to 1.5x of it is a legitimate catch-up, and a slice rounds
    cap = KBPS * 1000 // 8 // 1000 / CC_PKT
    assert new <= old // 2 and new <= 3 * cap, (old, new, cap)


def test_smooth_counts_few_windows_over_the_ceiling(wire_limited):
    # #3943: `over_ms` is counted by the sender against its own clock, so a stall of a loaded runner shows in it: 20 windows
    # over with `forgiven_ms_wire=0` (a lag under 20 ms is made up at 1.5x the limit, which is windows over by design),
    # against the 13 this allowed (public gate, 3.12 and 3.13). The same count came out 1..14 on an idle host. So the claim
    # is read from the datagrams instead, like the batch knob below: of the packets, not of the windows a stall sets.
    # Twice the ceiling in a ms (17 packets) is more than a catch-up may send; the default's 64-packet batches are there.
    cap = 2 * KBPS * 1000 // 8 // 1000 / CC_PKT
    smooth, default = wire_limited["smooth"], wire_limited["default"]
    assert crowded(smooth.listener.stamps(), cap) <= 0.05, (crowded(smooth.listener.stamps(), cap), smooth.stats)
    assert crowded(default.listener.stamps(), cap) >= 0.25      # the same measure does tell the default's 64


def test_smooth_keeps_the_average_rate(wire_limited, img):
    # #3943: two clocks, one against the other, said nothing a loaded runner could keep: each run of the sender is slowed by
    # its own stalls (0.9 * old <= new <= 1.1 * old failed on the public gate with both of them correct). A stall can only
    # ADD to the time a stream takes and only SHORTEN the sleeps the sender made, so the two sides are read where they hold:
    # never faster than the limit (from the receive stamps), and never more sleeping than the schedule (the sender's own count).
    wire = sum(len(d) for d in wire_limited["smooth"].listener.data())
    for mode in ("smooth", "default"):
        sent = wire_limited[mode]
        assert wire * 8 / sent.listener.seconds() / 1000 <= 1.05 * KBPS, (mode, wire, sent.listener.seconds())
        assert sum(sent.paced) <= 1.12 * 1000 * sum(expected_sleeps(img, KBPS, 100_000_000)), (mode, sent.paced)


@pytest.fixture(scope="module")
def write_limited(cc, img, tmp_path_factory):
    """Wire 100 Mbit/s, writes 200 Mbit/s: a noisy chunk is bound by the wire (336 ms), a chunk of a pattern by
    the write (4MiB in 168 ms, one packet on the wire)."""
    tmp, session = tmp_path_factory.mktemp("pace-write"), C.new_session()
    kw = dict(kbps=KBPS, write_kbps=200_000)
    return {"legacy": transmit(cc, img, tmp, "legacy", session, **kw),
            "smooth": transmit(cc, img, tmp, "smooth", session, values={"CHUNKCAST_PACE": "smooth"}, **kw)}


def chunk_times(img, kbps, write_kbps):
    """Per chunk, in s: its wire bytes at kbps, and its decompressed bytes at write_kbps."""
    for k, zlen in enumerate(img.zlens):
        wire = zlen + WIRE_HEAD * -(-zlen // 1440)
        yield wire * 8 / kbps / 1000, min(C.CH, len(img.data) - k * C.CH) * 8 / write_kbps / 1000


def expected_seconds(img, kbps, write_kbps):
    """Per chunk, the slower of: its wire bytes at kbps, its decompressed bytes at write_kbps."""
    return sum(max(t) for t in chunk_times(img, kbps, write_kbps))


def expected_sleeps(img, kbps, write_kbps):
    """(wire, disk) in s: what the sender may sleep on each axis -- a chunk's slower limit is the one it sleeps for."""
    times = list(chunk_times(img, kbps, write_kbps))
    return sum(w for w, d in times if w >= d), sum(d for w, d in times if d > w)


def test_smooth_counts_the_write_limit_in_decompressed_bytes_and_the_wire_in_wire_bytes(write_limited, img):
    # #3943: 1.5 s is what the limits ask. A stall of a loaded runner only adds to the time a stream takes (1.12 * 1.5 s was
    # overrun on the public gate) and only shortens the sleeps, so each wrong count is read on the side a stall cannot move:
    # wire bytes against the write limit take ~1.0 s -- under what the stamps allow; decompressed bytes against the wire limit
    # make the sender sleep ~2.0 s on the wire axis -- over what it may sleep there (~1.0 s).
    smooth = write_limited["smooth"]
    want = expected_seconds(img, KBPS, 200_000)
    assert smooth.listener.seconds() >= 0.93 * want, (want, smooth.listener.seconds())
    wire_s, disk_s = expected_sleeps(img, KBPS, 200_000)
    assert smooth.paced[0] <= 1.12 * 1000 * wire_s and smooth.paced[1] <= 1.12 * 1000 * disk_s, (wire_s, disk_s, smooth.paced)
    assert smooth.listener.data() == write_limited["legacy"].listener.data()


def test_smooth_keeps_the_time_legacy_takes_when_both_limits_bind(img, write_limited):
    # #3943: two runs, one against the other, are two draws of a loaded runner's stalls. Both are held to the schedule the
    # limits make, which is the time legacy takes: not shorter (stamps) and not more sleeping (the sender's own count).
    want = expected_seconds(img, KBPS, 200_000)
    for mode in ("legacy", "smooth"):
        sent = write_limited[mode]
        assert sent.listener.seconds() >= 0.93 * want, (mode, want, sent.listener.seconds())
        assert sum(sent.paced) <= 1.12 * 1000 * want, (mode, want, sent.paced)


@pytest.fixture(scope="module")
def disk_limited(cc, img, tmp_path_factory):
    """The write is the only limit (--kbps far above the wire): every sleep belongs to --write-kbps."""
    return transmit(cc, img, tmp_path_factory.mktemp("pace-disk"), "smooth", C.new_session(),
                    kbps=100_000_000, write_kbps=200_000, values={"CHUNKCAST_PACE": "smooth"})


def test_smooth_says_which_limit_held_it_back(write_limited, wire_limited, disk_limited):
    # #3943: how long the sender slept is read against its own clock, so a stall of a loaded runner shortens it (581 ms
    # against the 600 this asked for, public gate 3.13; 524, 500, 511 on WSL): no number of ms says which limit held it.
    # What the sender says is WHICH axis a sleep is written to, and that does not depend on the clock: with one limit
    # alone every ms is that one's and the other reads exactly 0, and with both each one has slept.
    assert wire_limited["smooth"].paced[0] > 0 and wire_limited["smooth"].paced[1] == 0, wire_limited["smooth"].paced
    assert disk_limited.paced[0] == 0 and disk_limited.paced[1] > 0, disk_limited.paced
    # three chunks of a pattern wait for their writes, three noisy ones for the wire
    wire_ms, disk_ms = write_limited["smooth"].paced
    assert wire_ms > 0 and disk_ms > 0, write_limited["smooth"].paced


@pytest.mark.parametrize("pace,batch", [("smooth", "2"), ("legacy", "8"), ("smooth", "1")])
def test_a_batch_knob_changes_the_timing_and_not_a_datagram(cc, img, tmp_path, wire_limited, pace, batch):
    sent = transmit(cc, img, tmp_path, "batch", C.new_session(), kbps=KBPS, write_kbps=100_000_000,
                    values={"CHUNKCAST_PACE": pace, "CHUNKCAST_BATCH": batch})
    assert sent.knobs == [f"knobs ahead=4 readahead=legacy order=seq fb_low=2 fb_high=6 qcap=8 pace={pace} batch={batch} reader=inline readq=8"]
    # the session (bytes 8..16) is in every datagram and is another one here: all the rest must be the same
    mine = [d[:8] + d[16:] for d in sent.listener.data()]
    base = [d[:8] + d[16:] for d in wire_limited["default"].listener.data()]
    assert mine == base
    if pace == "legacy":
        # a few batches in a ms, never the 64 of the default -- of the packets, not of the worst window: one stall of a
        # loaded runner bunched 32 packets into a ms (public gate 20/11, `burst` = 32 against 24) and is not a batch of 32.
        cap = 3 * int(batch)
        assert crowded(sent.listener.stamps(), cap) <= 0.05, crowded(sent.listener.stamps(), cap)
        assert crowded(wire_limited["default"].listener.stamps(), cap) >= 0.25     # the same measure does tell the default's 64


INVALID = [("CHUNKCAST_PACE", v) for v in ("", "fast", "Smooth", "legacy ", "1", "smooth,legacy")] + \
          [("CHUNKCAST_BATCH", v) for v in ("0", "65", "x", "-1", "", "04", "4.5")]


@pytest.mark.parametrize("name,value", INVALID)
def test_an_invalid_value_is_refused_by_name_and_nothing_is_sent(cc, img, tmp_path, name, value):
    listener, run = Stamped(), None
    try:
        run = sender(cc, img, listener.addr, tmp_path / "invalid", C.new_session(), kbps=KBPS, write_kbps=200_000,
                     values={name: value})
        assert run.wait() == 2, run.log()
        assert name in run.err.read_text(), run.log()
        assert "nothing is started" in run.err.read_text(), run.log()
    finally:
        stop(run)
        listener.close()
    assert listener.packets == []


def test_the_sendstats_line_ends_with_the_window_counter(wire_limited):
    for sent in wire_limited.values():
        text = sent.run.out.read_text()
        assert re.search(r"^sendstats read_ms=\d+ .* refused=\d+ over_ms=\d+ readq_empty_ms=\d+ readq_full_ms=\d+$", text, re.M), text


# --- what the console's log reader makes of the new fields ---------------------------------------------

NEW_KNOBS = LEGACY_LINE.replace("pace=legacy batch=64", "pace=smooth batch=2")
NEW_STATS = ("sendstats read_ms=12 lag_forgiven_wire=0 lag_forgiven_disk=3 forgiven_ms_wire=0 forgiven_ms_disk=41 "
             "enobufs=0 eagain=1 eintr=0 refused=0 over_ms=7")


@pytest.mark.parametrize("line", [LEGACY_LINE, NEW_KNOBS, NEW_STATS])
def test_the_new_fields_are_progress_and_not_what_the_sender_said(tmp_path, line):
    path = tmp_path / "send.log"
    path.write_text(f"{line}\nsent 3 900\npaced 10 20\nchunkcast: send: the file ended inside chunk 4\n{NEW_STATS}\n"
                    f"paced 12 25\n", encoding="utf-8")
    assert chunklog.log_tail(path) == "chunkcast: send: the file ended inside chunk 4"


def test_a_half_written_new_field_is_still_a_sentence_of_the_sender(tmp_path):
    path = tmp_path / "send.log"
    half = NEW_KNOBS.rsplit(" batch=", 1)[0]
    path.write_text(f"{half}\n", encoding="utf-8")
    assert chunklog.log_tail(path) == half
