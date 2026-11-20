"""#3161 -- `chunkcast send --stdin`: the sender of a direct send, and a whole direct wave with it.

In a direct send the source is a disk in a build machine, not a file in the library. The
partition file exists only as a stream -- `partclone | chunkcut`, read again for every pass --
and the index is the one the FIRST read of that disk wrote. So the sender takes the frames from
stdin, in order, and refuses a stream that is not the file its index describes.

Real binaries (`agent/chunkcast.c`, built as the initramfs builder builds it), files for disks,
loopback for the network. In the last test the server's real `ChunkEngine` orchestrates and a
thread stands for the build machine: it does what `directchunk.sh` does with an order -- runs
`chunkcast send --stdin` over the "disk" and reports the exit code. The shell itself is in
test_directchunk_agent_3161.py; partclone and chunkcut are not run here (the stream is the file
the rig built, which is what they would write).

**Negative control:** on `chunkcast.c` of the base commit `--stdin` is "bad option" (exit 2), and
every test here that sends fails on its first assertion about the sender's exit code. The last
test imports `server.rounds.directchunk`, which the base does not have: its control is a mutation
(`threshold` back to the default -- the wave then answers `fetch`), in the PR's body.
"""

from __future__ import annotations

import http.client
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

import chunkcast_rig as C
import pcl_image as P
from native import requires_native

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast needs gcc, libzstd and zstd")

PART = 3


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("stdin3161-bin"))


@pytest.fixture(scope="module", params=["partclone", "raw"])
def img(request, cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp(f"img-{request.param}"), cc / "fanout", kind=request.param, noisy=(0, 2))


def send_stdin(cc: Path, img: C.Image, addr: str, session: str, stream: bytes, *, tag: Path, pass_: int = 1,
               chunks: str | None = None, extra: tuple[str, ...] = ()) -> C.Run:
    """`chunkcast send --stdin` with `stream` on its stdin -- what `partclone | chunkcut` would write."""
    src = tag.with_suffix(".in")
    src.write_bytes(stream)
    if chunks is not None:
        listed = tag.with_suffix(".chunks")
        listed.write_text(chunks + "\n", encoding="ascii")
        extra = ("--chunks-file", str(listed), *extra)
    with src.open("rb") as stdin:
        return C._spawn([str(cc / "chunkcast"), "send", "--stdin", "--to", addr, "--session", session,
                         "--part", str(PART), "--pass", str(pass_), "--index", str(img.idx),
                         "--kbps", "80000", "--write-kbps", "800000", "--lead-ms", "200", "--tail-ms", "500",
                         *extra], tag, stdin=stdin)


def _reap(run: C.Run) -> None:
    """A receiver a failed assertion left listening is not left behind (the suite's hygiene check)."""
    if run.process.poll() is None:
        run.process.kill()
    run.process.wait(10)


@pytest.fixture()
def pair(cc, img, tmp_path):
    """A receiver on loopback and its blank target: (address, session, receiver, target, state)."""
    addr, session = f"127.0.0.1:{C.free_port()}", C.new_session()
    target, state = img.blank(tmp_path / "t.disk"), tmp_path / "t.state"
    run = C.recv(cc, img, [target], addr, session, state, tag=tmp_path / "recv", part=PART, stall_s=4)
    yield addr, session, run, target, state
    _reap(run)


def test_a_partition_that_arrives_on_stdin_is_sent_whole_and_written_as_the_image(cc, img, tmp_path, pair):
    addr, session, receiver, target, state = pair
    sent = send_stdin(cc, img, addr, session, img.file.read_bytes(), tag=tmp_path / "send")
    assert sent.wait() == 0, sent.log()
    assert f"done {img.n} " in sent.out.read_text()
    assert receiver.wait() == 0, receiver.log()
    assert C.state_of(state)[str(target)]["durable"] == set(range(img.n))
    assert target.read_bytes() == img.expected()


def test_a_repair_pass_reads_the_whole_stream_and_sends_only_its_chunks(cc, img, tmp_path, pair):
    addr, session, receiver, target, state = pair
    sent = send_stdin(cc, img, addr, session, img.file.read_bytes(), tag=tmp_path / "send", chunks="1-1,3-3")
    assert sent.wait() == 0, sent.log()
    assert "done 2 " in sent.out.read_text()                # two chunks sent; the stream was read to its end
    assert receiver.wait() in (0, 4), receiver.log()
    assert C.state_of(state)[str(target)]["durable"] == {1, 3}
    assert target.read_bytes() == img.expected({1, 3})


