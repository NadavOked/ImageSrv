"""#3852: the stream-eviction ceiling quoted in prose must be the one in the code.

The code says 120000 ms (`CC_WRITE_STALL_MS` in agent/chunkcast.c, `STALL_EVICT_MS` in
agent/fanout.c). Three files still told the next reader that the decisions map writes 150000
and the code 120000 -- a stale claim that sends someone building a brain rule on a number
that is not in the code (#3653). The number is read from the C sources, not hard-coded here,
so a later change to the code makes the prose fail loudly instead of drifting silently.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROSE = (
    "docs/architecture/interfaces.md",
    "server/brain/catalog.toml",
    "server/brain/drawertune.py",
)
OLD_WRONG_VALUE = "150000"


def _define(path: str, name: str) -> str:
    text = (REPO / path).read_text(encoding="utf-8")
    m = re.search(rf"^#define\s+{name}\s+(\d+)\b", text, re.M)
    assert m, f"{path}: no #define {name}"
    return m.group(1)


def test_both_c_sources_agree_on_the_ceiling() -> None:
    assert _define("agent/chunkcast.c", "CC_WRITE_STALL_MS") == _define("agent/fanout.c", "STALL_EVICT_MS")


def test_prose_does_not_cite_the_wrong_ceiling() -> None:
    for path in PROSE:
        text = (REPO / path).read_text(encoding="utf-8")
        assert OLD_WRONG_VALUE not in text, f"{path} still cites {OLD_WRONG_VALUE} as the eviction ceiling"


def test_prose_cites_the_value_that_is_in_the_code() -> None:
    value = _define("agent/fanout.c", "STALL_EVICT_MS")
    for path in PROSE:
        text = (REPO / path).read_text(encoding="utf-8")
        assert value in text, f"{path} does not cite the code's eviction ceiling {value}"
