"""A receiver that has not reported `ended` yet may hold the chunk the others rejected: no source verdict."""

from test_chunk_source_3933 import _ended
from test_chunksend_2533 import SID, rig, wait_for  # noqa: F401  (rig is a fixture)


def test_straggler_holding_the_chunk_blocks_the_source_verdict(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    # c is live and already told the server it holds every chunk (chunk 5 included); its `ended` has not arrived yet
    rig.tell("c", 1, "listening", {"c": [[0, 9]]})
    for mac in ("a", "b"):
        _ended(rig, mac, 1, [[0, 4], [6, 9]], [5])           # both rejected chunk 5 against the index
    assert wait_for(lambda: engine.status()["state"] in ("failed", "repairing"))
    assert "SOURCE_FAILURE" not in (engine.status()["error"] or ""), engine.status()["error"]
    assert engine.status()["state"] == "repairing"