@pytest.mark.parametrize("slow", ["a read that waits for the disk", "chunk after chunk that is not sent"])
def test_a_repair_pass_keeps_its_receivers_while_it_reads_what_it_does_not_send(cc, tmp_path, slow):
    """Review of #3216, P1. A repair pass reads the whole partition to send a few chunks of it. A receiver's
    stall clock is reset by the sender's packets only: one that heard nothing while the sender read the chunks
    before the one it lacked left as "stalled" -- and missed it, in this pass and in every pass after it.
    Here the receiver's patience is 4 seconds, and the last chunk -- the only one sent -- comes after 9."""
    img = C.build(tmp_path / "img", cc / "fanout", kind="raw", noisy=(), chunks=5.0)
    addr, session, last = f"127.0.0.1:{C.free_port()}", C.new_session(), img.n - 1
    target, state = img.blank(tmp_path / "t.disk"), tmp_path / "t.state"
    receiver = C.recv(cc, img, [target], addr, session, state, tag=tmp_path / "recv", part=PART, stall_s=4)
    listed = tmp_path / "repair.chunks"
    listed.write_text(f"{last}-{last}\n", encoding="ascii")
    sender = C._spawn([str(cc / "chunkcast"), "send", "--stdin", "--to", addr, "--session", session,
                       "--part", str(PART), "--pass", "1", "--index", str(img.idx), "--chunks-file", str(listed),
                       "--kbps", "80000", "--write-kbps", "800000", "--lead-ms", "200", "--tail-ms", "500"],
                      tmp_path / "send", stdin=subprocess.PIPE)
    stream, pipe = img.file.read_bytes(), sender.process.stdin
    try:
        try:
            if slow == "a read that waits for the disk":
                pipe.write(stream[:img.offsets[last]])      # everything before it at once -- and then nothing
                pipe.flush()
                time.sleep(9)
            else:
                for k in range(last):                       # a frame, a pause, a frame: 9 seconds of reading
                    pipe.write(stream[img.offsets[k]:img.offsets[k] + img.zlens[k]])
                    pipe.flush()
                    time.sleep(9 / last)
            pipe.write(stream[img.offsets[last]:])
            pipe.close()
        except BrokenPipeError:
            pass                                            # a sender that left early: its exit code says why, below
        assert sender.wait(30) == 0, sender.log()
        assert receiver.wait(30) == 0, receiver.log()       # it was still there, and saw the END
        assert C.state_of(state)[str(target)]["durable"] == {last}
        assert target.read_bytes() == img.expected({last})
    finally:
        _reap(sender)
        _reap(receiver)


def test_a_stream_that_ends_inside_a_chunk_is_refused_and_is_not_a_sent_pass(cc, img, tmp_path, pair):
    addr, session, receiver, target, _state = pair
    cut = img.offsets[2] + img.zlens[2] // 2                # the disk "ended" in the middle of chunk 2
    sent = send_stdin(cc, img, addr, session, img.file.read_bytes()[:cut], tag=tmp_path / "send")
    assert sent.wait() == 9, sent.log()
    assert "not the file its index describes (chunk 2" in sent.err.read_text()
    assert "done" not in sent.out.read_text()               # no "done" line: the caller's evidence of a sent pass
    receiver.wait()                                         # it stalls (no END was sent) and says what it has
    assert target.read_bytes() == img.expected({0, 1})      # what came before the cut, verified, and nothing else


def test_a_stream_of_another_disk_is_refused_at_the_first_frame_that_is_elsewhere(cc, img, tmp_path, pair):
    """Read 2 gave other frames than read 1 (the disk changed): the boundaries of the index are not its."""
    other = C.build(tmp_path / "other", cc / "fanout", kind=img.kind, noisy=(1,), salt=31337)
    assert other.zlens != img.zlens
    addr, session, receiver, target, _state = pair
    sent = send_stdin(cc, img, addr, session, other.file.read_bytes(), tag=tmp_path / "send")
    assert sent.wait() == 9, sent.log()
    assert "done" not in sent.out.read_text()
    receiver.wait()
    assert target.read_bytes() == img.expected(set())       # not one byte of the other disk reached the target


