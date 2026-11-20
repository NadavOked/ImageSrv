"""An image_format 2 partition file, built byte for byte (#2533) -- for the tests of
the chunk format on the server's side (server/library/chunkformat.py).

A partition file is zstd frames that stand alone: the head (partclone's header,
bit map and its CRC; none in a raw partition) and then one frame for every 4MiB
of the data stream. The frames here are *stored* frames -- Raw_Blocks only, the
content size in the header, zstd's checksum (the low 32 bits of XXH64) at the
end -- written from RFC 8878, not by the code under test and not by libzstd:
`zstd -dc` reads them (test_chunk_format_2533.py checks that on the lab), and so
does `STORED_DC` below, the same reading in Python for a workstation without
zstd. The expected index is built here from the offsets this file chose and
from the public XXH64 reference (test_fanout.py) -- neither side vouches for
itself.

    python chunk_image.py [file]      decodes stored frames to stdout (STORED_DC)
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

CHUNK_BYTES = 4 * 1024 * 1024
MAGIC = b"\x28\xb5\x2f\xfd"
BLOCK_MAX = 128 * 1024


def _xxh(data: bytes) -> str:
    from test_fanout import xxh64_reference
    return xxh64_reference(data)


def stored_frame(data: bytes, *, checksum: bool = True, content_size: bool = True) -> bytes:
    """One zstd frame holding `data` in Raw_Blocks. Single segment when it says its
    content size (8 bytes); otherwise a window descriptor and no size at all."""
    if content_size:
        head = MAGIC + bytes([0xE0 | (0x04 if checksum else 0)]) + len(data).to_bytes(8, "little")
    else:
        head = MAGIC + bytes([0x04 if checksum else 0]) + bytes([0x70])      # a 16MiB window, no FCS
    blocks = []
    pieces = [data[i:i + BLOCK_MAX] for i in range(0, len(data), BLOCK_MAX)] or [b""]
    for i, piece in enumerate(pieces):
        last = 1 if i == len(pieces) - 1 else 0
        blocks.append(((len(piece) << 3) | last).to_bytes(3, "little") + piece)
    tail = bytes.fromhex(_xxh(data))[::-1][:4] if checksum else b""          # the low 32 bits, little-endian
    return head + b"".join(blocks) + tail


def split(stream: bytes, raw: bool) -> tuple[bytes, bytes]:
    """(the head, the data stream) of a partclone -a0 stream; a raw one has no head."""
    if raw:
        return b"", stream
    total = int.from_bytes(stream[60:68], "little")
    at = 110 + (total + 7) // 8 + 4
    return stream[:at], stream[at:]


def build(stream: bytes, raw: bool = False, *, damage=None) -> tuple[bytes, str]:
    """(the partition file, its index) for a partclone stream cut as chunkcut cuts it.
    `damage(k, chunk) -> bytes` changes what is stored for chunk k -- the index keeps
    the hash of the chunk as it was read, which is what a damaged file looks like."""
    head, data = split(stream, raw)
    out, rows = [], []
    head_frame = b"" if raw else stored_frame(head)
    at = len(head_frame)
    for k in range(0, len(data), CHUNK_BYTES):
        chunk = data[k:k + CHUNK_BYTES]
        frame = stored_frame(damage(k // CHUNK_BYTES, chunk) if damage else chunk)
        rows.append((at, len(frame), _xxh(chunk)))
        out.append(frame)
        at += len(frame)
    index = (f"IMAGESRV-IDX 2 {'raw' if raw else 'partclone'} {CHUNK_BYTES} {len(head)} {len(head_frame)}"
             f" {'-' if raw else _xxh(head)} {len(data)} {len(rows)} {at}\n")
    index += "".join(f"{o} {z} {x}\n" for o, z, x in rows)
    return head_frame + b"".join(out), index


def index_of(hashes: list[str], data_bytes: int, *, raw: bool = False):
    """An Index with these chunk hashes and frame sizes of its own -- for the tests
    that hand the library a derived index without a file behind it."""
    from server.library.chunkformat import Index
    rows, at = [], 0 if raw else 77
    for k, value in enumerate(hashes):
        rows.append((at, 100 + k, value))
        at += 100 + k
    return Index("raw" if raw else "partclone", 0 if raw else 1000, 0 if raw else 77,
                 None if raw else "0123456789abcdef", data_bytes, at, tuple(rows))


def place(folder, name: str, index) -> None:
    """A partition file as long as `index` says it is -- for the tests that hand the
    library a derived index: an index beside a file of another length is not read."""
    (folder / name).write_bytes(bytes(index.file_zbytes))


def signed(manifest: dict, indexes: dict[int, str]) -> dict:
    """The manifest with `index_xxh64` on the partitions named: {partition index: index text}."""
    out = copy.deepcopy(manifest)
    for part in out["partitions"]:
        if part["index"] in indexes:
            part["index_xxh64"] = _xxh(indexes[part["index"]].encode("ascii"))
    return out


def decode_stored(blob: bytes) -> bytes:
    """What zstd -dc gives for a file of stored frames. ValueError on anything else."""
    out, at = [], 0
    while at < len(blob):
        if blob[at:at + 4] != MAGIC:
            raise ValueError(f"no frame at {at}")
        fhd = blob[at + 4]
        single, checksum, fcs_flag = bool(fhd & 0x20), bool(fhd & 0x04), fhd >> 6
        at += 5 + (0 if single else 1)
        size = (1 if single else 0, 2, 4, 8)[fcs_flag]
        want = int.from_bytes(blob[at:at + size], "little") + (256 if fcs_flag == 1 else 0) if size else None
        at += size
        content = []
        while True:
            value = int.from_bytes(blob[at:at + 3], "little")
            if len(blob) < at + 3 or (value >> 1) & 3:
                raise ValueError("not a stored frame")
            content.append(blob[at + 3:at + 3 + (value >> 3)])
            if len(content[-1]) != value >> 3:
                raise ValueError("a block is cut short")
            at += 3 + (value >> 3)
            if value & 1:
                break
        data = b"".join(content)
        if want is not None and want != len(data):
            raise ValueError("the frame is not as long as it says")
        if checksum:
            if blob[at:at + 4] != bytes.fromhex(_xxh(data))[::-1][:4]:
                raise ValueError("checksum")
            at += 4
        out.append(data)
    return b"".join(out)


#: a decompressor for stored frames, in the two shapes the server calls zstd in:
#: `STORED_DC <file>` (readbackhash.digest_file) and `STORED_DC` on stdin (decode_frame).
STORED_DC = (sys.executable, str(Path(__file__).resolve()))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raw = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else sys.stdin.buffer.read()
    try:
        sys.stdout.buffer.write(decode_stored(raw))
    except ValueError as exc:
        print(f"chunk_image: {exc}", file=sys.stderr)
        raise SystemExit(1)
