"""#3332: מנוע החתיכות בודק כל חתיכה ב-BLAKE3 לפני שהיא נכתבת (`agent/chunkcast.c`, `agent/blake3/`).

XXH64 אינו קריפטוגרפי: אפשר לבנות חתיכה של 4MiB עם תוכן אחר ואותו XXH64 בזמן קצר. כאן נבנית חתיכה כזו
(`same_xxh64_other_bytes`), ונבדק ש-XXH64 של האינדקס **אכן** תואם לה (`fanout --xxh64`) -- כלומר שרק BLAKE3 עוצר
אותה -- ושהיא אינה מגיעה לדיסק, לא ב-`apply` (HTTP, מילוי משכנים, P2P) ולא ב-`recv` (המולטיקאסט). רשימת ה-BLAKE3
עצמה מאומתת מול ה-hash שבמניפסט, ומימוש ה-BLAKE3 מול וקטורי הבדיקה הרשמיים (`test_vectors.json` של BLAKE3 1.8.1,
קלט `i % 251`).

**בקרה שלילית:** `agent/chunkcast.c`, `tools/build_initramfs.sh`, `tools/server-tools-build.sh` ו-
`tests/chunkcast_rig.py`, `agent/chunkcut.c` ו-`agent/lib/capture/capturesink.sh` חוזרים לבסיס, ו-`agent/blake3/` נמחקת. chunkcast אינו מכיר `--b3-file`, `b3list` ו-
`b3sum` (exit 2, "bad option" / usage), וכל טסט כאן נופל ב-AssertionError על קוד היציאה או על מה שנכתב.
"""

from __future__ import annotations

import random
import struct
import subprocess
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from test_agent import posix, sh

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast needs gcc, libzstd and zstd")

#: BLAKE3 1.8.1, test_vectors/test_vectors.json: (input_len, hash) for the input bytes i % 251
VECTORS = (
    (0, "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"),
    (1, "2d3adedff11b61f14c886e35afa036736dcd87a74d27b5c1510225d0f592e213"),
    (64, "4eed7141ea4a5cd4b788606bd23f46e212af9cacebacdc7d1f4c6dc7f2511b98"),
    (65, "de1e5fa0be70df6d2be8fffd0e99ceaa8eb6e8c93a63f2d8d1c30ecb6b263dee"),
    (1023, "10108970eeda3eb932baac1428c7a2163b0e924c9a9e25b35bba72b28f70bd11"),
    (1024, "42214739f095a406f3fc83deb889744ac00df831c10daa55189b5d121c855af7"),
    (1025, "d00278ae47eb27b34faecf67b4fe263f82d5412916c1ffd97c8cb7fb814b8444"),
    (2048, "e776b6028c7cd22a4d0ba182a8bf62205d2ef576467e838ed6f2529b85fba24a"),
    (2049, "5f4d72f40d7a5f82b15ca2b2e44b1de3c2ef86c426c95c1af0b6879522563030"),
    (3072, "b98cb0ff3623be03326b373de6b9095218513e64f1ee2edd2525c7ad1e5cffd2"),
    (4097, "9b4052b38f1c5fc8b1f9ff7ac7b27cd242487b3d890d15c96a1c25b8aa0fb995"),
    (8193, "bab6c09cb8ce8cf459261398d2e7aef35700bf488116ceb94a36d0f5f1b7bc3b"),
    (16384, "f875d6646de28985646f34ee13be9a576fd515f76b5b0a26bb324735041ddde4"),
    (31744, "62b6960e1a44bcc1eb1a611a8d6235b6b4b78f32e7abc4fb4c6cdcce94895c47"),
    (102400, "bc3e3d41a1146b069abffad3c0d44860cf664390afce4d9661f7902e7943e085"),
)

_M = (1 << 64) - 1
_P1, _P2 = 11400714785074694791, 14029467366897019727


def _r(a: int, x: int) -> int:
    a = (a + x * _P2) & _M
    return (((a << 31) | (a >> 33)) & _M) * _P1 & _M


def _s(data, upto: int) -> list[int]:
    v = [(_P1 + _P2) & _M, _P2, 0, (-_P1) & _M]
    for w in struct.iter_unpack("<4Q", memoryview(data)[:upto]):
        v = [_r(v[i], w[i]) for i in range(4)]
    return v


def _u(before: int, after: int) -> int:
    t = (after * pow(_P1, -1, 1 << 64)) & _M
    t = ((t >> 31) | (t << 33)) & _M
    return ((t - before) & _M) * pow(_P2, -1, 1 << 64) & _M


def same_xxh64_other_bytes(data: bytes) -> bytes:
    """A chunk of `data`'s length, other bytes, and `data`'s XXH64 (seed 0). `len(data)` is a multiple of 32."""
    assert len(data) % 32 == 0 and len(data) >= 64
    out = bytearray(data)
    mark = b"NOT THE IMAGE'S BYTES " * 64
    out[1 << 20:(1 << 20) + len(mark)] = mark
    end = len(out) - 32
    want, have = _s(data, len(data)), _s(out, end)
    out[end:] = struct.pack("<4Q", *(_u(have[i], want[i]) for i in range(4)))
    return bytes(out)


