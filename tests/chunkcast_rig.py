"""A format-2 partition file and its index, built here (#2533) -- for the tests of
the chunk engine (`agent/chunkcast.c`, `server/rounds/repair.py`).

The layout is handoffs/format-spec-2533.md: the partition file is a
concatenation of independent zstd frames -- the head (partclone header + bit map
+ its CRC) and then one frame a 4MiB chunk of the data stream -- and the index
holds each chunk's offset, length and XXH64. The builder is independent of the
code under test: the frames come from the `zstd` CLI, one call a chunk, and what
must be on the disk is laid out block by block in Python.

Also here: the helpers that run `chunkcast` and a relay that sits between a
sender and its receivers and loses, repeats, damages or reorders packets by
their identity (chunk, packet number), so the same run always misses the same
chunks.
"""

from __future__ import annotations

import os
import random
import secrets
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pcl_image as P

REPO = Path(__file__).resolve().parent.parent
CH = 4 << 20
GARBAGE = 0xEE                      # what a target holds before it is written
PKT_HEAD = struct.Struct(">IBBHQ")  # magic, type, protocol version, partition, session
VERSION = 1
MAGIC = 0x49534331
ANNOUNCE, DATA, END = 1, 2, 3
#: #3332: what chunkcast is built with beside chunkcast.c -- the builder's own list (tools/build_initramfs.sh)
BLAKE3_SOURCES = ("blake3_one.c", "blake3_sse41_x86-64_unix.S", "blake3_sse2_x86-64_unix.S")


def build_binaries(root: Path) -> Path:
    """fanout (for XXH64, as the format defines it) and chunkcast, built as the
    initramfs builder builds them."""
    for cmd in (
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(root / "fanout"), str(REPO / "agent/fanout.c")],
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(root / "chunkcast"),
         str(REPO / "agent/chunkcast.c"), *(str(REPO / "agent/blake3" / f) for f in BLAKE3_SOURCES), "-l:libzstd.so.1"],
    ):
        p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert p.returncode == 0, p.stdout + p.stderr
    return root


