"""A partclone v2 image, built byte for byte (#1805) -- for the tests of the
read-back digest on both sides: the server's (server/agent/readbackhash.py) and the
agent's (`fanout --readback`).

The layout is partclone's own (src/partclone.h, src/main.c; server/library/usedbackfill.py
documents the header): image_desc_v2 (110 bytes, little-endian, CRC-32 seeded
0xFFFFFFFF with no final XOR over the first 106), the bit map -- one bit a block,
LSB first, (total + 7) // 8 bytes -- and its CRC-32, then the used blocks in
order with `checksum_size` bytes after every `blocks_per_checksum` of them and
one more for a remainder. These builders are independent of the code under
test: the expected digest is hashlib over the used blocks a test chose itself.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

HEAD = struct.Struct("<15sx14s4sH16sQQQQI")      # 88 bytes, then the options
OPTS = struct.Struct("<IHHHHIBB")                # 18 bytes: image_options_v2


def crc(data: bytes) -> int:
    return zlib.crc32(data) ^ 0xFFFFFFFF


def header(total: int, used: int, bs: int, *, checksum_mode: int = 0, checksum_size: int = 0,
           per_checksum: int = 0, bitmap_mode: int = 1, image_version: int = 2,
           version: bytes = b"0002", device_size: int | None = None) -> bytes:
    head = HEAD.pack(b"partclone-image", b"0.3.27", version, 0xC0DE, b"NTFS",
                     total * bs if device_size is None else device_size, total, used, used, bs)
    head += OPTS.pack(4, image_version, 64, checksum_mode, checksum_size, per_checksum, 1, bitmap_mode)
    return head + struct.pack("<I", crc(head))


def bitmap(total: int, used: set[int] | list[int]) -> bytes:
    bits = bytearray((total + 7) // 8)
    for i in used:
        bits[i >> 3] |= 1 << (i & 7)
    return bytes(bits)


def block(i: int, bs: int, salt: int = 0) -> bytes:
    """A block whose bytes say which block it is -- a block in the wrong place
    hashes differently from the right one."""
    seed = hashlib.sha256(f"{salt}:{i}".encode()).digest()
    return (seed * (bs // len(seed) + 1))[:bs]


def image(total: int, used: list[int], bs: int, *, checksum_size: int = 0, per_checksum: int = 0,
          salt: int = 0, **head) -> bytes:
    """header + bit map + its CRC + the used blocks, with partclone's checksums."""
    used = sorted(used)
    mode = 0x20 if checksum_size else 0
    bm = bitmap(total, used)
    out = [header(total, len(used), bs, checksum_mode=mode, checksum_size=checksum_size,
                  per_checksum=per_checksum, **head), bm, struct.pack("<I", crc(bm))]
    group = 0
    for i in used:
        out.append(block(i, bs, salt))
        group += 1
        if checksum_size and per_checksum and group == per_checksum:
            out.append(b"\xC5" * checksum_size)
            group = 0
    if checksum_size and per_checksum and group:
        out.append(b"\xC5" * checksum_size)
    return b"".join(out)


def device(total: int, used: list[int], bs: int, *, salt: int = 0, garbage: int = 0xEE) -> bytes:
    """The partition as `partclone -r` leaves it: the used blocks in place,
    whatever was there before everywhere else."""
    free = bytes([garbage]) * bs
    keep = set(used)
    return b"".join(block(i, bs, salt) if i in keep else free for i in range(total))


def expected(used: list[int], bs: int, salt: int = 0) -> tuple[str, int]:
    """What the disk must hash to: the used blocks, in order -- hashlib, not the code under test."""
    sha = hashlib.sha256()
    for i in sorted(used):
        sha.update(block(i, bs, salt))
    return sha.hexdigest(), len(used) * bs


CHUNK_BYTES = 4 * 1024 * 1024   # #1864: the read-back list cuts the data stream here


def stream(used: list[int], bs: int, salt: int = 0) -> bytes:
    """The data stream: the used blocks, in order -- what partclone -r writes."""
    return b"".join(block(i, bs, salt) for i in sorted(used))


def expected_list(data: bytes) -> tuple[str, int]:
    """#1864: what the list must be -- XXH64 (the public reference in
    test_fanout.py, not the code under test) of each 4MiB piece, a line each."""
    from test_fanout import xxh64_reference
    lines = [xxh64_reference(data[i:i + CHUNK_BYTES]) for i in range(0, len(data), CHUNK_BYTES)]
    return "".join(line + "\n" for line in lines), len(data)


def map_only(img: bytes, total: int) -> bytes:
    """What `partclone -c` has written by the time fanout --readback stops reading."""
    return img[:110 + (total + 7) // 8 + 4]
