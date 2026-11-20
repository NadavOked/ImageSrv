"""#2388, the server's side: the answer to `listening` says `held: true` while the sender
holds that pass for another machine's operator question (#2202) -- and only then. The
agent's side, and the whole story: tests/test_chunk_hold_2388.py.

Negative control: agent/lib/restore/chunkrecv.sh and server/rounds/chunksend.py back to the base -- see the PR.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.rounds import chunksend

from test_chunksend_2533 import rig, wait_for  # noqa: F401 -- `rig` is a fixture


def test_a_listener_is_told_the_pass_is_held_for_another_machines_question(rig, monkeypatch):
    """RED: the answer was `receive` and nothing else -- the machine that heard nothing could not
    tell a held wave from a dead sender, and failed at its own start ceiling."""
    monkeypatch.setattr(chunksend, "HELD_FRESH_S", 0.3, raising=False)   # raising=False: on the base the answer is what fails, not the patch
    asking = {"open": True}
    rig.make(pending_prompts=lambda sid: ["c"] if asking["open"] else [])
    assert wait_for(lambda: rig.tell("a", 1, "listening", {"A1": []}).get("held") is True)
    assert rig.tell("b", 1, "listening", {"B1": []})["held"] is True
    assert rig.runner.commands == []                                   # held: nothing on the air
    asking["open"] = False                                             # c's question is answered
    rig.tell("c", 1, "listening", {"C1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    # sending now: the hold is over, and a listen after it is not told otherwise
    assert wait_for(lambda: "held" not in rig.tell("a", 1, "listening", {"A1": []}), seconds=3)


def test_no_question_no_hold(rig):
    """Guard (passes in both states): no open question -- never `held`."""
    rig.make(pending_prompts=lambda sid: [])
    for mac in ("a", "b"):
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}) == {"ok": True, "action": "receive", "pass": 1}