def same_frame_other_bytes(frame: bytes, data: bytes) -> bytes:
    """`frame` (a zstd frame of raw blocks, as an incompressible chunk is stored) with `data` in its blocks: the same
    length and header -- and the same content checksum, which is XXH64 too."""
    fhd = frame[4]
    at = 5 + (0 if fhd & 0x20 else 1) + (0, 1, 2, 4)[fhd & 3]
    at += (1 if fhd & 0x20 else 0, 2, 4, 8)[fhd >> 6]
    out, pos = bytearray(frame[:at]), 0
    while True:
        head = int.from_bytes(frame[at:at + 3], "little")
        last, kind, size = head & 1, (head >> 1) & 3, head >> 3
        assert kind == 0, "the chunk is not stored raw: pick an incompressible one"
        out += frame[at:at + 3] + data[pos:pos + size]
        at, pos = at + 3 + size, pos + size
        if last:
            break
    out += frame[at:]
    assert len(out) == len(frame) and pos == len(data)
    return bytes(out)


@pytest.fixture(scope="module")
def cc(tmp_path_factory):
    return C.build_binaries(tmp_path_factory.mktemp("b3-bin"))


@pytest.fixture(scope="module")
def img(cc, tmp_path_factory):
    """partclone, 4.5 chunks: 0 and 2 incompressible."""
    return C.build(tmp_path_factory.mktemp("b3-img"), cc / "fanout", noisy=(0, 2))


def b3(cc: Path, mode: str, data: bytes) -> bytes:
    done = subprocess.run([str(cc / "chunkcast"), mode], input=data, capture_output=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def b3_list(cc: Path, img: C.Image, tmp: Path) -> tuple[Path, str]:
    """The image server's BLAKE3 list of `img` and its BLAKE3 -- what `<file>.b3` and `chunk_b3` hold."""
    listed = tmp / "p3.b3"
    listed.write_bytes(b3(cc, "b3list", img.data))
    return listed, b3(cc, "b3sum", listed.read_bytes()).decode().strip()


def b3_args(cc: Path, img: C.Image, tmp: Path) -> tuple[str, ...]:
    listed, digest = b3_list(cc, img, tmp)
    return ("--b3-file", str(listed), "--b3-file-hash", digest)


def forged_chunk(cc: Path, img: C.Image, k: int) -> bytes:
    """Chunk k's frame, with other bytes and the index's XXH64 -- checked here, so that only BLAKE3 is left to stop it."""
    real = img.data[k * C.CH:(k + 1) * C.CH]
    other = same_xxh64_other_bytes(real)
    assert other != real and C.xxh64(cc / "fanout", other) == C.xxh64(cc / "fanout", real)
    frame = same_frame_other_bytes(img.frame(k), other)
    assert len(frame) == img.zlens[k] and frame != img.frame(k)
    return frame


# --- the implementation ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("length,want", VECTORS)
def test_blake3_is_the_specs_on_its_own_test_vectors(cc, length, want):
    assert b3(cc, "b3sum", bytes(i % 251 for i in range(length))).decode() == want + "\n"


def test_the_list_is_the_blake3_of_every_4mib_and_the_short_last_one(cc, img):
    listed = b3(cc, "b3list", img.data)
    assert len(listed) == 32 * img.n
    for k in range(img.n):
        piece = img.data[k * C.CH:(k + 1) * C.CH]
        assert listed[32 * k:32 * k + 32].hex() == b3(cc, "b3sum", piece).decode().strip(), k
    assert b3(cc, "b3list", b"") == b""


# --- a chunk with the index's XXH64 and other bytes -----------------------------------------------------------------


def test_a_chunk_with_the_indexs_xxh64_and_other_bytes_is_not_written_by_apply(cc, img, tmp_path):
    """HTTP, a neighbour's fill, a neighbour cloner: all of them are `apply`. Chunk 2 passes XXH64 and fails BLAKE3:
    not a byte of it on the disk, the chunk before it stays, and apply says which check stopped it."""
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 1, 2, img.frame(1) + forged_chunk(cc, img, 2), tag=tmp_path / "apply",
                  extra=b3_args(cc, img, tmp_path))
    assert run.process.returncode == 8, run.log()
    assert "chunk 2 does not match the index (wrong BLAKE3) -- not written" in run.err.read_text(), run.log()
    assert C.state_of(state)[str(disk)]["durable"] == {1}
    assert disk.read_bytes() == img.expected({1})


