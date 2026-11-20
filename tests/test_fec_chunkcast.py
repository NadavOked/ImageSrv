"""Exercise the actual send/recv processes and the pre-FEC v1 datagram layout."""
from collections import Counter
import json
import struct

import pytest

import chunkcast_rig as C
from native import requires_native
from test_chunk_b3_3332 import b3, b3_list

pytestmark = requires_native("gcc", "zstd", posix=True, why="Linux chunkcast loopback")


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("fec-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    return C.build(tmp_path_factory.mktemp("fec-img"), cc / "fanout", kind="raw", chunks=0.03125)


@pytest.mark.parametrize("drop,wrong_b3,complete,longest,masks", [
    ({31, 32}, False, True, 2, [[0, 0, 1 << 31], [0, 1, 1]]),
    ({4, 5}, False, False, 2, [[0, 0, 48]]),
    ({91}, False, True, 1, [[0, 2, 1 << 27]]),  # short final packet in a partial group
    ({7}, True, False, 1, [[0, 0, 128]]),
])
def test_one_pass_through_lossy_relay(cc, img, tmp_path, drop, wrong_b3, complete, longest, masks):
    listed, digest = b3_list(cc, img, tmp_path)
    if wrong_b3:
        listed.write_bytes(bytes(32))
        digest = b3(cc, "b3sum", listed.read_bytes()).decode().strip()
    data = Counter()

    def rule(receiver, kind, chunk, seq, packet):
        if kind == C.DATA:
            data[seq] += 1
            if seq in drop:
                return []
        return [packet]

    relay = C.Relay(1, rule)
    disk, state, session = img.blank(tmp_path / "disk"), tmp_path / "state", C.new_session()
    try:
        rx = C.recv(cc, img, [disk], relay.listen(0), session, state, tag=tmp_path / "rx",
                    extra=("--fec", "--b3-file", str(listed), "--b3-file-hash", digest))
        tx = C.send(cc, img, relay.addr, session, tag=tmp_path / "tx", extra=("--fec",))
        assert tx.wait() == 0, tx.log()
        assert rx.wait() == 0, rx.log()  # END finishes even a partial pass; durable state is the verdict.
    finally:
        relay.close()
    packets = (img.zlens[0] + 1439) // 1440
    assert data == Counter(range(packets))
    line, = [s for s in rx.out.read_text().splitlines() if s.startswith("loss_shape ")]
    assert json.loads(line.split(" ", 1)[1]) == dict(k=32, chunks=1, packets=packets, lost=len(drop),
                                                     longest=longest, groups=len(masks), masks=masks)
    have = {0} if complete else set()
    assert C.state_of(state)[str(disk)]["durable"] == have
    assert disk.read_bytes() == img.expected(have)
    if wrong_b3:
        assert "wrong BLAKE3" in rx.err.read_text()
    if not complete:
        assert "pass complete" not in rx.out.read_text()


def test_default_sender_matches_legacy_wire_bytes(cc, img, tmp_path):
    wire = []

    def capture(receiver, kind, chunk, seq, packet):
        wire.append(packet)
        return []

    session = C.new_session()
    relay = C.Relay(1, capture)
    try:
        tx = C.send(cc, img, relay.addr, session, tag=tmp_path / "sender")
        assert tx.wait() == 0, tx.log()
    finally:
        relay.close()

    def header(kind):
        return struct.pack(">IBBHQ", 0x49534331, kind, 1, 3, int(session, 16))

    control = struct.pack(">IQQ", 1, img.n, int(img.index_xxh, 16))
    frame = img.frame(0)
    data = [header(2) + struct.pack(">QII", 0, len(frame), i // 1440) + frame[i:i + 1440]
            for i in range(0, len(frame), 1440)]
    assert wire == [header(1) + control] * 2 + data + [header(3) + control] * 6
