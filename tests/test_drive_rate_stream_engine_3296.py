"""#3296: what the chunk engine says each drive took from the first pass (`ChunkEngine.rate_outcome.drives`).

The drive memory (`drive_rates.record_wave`) learns from this number, and the counter it is compared with
(`bytes_written`) is in compressed bytes -- so the engine counts the index's frame lengths, not chunks. The image here
has frames of different sizes (chunk k is k+1 bytes), so a count of chunks and a sum of frames do not agree.
A fake `chunkcast`, as `test_chunksend_2533`; every wait is on a condition.

**Negative control:** `server/rounds/chunksend.py` back to the base -- `rate_outcome` has no `drives` and no
`stream_zbytes` (KeyError).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_chunk_rate_engine_3163 import _listening
from test_chunksend_2533 import SID, Lib, rig  # noqa: F401 -- rig is a fixture

SIZES = list(range(1, 11))                 # chunk k: a frame of k+1 bytes -> 55 bytes in all


class SizedLib(Lib):
    """The image of `Lib` (partition 3, ten chunks), with frames of different sizes."""

    def __init__(self, root):
        super().__init__(root, chunks=len(SIZES))
        (root / "p3.windows.pcl.zst").write_bytes(b"z" * sum(SIZES))
        offsets = [sum(SIZES[:k]) for k in range(len(SIZES))]
        (root / "p3.windows.pcl.zst.idx").write_text(
            f"IMAGESRV-IDX 2 raw 4194304 0 0 - {len(SIZES) * 4194304} {len(SIZES)} {sum(SIZES)}\n"
            + "".join(f"{offsets[k]} {SIZES[k]} {k:016x}\n" for k in range(len(SIZES))), encoding="ascii")


def test_each_drive_is_told_in_the_compressed_bytes_it_had_when_the_first_pass_ended(rig, tmp_path):
    engine = rig.make(lib=SizedLib(tmp_path))
    _listening(rig)
    rig.tell("a", 1, "ended", {"A1": [[0, 9]]})
    rig.tell("b", 1, "ended", {"B1": [[0, 4]]})                                # chunks 5-9 come later, not in the pass
    rig.ask("c", 1, {"C1": [[5, 9]]})                                          # an answer: the first pass is planned
    got = engine.rate_outcome(SID)
    assert got["stream_zbytes"] == 55
    assert got["drives"] == {"a": {"A1": 55}, "b": {"B1": 1 + 2 + 3 + 4 + 5}, "c": {"C1": 6 + 7 + 8 + 9 + 10}}