def test_a_chunk_with_the_indexs_xxh64_and_other_bytes_is_not_written_from_the_multicast(cc, img, tmp_path):
    """The VLAN: the sender's file holds the forged frame for chunk 2. Every other chunk is written; 2 stays missing."""
    forged = tmp_path / "forged.pcl.zst"
    body = bytearray(img.file.read_bytes())
    body[img.offsets[2]:img.offsets[2] + img.zlens[2]] = forged_chunk(cc, img, 2)
    forged.write_bytes(body)
    disk, state, session, port = img.blank(tmp_path / "a.disk"), tmp_path / "state", C.new_session(), C.free_port()
    r = C.recv(cc, img, [disk], f"127.0.0.1:{port}", session, state, tag=tmp_path / "recv",
               extra=b3_args(cc, img, tmp_path))
    s = C.send(cc, img, f"127.0.0.1:{port}", session, tag=tmp_path / "send", file=forged)
    assert s.wait() == 0, s.log()
    assert r.wait() == 0, r.log()
    assert "chunk 2 does not match the index (wrong BLAKE3) -- not written" in r.err.read_text(), r.log()
    have = set(range(img.n)) - {2}
    assert C.state_of(state)[str(disk)]["durable"] == have
    assert disk.read_bytes() == img.expected(have)


def test_with_the_list_the_images_own_chunks_are_all_written(cc, img, tmp_path):
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 0, img.n, b"".join(img.frame(k) for k in range(img.n)),
                  tag=tmp_path / "apply", extra=b3_args(cc, img, tmp_path))
    assert run.process.returncode == 0, run.log()
    assert "checked against XXH64 alone" not in run.err.read_text()
    assert disk.read_bytes() == img.expected()


# --- the list itself ------------------------------------------------------------------------------------------------


def test_without_a_list_chunkcast_says_it_checks_xxh64_alone(cc, img, tmp_path):
    """The agent never calls it so (test_chunk_b3_agent_3332.py: the drawers fail first). A rig that does is told."""
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 1, 1, img.frame(1), tag=tmp_path / "apply")
    assert run.process.returncode == 0, run.log()
    assert "no BLAKE3 list (--b3-file): the chunks are checked against XXH64 alone" in run.err.read_text()


# --- the source of a direct send: chunkcut writes the list, the manifest names it ------------------------------------


@pytest.fixture(scope="module")
def chunkcut(tmp_path_factory) -> Path:
    binary = tmp_path_factory.mktemp("b3-cut") / "chunkcut"
    built = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread", "-o", str(binary),
                            str(C.REPO / "agent/chunkcut.c"), "-l:libzstd.so.1"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert built.returncode == 0, built.stderr
    return binary


def test_chunkcut_writes_the_blake3_list_of_its_chunks_beside_the_index(cc, chunkcut, tmp_path):
    data = random.Random(3332).randbytes(int(2.5 * C.CH))
    idx = tmp_path / "idx"
    cut = subprocess.run([str(chunkcut), "--raw", "-l", "3", "-T", "2", "-i", str(idx)], input=data, capture_output=True)
    assert cut.returncode == 0, cut.stderr
    assert (tmp_path / "idx.b3").read_bytes() == b3(cc, "b3list", data) and len(b3(cc, "b3list", data)) == 3 * 32


def test_the_manifest_names_the_list_chunkcut_wrote(cc, tmp_path):
    """`capture_index_json` (capturesink.sh): `chunk_b3` beside `index_xxh64` -- the BLAKE3 of `<index>.b3`."""
    (tmp_path / "idx").write_text("IMAGESRV-IDX 2 raw 4194304 0 0 - 0 0 0\n", newline="\n")
    (tmp_path / "idx.xxh64").write_text("0123456789abcdef\n", newline="\n")
    (tmp_path / "idx.b3").write_bytes(bytes(range(64)))
    digest = b3(cc, "b3sum", bytes(range(64))).decode().strip()
    out = sh(f'export PATH={posix(cc)!r}:"$PATH" LOG_FILE={posix(tmp_path / "log")!r}; '
             f'. {posix(C.REPO / "agent/lib/capture/capturesink.sh")}; capture_index_json {posix(tmp_path / "idx")!r}')
    assert out == f',"index_xxh64":"0123456789abcdef","chunk_b3":"{digest}"', out


@pytest.mark.parametrize("damage,said", [
    ("hash", "is not the manifest's BLAKE3 list -- nothing is believed"),
    ("short", "holds 128 bytes, the index has 5 chunks of 32 -- refused"),
    ("alone", "--b3-file and --b3-file-hash go together"),
    ("hex", "--b3-file-hash is not 64 lowercase hex digits"),
])
def test_a_list_that_is_not_the_manifests_is_refused_and_nothing_is_written(cc, img, tmp_path, damage, said):
    listed, digest = b3_list(cc, img, tmp_path)
    if damage == "hash":
        digest = b3(cc, "b3sum", b"another list").decode().strip()
    if damage == "short":
        listed.write_bytes(listed.read_bytes()[:-32])
        digest = b3(cc, "b3sum", listed.read_bytes()).decode().strip()
    extra = {"alone": ("--b3-file", str(listed)),
             "hex": ("--b3-file", str(listed), "--b3-file-hash", digest.upper())}.get(
        damage, ("--b3-file", str(listed), "--b3-file-hash", digest))
    disk, state = img.blank(tmp_path / "a.disk"), tmp_path / "state"
    run = C.apply(cc, img, [disk], state, 1, 1, img.frame(1), tag=tmp_path / "apply", extra=extra)
    assert run.process.returncode == 2, run.log()
    assert said in run.err.read_text(), run.log()
    assert disk.read_bytes() == img.expected(set())
