#!/usr/bin/env python3
"""List every path in a decompressed initramfs read from stdin, one per line (#1630).

`cpio -t` stops at the first TRAILER!!!. The kernel does not: init/initramfs.c
goes on to the next header after NUL padding, so an archive appended to the
image (an overlay concatenated at the end) is unpacked -- and was invisible to
initrd-lab-gate.sh. This reads newc entries the way the kernel does, to the end
of the stream: a header ("070701"/"070702") at a 4-byte boundary, the name up to
its first NUL, the body, padding; NUL bytes between entries are skipped.

Exit 0 only when the whole stream was read and held at least one entry. Anything
else -- truncation, a byte that is neither a header nor NUL padding, a name with
a newline (it would split the listing) -- is exit 1, never "end of listing"
(principle 5).
"""

from __future__ import annotations

import sys

HEADER = 110
MAGICS = (b"070701", b"070702")
CHUNK = 1 << 20


class Bad(Exception):
    pass


def align4(n: int) -> int:
    return (n + 3) & ~3


def main() -> int:
    src = sys.stdin.buffer
    out = sys.stdout.buffer
    pos = 0
    entries = 0

    def read(n: int, what: str) -> bytes:
        nonlocal pos
        buf = src.read(n)
        if len(buf) != n:
            raise Bad(f"truncated {what} at offset {pos}")
        pos += n
        return buf

    def skip(n: int, what: str) -> None:
        while n:
            n -= len(read(min(n, CHUNK), what))

    while True:
        first = src.read(1)
        if not first:
            break
        pos += 1
        if first == b"\0":
            continue
        start = pos - 1
        if start & 3:
            raise Bad(f"data at offset {start} is not on a 4-byte header boundary")
        header = first + read(HEADER - 1, "header")
        if header[:6] not in MAGICS:
            raise Bad(f"no newc header at offset {start} (magic {header[:6]!r})")
        try:
            fields = [int(header[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        except ValueError:
            raise Bad(f"header at offset {start} has a non-hex field") from None
        filesize, namesize = fields[6], fields[11]
        raw = read(namesize, "name")
        skip(align4(pos) - pos, "name padding")
        name = raw.split(b"\0", 1)[0]
        if b"\n" in name:
            raise Bad(f"entry at offset {start} has a newline in its name")
        if name and name != b"TRAILER!!!":
            out.write(name + b"\n")
        entries += 1
        skip(filesize, "body")
        skip(align4(pos) - pos, "body padding")
    if not entries:
        raise Bad("no cpio entries in the stream")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Bad as err:
        print(f"initrd-list: {err}", file=sys.stderr)
        sys.exit(1)
