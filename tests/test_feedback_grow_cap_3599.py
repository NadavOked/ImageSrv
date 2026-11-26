"""#3599: what the engine hands `chunkcast send --rate-cap`, the most the feedback's `--rate-max` may grow to.

The cap is the wire ceiling (`--kbps`, compressed) as a rate of uncompressed data -- this partition's chunks x 4MiB over its file's bytes: the
network capacity that was measured. A maximum K8 learned for the drives of the wave is the most they were proven to write: no growth then.
A repair pass is short: no growth. J1 numbers (17/11): `--kbps 892000`, 18,441 chunks, 18,545,444,861 compressed bytes, `--rate-max` 1,792,000.

**Negative control:** `server/rounds/chunksend.py` back at the base: `feedback_grow_cap` does not exist, and the command has no `--rate-cap`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server.rounds import chunksend
from server.rounds.chunksend import ChunkEngine, feedback_bounds, feedback_grow_cap

KBPS, WRITE, FLOOR = 892_000, 896_000, 74
BIG_FILE = 100 << 20     # #4003: a test file bigger than this is a real write on Windows
CHUNKS, ZBYTES = 18_441, 18_545_444_861


def test_the_cap_is_the_wire_ceiling_as_a_rate_of_uncompressed_data():
    cap = feedback_grow_cap(KBPS, None, ZBYTES, CHUNKS)
    assert cap == KBPS * CHUNKS * chunksend.CHUNK_BYTES // ZBYTES == 3_720_248          # ~465MB/s of data on a 1G wire at the J1 ratio
    assert cap > feedback_bounds(KBPS, WRITE, FLOOR)[1] == 1_792_000


@pytest.mark.parametrize("learned, size, chunks, kbps", [(1_000_000, ZBYTES, CHUNKS, KBPS), (None, None, CHUNKS, KBPS),
                                                          (None, ZBYTES, None, KBPS), (None, 0, CHUNKS, KBPS), (None, ZBYTES, CHUNKS, 0)])
def test_without_what_to_convert_by_or_with_a_learned_maximum_there_is_no_growth(learned, size, chunks, kbps):
    assert feedback_grow_cap(kbps, learned, size, chunks) is None


def _cmd(tmp_path, monkeypatch, *, learned_max=None, chunks=None, part_chunks=CHUNKS, size=ZBYTES):
    # #4003: the engine only needs the file's size; truncate() to 18GB is a real 18GB write on Windows (NTFS is not sparse by default)
    path = tmp_path / "p3.zst"
    monkeypatch.setattr(chunksend, "_size_of", lambda p: size)
    engine = SimpleNamespace(port=7713, interface=None, source=None, feedback=True, write_floor=lambda: FLOOR)   # פורט-הפצה-מכוון
    wave = SimpleNamespace(key="0123456789abcdef", round=1, part_chunks={3: part_chunks})
    cmd = ChunkEngine._command(engine, wave, {"index": 3}, path, KBPS, WRITE, chunks, learned_max)
    # #4003 guard: no test file of this size may exist (a real write on Windows)
    assert not [q for q in tmp_path.rglob("*") if q.is_file() and q.stat().st_size > BIG_FILE]
    return cmd


def _flag(cmd, name):
    return int(cmd[cmd.index(name) + 1]) if name in cmd else None


def test_a_full_pass_carries_the_cap(tmp_path, monkeypatch):
    cmd = _cmd(tmp_path, monkeypatch)
    assert _flag(cmd, "--rate-max") == 1_792_000 and _flag(cmd, "--rate-cap") == 3_720_248


def test_a_repair_pass_a_learned_maximum_or_a_missing_file_carries_none(tmp_path, monkeypatch):
    assert _flag(_cmd(tmp_path, monkeypatch, chunks=[(5, 9)]), "--rate-cap") is None
    assert _flag(_cmd(tmp_path, monkeypatch, learned_max=1_000_000), "--rate-cap") is None
    assert _flag(_cmd(tmp_path / "nothing-here", monkeypatch, size=None), "--rate-cap") is None