def xxh64(fanout: Path, data: bytes) -> str:
    p = subprocess.run([str(fanout), "--xxh64"], input=data, capture_output=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.decode().strip()


def _frame(tmp: Path, data: bytes, level: int) -> bytes:
    src = tmp / "frame.in"
    src.write_bytes(data)
    p = subprocess.run(["zstd", "-q", "--check", f"-{level}", "-c", str(src)], capture_output=True,
                       stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    return p.stdout


def chunk_bytes(k: int, size: int = CH, *, noisy: bool, salt: int = 0) -> bytes:
    """Chunk k's content. noisy: incompressible (a frame of ~2,900 packets);
    else a short pattern that says which chunk it is (a frame of a few packets)."""
    if noisy:
        return random.Random(f"{salt}:{k}").randbytes(size)
    seed = f"chunk {k} salt {salt} ".encode() * 4
    return (seed * (size // len(seed) + 1))[:size]


@dataclass
class Image:
    kind: str
    file: Path
    idx: Path
    index_xxh: str
    head_z: Path | None
    data: bytes
    offsets: list[int]
    zlens: list[int]
    total: int = 0          # partclone: blocks on the device
    bs: int = 0
    used: tuple[int, ...] = ()

    @property
    def n(self) -> int:
        return len(self.offsets)

    @property
    def device_bytes(self) -> int:
        return self.total * self.bs if self.kind == "partclone" else len(self.data)

    def frame(self, k: int) -> bytes:
        with self.file.open("rb") as f:
            f.seek(self.offsets[k])
            return f.read(self.zlens[k])

    def expected(self, have: set[int] | None = None) -> bytes:
        """The target after the chunks in `have` (all, when None) were written:
        those bytes in place, the garbage it started with everywhere else."""
        out = bytearray([GARBAGE]) * self.device_bytes
        for k in range(self.n) if have is None else sorted(have):
            piece = self.data[k * CH:(k + 1) * CH]
            if self.kind == "raw":
                out[k * CH:k * CH + len(piece)] = piece
                continue
            pos = 0
            while pos < len(piece):             # a run to the end of the block, at the block's own place
                o = k * CH + pos
                run = min(self.bs - o % self.bs, len(piece) - pos)
                at = self.used[o // self.bs] * self.bs + o % self.bs
                out[at:at + run] = piece[pos:pos + run]
                pos += run
        return bytes(out)

    def blank(self, path: Path, short: int = 0) -> Path:
        path.write_bytes(bytes([GARBAGE]) * (self.device_bytes - short))
        return path


def build(tmp: Path, fanout: Path, *, kind: str = "partclone", noisy: tuple[int, ...] = (0,),
          chunks: float = 4.5, bs: int = 4096, salt: int = 0, level: int = 3, name: str = "p3.windows.pcl.zst") -> Image:
    tmp.mkdir(parents=True, exist_ok=True)
    size = int(chunks * CH) // bs * bs
    n = -(-size // CH)
    data = b"".join(chunk_bytes(k, min(CH, size - k * CH), noisy=k in noisy, salt=salt) for k in range(n))
    assert len(data) == size
    head, total, used = b"", 0, ()
    if kind == "partclone":
        blocks = size // bs
        used, i = [], 0
        while len(used) < blocks:              # free blocks scattered among the used ones
            if i % 7 != 3:
                used.append(i)
            i += 1
        total = i + 5
        bm = P.bitmap(total, used)
        head = P.header(total, blocks, bs) + bm + struct.pack("<I", P.crc(bm))
    frames = [_frame(tmp, data[k * CH:(k + 1) * CH], level) for k in range(n)]
    head_frame = _frame(tmp, head, level) if head else b""
    file = tmp / name
    file.write_bytes(head_frame + b"".join(frames))
    lines = [f"IMAGESRV-IDX 2 {kind} {CH} {len(head)} {len(head_frame)} "
             f"{xxh64(fanout, head) if head else '-'} {size} {n} {file.stat().st_size}"]
    offsets, at = [], len(head_frame)
    for k, frame in enumerate(frames):
        offsets.append(at)
        lines.append(f"{at} {len(frame)} {xxh64(fanout, data[k * CH:(k + 1) * CH])}")
        at += len(frame)
    idx = tmp / (name + ".idx")
    idx.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    head_z = None
    if head:
        head_z = tmp / (name + ".head")
        head_z.write_bytes(head_frame)
    return Image(kind, file, idx, xxh64(fanout, idx.read_bytes()), head_z, data, offsets,
                 [len(f) for f in frames], total, bs, tuple(used))


# --- running chunkcast ---------------------------------------------------------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def new_session() -> str:
    return secrets.token_hex(8)


ENV = {**os.environ, "CHUNKCAST_ALLOW_FILE": "1"}   # a file may stand for a disk in a test only


@dataclass
class Run:
    process: subprocess.Popen
    out: Path
    err: Path

    def wait(self, seconds: float = 60) -> int:
        try:
            return self.process.wait(seconds)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise AssertionError(f"still running after {seconds}s\n{self.log()}") from None

    def log(self) -> str:
        return self.out.read_text(errors="replace") + self.err.read_text(errors="replace")

    def wait_for(self, word: str, seconds: float = 20) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if word in self.out.read_text(errors="replace"):
                return
            assert self.process.poll() is None, f"exited {self.process.returncode} before '{word}'\n{self.log()}"
            time.sleep(0.05)
        raise AssertionError(f"'{word}' did not appear in {seconds}s\n{self.log()}")


def _spawn(cmd: list[str], tag: Path, env=None, stdin=subprocess.DEVNULL) -> Run:
    out, err = tag.with_suffix(".out"), tag.with_suffix(".err")
    with out.open("wb") as o, err.open("wb") as e:
        return Run(subprocess.Popen(cmd, stdout=o, stderr=e, stdin=stdin, env=env or ENV), out, err)


def image_args(img: Image, index_xxh: str | None = None) -> list[str]:
    return ["--index", str(img.idx), "--index-xxh64", index_xxh or img.index_xxh,
            *(["--head", str(img.head_z)] if img.kind == "partclone" else ["--raw"])]


def recv(cc: Path, img: Image, targets: list[Path], addr: str, session: str, state: Path, *, tag: Path,
         pass_: int = 1, part: int = 3, start_s: int = 20, stall_s: int = 5, env=None,
         index_xxh: str | None = None, extra: tuple[str, ...] = ()) -> Run:
    """Starts a receiver and returns once it is listening."""
    run = _spawn([str(cc / "chunkcast"), "recv", "--listen", addr, "--session", session,
                  "--part", str(part), "--pass", str(pass_), *image_args(img, index_xxh), "--state", str(state),
                  "--start-s", str(start_s), "--stall-s", str(stall_s), "--sync-ms", "100", *extra,
                  *map(str, targets)], tag, env)
    run.wait_for("listening")
    return run


def send(cc: Path, img: Image, addr: str, session: str, *, tag: Path, pass_: int = 1, part: int = 3,
         chunks: str | None = None, file: Path | None = None, kbps: int = 80_000,
         write_kbps: int = 800_000, extra: tuple[str, ...] = ()) -> Run:
    """kbps: 10MB/s on the wire -- about 7,000 packets a second. A test host caps a
    socket's receive buffer at ~425KB (rmem_max, no root), and under a loaded queue a
    faster stream lost real packets on loopback (testrunner, 12/11, load 10).
    write_kbps: 100MB/s of decompressed bytes -- a chunk every 42ms."""
    if chunks is not None:                  # the list of a repair pass is a file (E2BIG), never an argument
        listed = tag.with_suffix(".chunks")
        listed.write_text(chunks + "\n", encoding="ascii")
        extra = ("--chunks-file", str(listed), *extra)
    return _spawn([str(cc / "chunkcast"), "send", "--to", addr, "--session", session, "--part", str(part),
                   "--pass", str(pass_), "--index", str(img.idx), "--file", str(file or img.file),
                   "--kbps", str(kbps), "--write-kbps", str(write_kbps), "--lead-ms", "200", "--tail-ms", "500",
                   *extra], tag)


def apply(cc: Path, img: Image, targets: list[Path], state: Path, first: int, count: int, frames: bytes, *,
          tag: Path, part: int = 3, env=None, chunks: str | None = None, extra: tuple[str, ...] = (),
          seconds: float = 60) -> Run:
    """`chunks` ("A-B,C-D"): the list of a whole fetch, in a file, instead of --first/--count."""
    src = tag.with_suffix(".in")
    src.write_bytes(frames)
    which = ["--first", str(first), "--count", str(count)]
    if chunks is not None:
        listed = tag.with_suffix(".chunks")
        listed.write_text(chunks + "\n", encoding="ascii")
        which = ["--chunks-file", str(listed)]
    with src.open("rb") as stdin:
        run = _spawn([str(cc / "chunkcast"), "apply", "--part", str(part), *image_args(img), "--state", str(state),
                      *which, "--sync-ms", "100", *extra, *map(str, targets)], tag, env, stdin)
        run.wait(seconds)
    return run


def state_of(state: Path) -> dict[str, dict]:
    """The state file: {target path: {"alive", "durable": set of chunks, "reason"}}."""
    lines = state.read_text().splitlines()
    assert lines[0].startswith("chunkcast-state 1 "), lines[0]
    out = {}
    for line in lines[1:]:
        path, verdict, count, ranges, *reason = line.split(" ", 4)
        have: set[int] = set()
        for piece in ([] if ranges == "-" else ranges.split(",")):
            lo, hi = piece.split("-")
            have.update(range(int(lo), int(hi) + 1))
        assert len(have) == int(count), line
        out[path] = {"alive": verdict == "ok", "durable": have, "reason": reason[0] if reason else ""}
    return out


def ranges_of(chunks: set[int] | list[int]) -> str:
    """{1, 2, 3, 7} -> "1-3,7-7" -- the --chunks form."""
    out, run = [], []
    for k in sorted(chunks):
        if run and k == run[-1] + 1:
            run.append(k)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}")
        run = [k]
    if run:
        out.append(f"{run[0]}-{run[-1]}")
    return ",".join(out)


class MemLedger:
    """The write ledger for the engine's tests that run without a database: the
    interface of `chunksend.DbLedger` (union only), in a dict. `refuse`: serials
    the ledger does not know -- as a disk whose hello carried no serial or size."""

    def __init__(self, refuse=()):
        self.rows: dict[tuple, list] = {}
        self.refuse = set(refuse)

    def record(self, session_id, image_id, mac, serial, part, ranges):
        from server.rounds import repair
        if serial in self.refuse:
            return "the server does not know this disk"
        key = (session_id, mac, serial, part)
        self.rows[key] = repair.union(self.rows.get(key, []), ranges)
        return None

    def durable(self, session_id, mac, serial, part):
        return list(self.rows.get((session_id, mac, serial, part), []))


# --- the relay ------------------------------------------------------------------

class Relay:
    """Between one sender and its receivers, on loopback. `rule(receiver, type,
    chunk, seq, packet)` returns the packets that receiver gets for this one:
    [] loses it, [p, p] repeats it, [changed] damages it. A rule decides by what
    the packet IS, never by when it came, so a run is repeatable."""

    def __init__(self, receivers: int, rule=None, reverse: int = 0, source: str = "127.0.0.1"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 << 20)
        self.sock.bind((source, 0))     # the address every receiver sees the stream come from (#1079: `recv --from`)
        self.sock.settimeout(0.2)
        self.addr = f"{source}:{self.sock.getsockname()[1]}"
        self.host = "127.0.0.1"         # where the receivers are; a multicast group in the end-to-end test
        self.ports = [free_port() for _ in range(receivers)]
        self.rule = rule or (lambda receiver, kind, chunk, seq, packet: [packet])
        self.reverse = reverse          # hold this many packets and release them newest first
        self.forwarded = [0] * receivers
        self.chunks_seen: list[set[int]] = [set() for _ in range(receivers)]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def listen(self, receiver: int) -> str:
        return f"127.0.0.1:{self.ports[receiver]}"

    def _out(self, packets: list[bytes]) -> None:
        for packet in packets:
            kind = packet[4]
            chunk, seq = (struct.unpack_from(">QxxxxI", packet, 16) if kind == DATA else (None, None))
            for r, port in enumerate(self.ports):
                for copy in self.rule(r, kind, chunk, seq, packet):
                    self.sock.sendto(copy, (self.host, port))
                    self.forwarded[r] += 1
                    if kind == DATA:
                        self.chunks_seen[r].add(chunk)

    def _run(self) -> None:
        held: list[bytes] = []
        while not self._stop.is_set():
            try:
                packet = self.sock.recv(2048)
            except socket.timeout:
                self._out(held[::-1])
                held = []
                continue
            if self.reverse and packet[4] == DATA:
                held.append(packet)
                if len(held) >= self.reverse:
                    self._out(held[::-1])
                    held = []
                continue
            self._out(held[::-1] + [packet])
            held = []

    def close(self) -> None:
        self._stop.set()
        self._thread.join(5)
        self.sock.close()