def test_a_frame_at_its_place_with_other_content_goes_out_and_is_never_written(cc, img, tmp_path, pair):
    """The sender checks WHERE the frames are; the receivers check WHAT they hold (the index's XXH64); and
    the build machine compares the sha256 of the whole stream with the manifest (directchunk.sh). Here the
    first two: chunk 1 changed on the disk between the reads without moving a boundary -- it is sent, the
    receiver refuses it, and it stays missing. Nothing wrong is written, and nothing is dropped in silence."""
    stream = bytearray(img.file.read_bytes())
    stream[img.offsets[1] + img.zlens[1] // 2] ^= 0xFF      # inside the frame of chunk 1; its length is what it was
    addr, session, receiver, target, state = pair
    sent = send_stdin(cc, img, addr, session, bytes(stream), tag=tmp_path / "send")
    assert sent.wait() == 0, sent.log()
    assert receiver.wait() in (0, 4), receiver.log()
    whole = set(range(img.n)) - {1}
    assert C.state_of(state)[str(target)]["durable"] == whole
    assert target.read_bytes() == img.expected(whole)


def test_a_stream_that_goes_on_past_its_index_is_refused(cc, img, tmp_path, pair):
    addr, session, receiver, _target, _state = pair
    sent = send_stdin(cc, img, addr, session, img.file.read_bytes() + b"x", tag=tmp_path / "send")
    assert sent.wait() == 9, sent.log()
    assert "goes on past" in sent.err.read_text() and "done" not in sent.out.read_text()


def test_the_sender_takes_a_file_or_stdin_never_both_and_never_neither(cc, img, tmp_path):
    base = [str(cc / "chunkcast"), "send", "--to", "127.0.0.1:9", "--session", C.new_session(), "--part", "3",
            "--pass", "1", "--index", str(img.idx)]
    for extra in (["--stdin", "--file", str(img.file)], []):
        done = subprocess.run(base + extra, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
        assert done.returncode == 2 and "one of --file and --stdin" in done.stderr, (extra, done.stderr)


def test_a_sender_that_waits_for_the_disk_stops_at_a_signal(cc, img, tmp_path):
    """A read of a disk that hangs must not hold the sender past the round's end: SIGTERM ends it, by name."""
    out, err = (tmp_path / "s.out").open("wb"), (tmp_path / "s.err").open("wb")
    waiting = subprocess.Popen(
        [str(cc / "chunkcast"), "send", "--stdin", "--to", f"127.0.0.1:{C.free_port()}", "--session", C.new_session(),
         "--part", "3", "--pass", "1", "--index", str(img.idx), "--lead-ms", "0"],
        stdin=subprocess.PIPE, stdout=out, stderr=err)       # a pipe nobody writes to
    try:
        time.sleep(0.5)
        assert waiting.poll() is None
        waiting.send_signal(signal.SIGTERM)
        assert waiting.wait(10) == 6
        assert "stopped by a signal" in (tmp_path / "s.err").read_text()
    finally:
        waiting.kill()
        waiting.stdin.close()
        out.close(), err.close()


# --- a whole direct wave: the server's engine, the source's sender, the receivers ------------------

SID, IMAGE = "wave-3161", "live_0000a161"


def _ranges(chunks):
    return [[int(lo), int(hi)] for lo, hi in (r.split("-") for r in C.ranges_of(chunks).split(",") if r)]


def test_a_direct_wave_is_orchestrated_by_the_server_and_sent_and_repaired_by_the_source(cc, tmp_path, monkeypatch):
    """Two cloners. `b` loses chunk 1 in the first pass. In a library wave it would fetch it over HTTP;
    here there is no file on the server, so the source is ordered to send it again, and does."""
    from server.rounds import chunksend, directchunk

    monkeypatch.setattr(chunksend, "POLL_S", 0.02)
    monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0)
    img = C.build(tmp_path / "img", cc / "fanout", noisy=(0, 2))
    seen_pass = {"n": 1}

    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.ANNOUNCE:
            seen_pass["n"] = int.from_bytes(packet[16:20], "big")
        return [] if (kind == C.DATA and seen_pass["n"] == 1 and receiver == 1 and chunk == 1 and seq == 0) else [packet]

    relay = C.Relay(2, rule)
    live = directchunk.DirectChunks(None, tmp_path / "direct", engine_on=lambda: True, silence=lambda: 30)
    # what the build machine uploads before its manifest, and the manifest that signs it
    assert live.store.put_index(IMAGE, PART, img.idx.read_bytes()) is None
    assert live.store.put_head(IMAGE, PART, img.head_z.read_bytes()) is None
    # #3332: and its BLAKE3 list, as chunkcut writes it beside the index -- the manifest names it (chunk_b3)
    listed = subprocess.run([str(cc / "chunkcast"), "b3list"], input=img.data, capture_output=True, check=True).stdout
    assert live.store.put_b3(IMAGE, PART, listed) is None
    named = subprocess.run([str(cc / "chunkcast"), "b3sum"], input=listed, capture_output=True, check=True).stdout
    manifest = {"id": IMAGE, "partitions": [{"index": PART, "file": img.file.name, "index_xxh64": img.index_xxh,
                                             "chunk_b3": named.decode().strip()}]}
    assert directchunk.seal(live.store, IMAGE, manifest) is None and manifest["partitions"][0]["readback_chunks"] == img.n

    class Lib:
        def get(self, image_id):
            return manifest

        def file_path(self, image_id, name):
            return live.store.part_path(image_id, PART)

    events, answers, errors, orders, receivers = [], [], [], [], []
    macs = ["a", "b"]
    engine = chunksend.ChunkEngine(
        Lib(), live_members=lambda sid: list(macs), lost_wait=lambda: 8, write_floor=lambda: 50,
        runner=live.runner(None), max_wait=15, start_timeout=15, wave_bitrate=lambda session: "200m",
        ledger=C.MemLedger(), on_event=lambda event, detail, sid: events.append((event, detail)))
    engine.no_file = lambda session_id: True
    live.engine = engine
    for mac in macs:
        engine.note_agent(mac, ["udpcast", "chunk"])
    assert engine.start({"id": SID, "image_id": IMAGE}) is None
    stop = threading.Event()

    def source():
        """The build machine: one `chunkcast send --stdin` over its "disk" for every order, then the verdict."""
        try:
            last = 0
            while not stop.is_set():
                order = live.order_for(IMAGE)
                if order is None or order["seq"] <= last:
                    time.sleep(0.05)
                    continue
                last = order["seq"]
                orders.append(order)
                listed = live.chunks_of(IMAGE, order["seq"]) if order["chunks"] else None
                for seen in relay.chunks_seen:
                    seen.clear()
                run = send_stdin(cc, img, relay.addr, order["key"], img.file.read_bytes(),
                                 tag=tmp_path / f"send{order['seq']}", pass_=order["pass"], chunks=listed)
                rc = run.wait()
                live.sent(IMAGE, {"seq": order["seq"], "state": "done" if rc == 0 else "failed", "rc": rc,
                                  "error": run.err.read_text()[-200:]})
        except BaseException as exc:   # noqa: BLE001 -- a thread's failure is the test's
            errors.append(exc)

    def cloner(mac, receiver, disks, state_file):
        """What chunkrecv.sh does for one partition -- and it must never be told to fetch."""
        def tell(pass_, state):
            durable = C.state_of(state_file) if state_file.exists() else {}
            return engine.report(SID, mac, {"part": PART, "pass": pass_, "state": state, "targets": [
                {"serial": serial, "write_mbps": 100,
                 "durable": [{"part": PART, "ranges": _ranges(durable.get(str(path), {}).get("durable", set()))}]}
                for serial, path in disks.items()]})
        try:
            pass_, action = 1, "receive"
            while action != "done":
                assert action == "receive", f"{mac}: the server answered {action}"
                run = C.recv(cc, img, list(disks.values()), relay.listen(receiver),
                             engine.session_fields(SID)["chunk"]["key"], state_file,
                             tag=tmp_path / f"{mac}-p{pass_}", pass_=pass_, part=PART, stall_s=10)
                receivers.append(run)
                assert tell(pass_, "listening")["action"] == "receive"
                assert run.wait() in (0, 4), run.log()
                end = time.monotonic() + 30
                while True:
                    answer = tell(pass_, "ended")
                    assert answer["ok"] and "fail" not in answer, answer
                    if answer["action"] != "wait":
                        break
                    assert time.monotonic() < end, f"{mac} is still told to wait"
                    time.sleep(0.1)
                answers.append((mac, answer["action"]))
                action, pass_ = answer["action"], answer.get("pass", pass_)
        except BaseException as exc:   # noqa: BLE001
            errors.append(exc)

    disks = {"a": {"A1": img.blank(tmp_path / "a1.disk")},
             "b": {"B1": img.blank(tmp_path / "b1.disk"), "B2": img.blank(tmp_path / "b2.disk")}}
    threads = [threading.Thread(target=source, daemon=True),
               *(threading.Thread(target=cloner, args=(mac, i, disks[mac], tmp_path / f"{mac}.state"), daemon=True)
                 for i, mac in enumerate(disks))]
    try:
        for t in threads:
            t.start()
        for t in threads[1:]:
            t.join(120)
        assert not errors, errors
        assert not any(t.is_alive() for t in threads[1:])
        end = time.monotonic() + 5
        while engine.status()["state"] != "done" and time.monotonic() < end:
            time.sleep(0.05)
        assert engine.status()["state"] == "done", (engine.status(), events)
    finally:
        stop.set()
        engine.close()
        relay.close()
        for run in receivers:
            _reap(run)

    for mac in disks:
        for path in disks[mac].values():
            assert path.read_bytes() == img.expected(), f"{path.name} is not the image"
    assert [(o["pass"], o["chunks"]) for o in orders] == [(1, False), (2, True)]     # one whole pass, one repair
    assert relay.chunks_seen[1] == {1}                      # the repair carried the one chunk that was lost
    assert ("b", "receive") in answers and not [a for a in answers if a[1] == "fetch"]
    assert engine.status()["fetched_chunks"] == 0 and engine.status()["multicast_chunks"] == 1


# --- #3209: single chunks, read from their own places on the disk ---------------------------------
#
# The image here is cut by the REAL chunkcut (agent/chunkcut.c), as read 1 of a direct send cuts it: what
# `--device` must reproduce is chunkcut's own frame of every chunk, byte for byte -- the receivers take a
# packet of another frame length for a foreign one. The rig's images are framed by the zstd CLI, and would
# only show that two other programs agree.

@pytest.fixture(scope="module")
def chunkcut(tmp_path_factory):
    binary = tmp_path_factory.mktemp("cut3209") / "chunkcut"
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(binary),
                            str(C.REPO / "agent/chunkcut.c"), "-l:libzstd.so.1"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return binary


def cut_disk(tmp: Path, cc: Path, chunkcut: Path, kind: str) -> tuple[C.Image, Path]:
    """(the image as chunkcut cut it, the "disk" it was read from). The disk holds garbage in its free
    blocks, so a reader that walked the map wrongly reads garbage and is caught."""
    tmp.mkdir(parents=True, exist_ok=True)
    bs, chunks, noisy = 4096, 4.5, (0, 2)
    size = int(chunks * C.CH) // bs * bs
    n = -(-size // C.CH)
    data = b"".join(C.chunk_bytes(k, min(C.CH, size - k * C.CH), noisy=k in noisy, salt=3209) for k in range(n))
    head, total, used = b"", 0, ()
    if kind == "partclone":
        used, i = [], 0
        while len(used) < size // bs:               # free blocks scattered among the used ones
            if i % 7 != 3:
                used.append(i)
            i += 1
        total = i + 5
        bm = P.bitmap(total, used)
        head = P.header(total, size // bs, bs) + bm + struct.pack("<I", P.crc(bm))
    file, idx = tmp / "p3.windows.pcl.zst", tmp / "p3.windows.pcl.zst.idx"
    cutter = subprocess.run([str(chunkcut), *(["--raw"] if kind == "raw" else []), "-l", "3", "-T", "2", "-i", str(idx)],
                            input=head + data, capture_output=True)
    assert cutter.returncode == 0, cutter.stderr
    file.write_bytes(cutter.stdout)
    rows = [line.split() for line in idx.read_text().splitlines()]
    head_z = None
    if kind == "partclone":
        head_z = tmp / "p3.head"
        head_z.write_bytes(cutter.stdout[:int(rows[0][5])])
    img = C.Image(kind, file, idx, C.xxh64(cc / "fanout", idx.read_bytes()), head_z, data,
                  [int(r[0]) for r in rows[1:]], [int(r[1]) for r in rows[1:]], total, bs, tuple(used))
    disk = tmp / "source.disk"
    disk.write_bytes(img.expected())
    return img, disk


@pytest.fixture(scope="module", params=["partclone", "raw"])
def cut(request, cc, chunkcut, tmp_path_factory):
    return cut_disk(tmp_path_factory.mktemp(f"cut-{request.param}"), cc, chunkcut, request.param)


def _device_args(img: C.Image, disk: Path) -> list[str]:
    return ["--index", str(img.idx), "--device", str(disk), "--level", "3",
            *(["--head", str(img.head_z)] if img.kind == "partclone" else [])]


def send_device(cc: Path, img: C.Image, disk: Path, addr: str, session: str, chunks: str, *, tag: Path) -> C.Run:
    listed = tag.with_suffix(".chunks")
    listed.write_text(chunks + "\n", encoding="ascii")
    return C._spawn([str(cc / "chunkcast"), "send", "--to", addr, "--session", session, "--part", str(PART),
                     "--pass", "2", *_device_args(img, disk), "--chunks-file", str(listed), "--kbps", "80000",
                     "--write-kbps", "800000", "--lead-ms", "200", "--tail-ms", "500"], tag)


def test_a_repair_reads_only_its_chunks_from_the_disk_and_they_are_chunkcuts_own_frames(cc, cut, tmp_path):
    img, disk = cut
    addr, session = f"127.0.0.1:{C.free_port()}", C.new_session()
    target, state = img.blank(tmp_path / "t.disk"), tmp_path / "t.state"
    receiver = C.recv(cc, img, [target], addr, session, state, tag=tmp_path / "recv", pass_=2, part=PART, stall_s=4)
    try:
        sent = send_device(cc, img, disk, addr, session, "1-1,3-4", tag=tmp_path / "send")
        assert sent.wait() == 0, sent.log()
        assert "done 3 " in sent.out.read_text()
        assert receiver.wait() in (0, 4), receiver.log()
        assert C.state_of(state)[str(target)]["durable"] == {1, 3, 4}      # taken: so each frame had the index's length
        assert target.read_bytes() == img.expected({1, 3, 4})
    finally:
        _reap(receiver)


def test_a_chunk_that_changed_on_the_disk_is_refused_before_a_byte_of_it_goes_out(cc, cut, tmp_path):
    img, disk = cut
    changed = tmp_path / "changed.disk"
    raw = bytearray(disk.read_bytes())
    where = 3 * C.CH + 100 if img.kind == "raw" else img.used[(3 * C.CH) // img.bs] * img.bs + 100
    raw[where] ^= 0xFF                                      # one byte of chunk 3, in its own place
    changed.write_bytes(raw)
    addr, session = f"127.0.0.1:{C.free_port()}", C.new_session()
    target, state = img.blank(tmp_path / "t.disk"), tmp_path / "t.state"
    receiver = C.recv(cc, img, [target], addr, session, state, tag=tmp_path / "recv", pass_=2, part=PART, stall_s=3)
    try:
        sent = send_device(cc, img, changed, addr, session, "1-1,3-3", tag=tmp_path / "send")
        assert sent.wait() == 9, sent.log()
        assert "the disk changed" in sent.err.read_text() and "done" not in sent.out.read_text()
        receiver.wait()
        assert target.read_bytes() == img.expected({1})    # chunk 1 before it, whole; of chunk 3 nothing
    finally:
        _reap(receiver)


def test_a_device_repair_needs_the_level_and_the_map(cc, cut, tmp_path):
    img, disk = cut
    base = [str(cc / "chunkcast"), "send", "--to", "127.0.0.1:9", "--session", C.new_session(), "--part", "3",
            "--pass", "2", "--index", str(img.idx), "--device", str(disk)]
    lacking = [base] + ([base + ["--level", "3"]] if img.kind == "partclone" else [])     # no level; no head
    for cmd in lacking:
        done = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
        assert done.returncode == 2 and "--device needs" in done.stderr, (cmd[-2:], done.stderr)


TOKEN = "5ec7e7c0ffee0123456789abcdef0123"       # what the image server of the round holds (`DirectChunks.serve_token`)
TOKEN_HEADER = "X-Imagesrv-Serve-Token"


def _serve(cc: Path, img: C.Image, disk: Path, tmp_path: Path, *extra: str, env: dict | None = None
           ) -> tuple[C.Run, int]:
    port = C.free_port()
    token_file = tmp_path / "serve.token"
    token_file.write_text(TOKEN + "\n", encoding="ascii")
    run = C._spawn([str(cc / "chunkcast"), "serve", "--listen", f"127.0.0.1:{port}", "--token-file", str(token_file),
                    *_device_args(img, disk), *extra], tmp_path / "serve", env={**C.ENV, **(env or {})})
    run.wait_for("serving")
    return run, port


def _ask(port: int, wanted: str, token: str | None = TOKEN) -> tuple[int, bytes]:
    link = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        link.request("GET", "/", headers={"Range": wanted, **({TOKEN_HEADER: token} if token is not None else {})})
        answer = link.getresponse()
        return answer.status, answer.read()
    finally:
        link.close()


def test_the_frame_server_answers_a_range_of_whole_chunks_with_the_files_own_bytes(cc, cut, tmp_path):
    """What a receiver's fetch asks the image server for (format-spec 8), answered from the disk: the bytes
    are the partition file's -- the file chunkcut wrote, which exists nowhere in a direct send."""
    img, disk = cut
    server, port = _serve(cc, img, disk, tmp_path)
    try:
        file = img.file.read_bytes()
        first, last = img.offsets[1], img.offsets[3] + img.zlens[3] - 1
        status, body = _ask(port, f"bytes={first}-{last}")
        assert status == 206 and body == file[first:last + 1]
        whole = _ask(port, f"bytes={img.offsets[0]}-{len(file) - 1}")          # every chunk, in one request
        assert whole[0] == 206 and whole[1] == file[img.offsets[0]:]
        assert _ask(port, f"bytes={first + 1}-{last}")[0] == 416               # not whole frames
        assert _ask(port, f"bytes={first}-{last - 1}")[0] == 416
        assert _ask(port, "bytes=0-0")[0] == 416                               # the head, or nothing: not here
        # and the frames are the ones a receiver's `apply` takes for a fetch
        target, state = img.blank(tmp_path / "t.disk"), tmp_path / "t.state"
        applied = C.apply(cc, img, [target], state, 1, 3, body, tag=tmp_path / "apply", part=PART)
        assert applied.process.returncode == 0, applied.log()
        assert target.read_bytes() == img.expected({1, 2, 3})
    finally:
        server.process.terminate()
        assert server.process.wait(10) == 0


def test_the_frame_server_answers_only_the_peer_it_was_told_to(cc, cut, tmp_path):
    img, disk = cut
    server, port = _serve(cc, img, disk, tmp_path, "--allow", "127.0.0.9")     # the image server, elsewhere
    try:
        with pytest.raises((http.client.HTTPException, OSError)):
            _ask(port, f"bytes={img.offsets[1]}-{img.offsets[1] + img.zlens[1] - 1}")
    finally:
        _reap(server)


def test_a_chunk_that_changed_is_never_served_the_answer_is_cut_short(cc, cut, tmp_path):
    img, disk = cut
    changed = tmp_path / "changed.disk"
    raw = bytearray(disk.read_bytes())
    raw[2 * C.CH + 7 if img.kind == "raw" else img.used[(2 * C.CH) // img.bs] * img.bs + 7] ^= 0xFF
    changed.write_bytes(raw)
    server, port = _serve(cc, img, changed, tmp_path)
    try:
        first, last = img.offsets[1], img.offsets[2] + img.zlens[2] - 1
        with pytest.raises((http.client.IncompleteRead, OSError)):             # chunk 1, and then the line is closed
            _ask(port, f"bytes={first}-{last}")
        assert "the disk changed" in server.err.read_text()
    finally:
        _reap(server)


def test_the_frame_server_answers_only_a_request_with_the_rounds_token(cc, cut, tmp_path):
    """The review of #3216: "the peer's address" was never who may ask -- when the image server's address is a name
    nothing narrowed it at all. Now no token or a wrong one is 403; the right one is 206 with the file's own bytes."""
    img, disk = cut
    server, port = _serve(cc, img, disk, tmp_path)
    try:
        first, last = img.offsets[1], img.offsets[2] + img.zlens[2] - 1
        wanted = f"bytes={first}-{last}"
        assert _ask(port, wanted, token=None)[0] == 403
        for wrong in ("", "x", TOKEN[:-1], TOKEN + "0", TOKEN[:-1] + "1", TOKEN.upper(), "0" * len(TOKEN)):
            assert _ask(port, wanted, token=wrong)[0] == 403, wrong
        status, body = _ask(port, wanted)
        assert status == 206 and body == img.file.read_bytes()[first:last + 1]
        assert _ask(port, "bytes=0-0")[0] == 416                         # the token is a condition, not a key to everything
    finally:
        server.process.terminate()
        assert server.process.wait(10) == 0
    assert TOKEN not in server.log()                                      # not in what it says, either


def test_the_frame_server_that_has_no_token_does_not_start(cc, cut, tmp_path):
    """Fail closed (principle 5): "I could not tell who may ask" is never "everybody may"."""
    img, disk = cut
    tokens = {name: tmp_path / f"{name}.token" for name in ("short", "empty", "spaced")}
    tokens["short"].write_text("abc\n", encoding="ascii")
    tokens["empty"].write_text("", encoding="ascii")
    tokens["spaced"].write_text("0123456789abcdef 0123456789abcdef\n", encoding="ascii")
    base = [str(cc / "chunkcast"), "serve", "--listen", f"127.0.0.1:{C.free_port()}", *_device_args(img, disk)]
    for extra in ([], ["--token-file", str(tmp_path / "absent.token")], *(["--token-file", str(p)] for p in tokens.values())):
        done = subprocess.run(base + extra, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20,
                              env=C.ENV)
        assert done.returncode == 2 and "serving" not in done.stdout and "--token-file" in done.stderr, (extra, done.stderr)


def test_a_connection_that_trickles_its_request_is_cut_at_the_ceiling_and_the_next_one_is_answered(cc, cut, tmp_path):
    """The review of #3216: `SO_RCVTIMEO` is per `recv`, so one byte every few seconds held the one-at-a-time server for
    hours. The request has a ceiling of its own, from the connection's start, whatever the bytes in between."""
    img, disk = cut
    server, port = _serve(cc, img, disk, tmp_path, env={"CHUNKCAST_SERVE_READ_MS": "1500"})
    try:
        began, closed = time.monotonic(), False
        with socket.create_connection(("127.0.0.1", port), timeout=20) as slow:
            slow.settimeout(0.3)
            while time.monotonic() - began < 15 and not closed:     # a byte a beat: no single recv ever waits long
                try:
                    slow.sendall(b"A")                              # never the blank line that ends a request
                    slow.recv(1)
                except socket.timeout:
                    continue                                        # nothing said yet: the event is the close
                except OSError:
                    closed = True
                else:
                    closed = True                                   # recv() == b"": the server closed it
        took = time.monotonic() - began
        assert closed and took < 8, f"the trickle was still served after {took:.1f}s"
        first, last = img.offsets[1], img.offsets[1] + img.zlens[1] - 1
        assert _ask(port, f"bytes={first}-{last}")[0] == 206         # and the server is free again
    finally:
        _reap(server)


def test_a_connection_gets_one_answer_and_is_closed(cc, cut, tmp_path):
    """The first request is answered and the connection is closed: a second one on it -- within the ceiling, with the
    token -- gets nothing. The relay asks once a connection anyway."""
    img, disk = cut
    server, port = _serve(cc, img, disk, tmp_path)
    try:
        first, last = img.offsets[1], img.offsets[1] + img.zlens[1] - 1
        one = f"GET / HTTP/1.1\r\nHost: x\r\nRange: bytes={first}-{last}\r\n{TOKEN_HEADER}: {TOKEN}\r\n\r\n".encode()
        with socket.create_connection(("127.0.0.1", port), timeout=20) as link:
            link.sendall(one)
            got = b""
            while b"\r\n\r\n" not in got:
                got += link.recv(1 << 16)
            head, _, body = got.partition(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 206") and b"Connection: close" in head
            while len(body) < img.zlens[1]:
                body += link.recv(1 << 16)
            assert body == img.file.read_bytes()[first:last + 1]
            more = b""
            try:
                link.sendall(one)                                   # the same connection, once more
                while chunk := link.recv(1 << 16):                  # until the server closes it
                    more += chunk
            except OSError:
                pass                                                # reset: closed, and that is the point
            assert more == b""
    finally:
        _reap(server)
