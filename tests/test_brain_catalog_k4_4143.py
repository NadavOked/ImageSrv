"""#4143: K4 (Integrity) points at the place where the source-failure decision of the chunk engine is actually taken."""

import tomllib
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / "server" / "brain" / "catalog.toml"


def test_k4_also_points_at_the_chunksend_source_failure_decision():
    k4 = next(i for i in tomllib.loads(CATALOG.read_text(encoding="utf-8"))["item"] if i["id"] == "K4")
    also = {(a["file"], a["symbol"]) for a in k4.get("also", [])}
    assert ("server/rounds/chunksend.py", "_send") in also
    assert ("server/rounds/chunkpass.py", "SOURCE_FAILURE_PREFIX") in also
    assert (k4["mode"], k4["brain_state"]) == ("shadow", "shadow")      # the old code decides, the brain records
