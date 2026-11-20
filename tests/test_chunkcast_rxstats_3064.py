"""#3064 (S41): `chunkcast recv` says every counter of the pass on stdout, once, before its verdict.

Until now only `abandoned` and `stray` reached the agent; the rest was a log line after the pass. The agent
(`rxsense.sh`) adds the passes up; here the real binary is run, on loopback, as in `test_chunkcast_2533.py`.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

from native import requires_native
from test_chunkcast_2533 import _clean, cc, img   # noqa: F401 -- cc and img are fixtures: the real chunkcast and its image

pytestmark = requires_native("gcc", "zstd", posix=True, why="chunkcast צריך gcc, libzstd ו-zstd")

KEYS = {"pkts", "foreign", "stray", "bad_pkt", "dup", "evicted", "busy", "bad_chunk", "good", "skipped", "conflict", "version"}


def test_a_clean_pass_says_all_twelve_counters_once_before_its_last_word(cc, img, tmp_path):
    a = img.blank(tmp_path / "a.disk")
    r, _s, _state = _clean(cc, img, tmp_path, [a])
    assert r.wait() == 0, r.log()
    out = r.out.read_text().splitlines()
    said = [line for line in out if line.startswith("rxstats ")]
    assert len(said) == 1, out
    counters = dict(pair.split("=", 1) for pair in said[0].split()[1:])
    assert set(counters) == KEYS, counters
    assert counters["stray"] == "-", "no --from: the packets of another source are not counted, so not 0"
    assert int(counters["good"]) == img.n and int(counters["pkts"]) > 0, counters
    assert all(counters[k] == "0" for k in ("foreign", "bad_pkt", "bad_chunk", "busy", "conflict", "version")), counters
    assert out[-1] == "pass complete" and out.index(said[0]) < len(out) - 1, out
