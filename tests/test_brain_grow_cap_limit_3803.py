"""#3803: `--rate-cap` that the brain decides (`writerate.grow_cap`, S4) never exceeds what `chunkcast` accepts.

`grow_cap` multiplies the wire rate by "uncompressed bytes of the partition / bytes of its file". A partition whose file is tiny gave 434,793,089
(a 5KiB ESP, one chunk) and `chunkcast send` refused it with exit 2 (`cc_num(v, 1, 100000000)`), so the wave failed because of arithmetic.

**Negative control:** `server/brain/writerate.py` back at the base (`27a8a78f4`): `grow_cap` returns 434,793,089, `CHUNKCAST_RATE_LIMIT` does not
exist, and `chunkcast send` exits 2 with "bad option '--rate-cap ...'".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import chunkcast_rig as C
from native import requires_native
from server.brain import writerate

REPO = Path(__file__).resolve().parent.parent
CHUNK = 4 * 1024 * 1024
KBPS = 800_000                  # --kbps of the incident's order of magnitude: 100MB/s on the wire
FILE = 5 * 1024                 # the 5KiB ESP of the simulation (#3546 step 9c): one 4MiB chunk


def c_limit(option: str) -> int:
    """The upper bound chunkcast.c puts on `--<option>`: `cc_num(v, 1, N, ...)`."""
    m = re.search(r'"%s"\)\) bad = cc_num\(v, \d+, (\d+),' % re.escape(option), (REPO / "agent/chunkcast.c").read_text(encoding="utf-8"))
    assert m, f"chunkcast.c has no line for --{option}"
    return int(m.group(1))


def test_the_limit_is_the_one_in_chunkcast_c():
    """guard: the Python copy follows the C source for all three flags the brain writes."""
    assert {c_limit(o) for o in ("rate-min", "rate-max", "rate-cap")} == {writerate.CHUNKCAST_RATE_LIMIT}


@pytest.mark.parametrize("file_bytes", [1, 512, FILE, 41 * 1024, 1 << 20, 1 << 30])
def test_grow_cap_stays_within_chunkcast_range(file_bytes):
    cap = writerate.grow_cap(KBPS, None, file_bytes, 1, CHUNK)
    assert cap is not None and 1 <= cap <= c_limit("rate-cap")


def test_extreme_ratio_is_clamped_not_dropped():
    assert writerate.grow_cap(KBPS, None, FILE, 1, CHUNK) == writerate.CHUNKCAST_RATE_LIMIT        # old code: 655,360,000
    assert writerate.grow_cap(KBPS, None, 1 << 30, 1, CHUNK) < writerate.CHUNKCAST_RATE_LIMIT      # a normal ratio is untouched


def test_decision_carries_the_clamp_in_its_evidence():
    call = writerate.judge_bounds(KBPS, KBPS, 50, None, file_bytes=FILE, chunks=1, full_pass=True, chunk_bytes=CHUNK, round_id="r")
    assert call.cap == writerate.CHUNKCAST_RATE_LIMIT
    assert f"--rate-cap {call.cap}" in call.reason and f"cap_limit={writerate.CHUNKCAST_RATE_LIMIT}" in call.evidence
    normal = writerate.judge_bounds(KBPS, KBPS, 50, None, file_bytes=1 << 30, chunks=1, full_pass=True, chunk_bytes=CHUNK, round_id="r")
    assert not any(e.startswith("cap_limit") for e in normal.evidence)


@requires_native("gcc", "zstd", posix=True, why="a real chunkcast send")
def test_chunkcast_send_accepts_the_decided_flags(tmp_path):
    (tmp_path / "bin").mkdir()   # build_binaries writes into an existing directory
    cc = C.build_binaries(tmp_path / "bin")
    img = C.build(tmp_path / "img", cc / "fanout", kind="raw", chunks=1, noisy=())
    call = writerate.judge_bounds(KBPS, KBPS, 50, None, file_bytes=img.file.stat().st_size, chunks=1, full_pass=True,
                                  chunk_bytes=CHUNK, round_id="r")
    assert call.cap is not None, "the file is tiny: the decision must still carry a cap"
    run = C.send(cc, img, f"127.0.0.1:{C.free_port()}", C.new_session(), tag=tmp_path / "send", write_kbps=KBPS,
                 extra=("--rate-min", str(call.low), "--rate-max", str(call.high), "--rate-cap", str(call.cap)))
    assert run.wait() == 0, run.log()
    assert "bad option" not in run.log()
