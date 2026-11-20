"""#3269: the chunk engine asks where the wave starts once the drawers of the first partition listened -- only then is
the write check of the wave known (`ChunkEngine.wave_start`, wired to `chunk_rate.start` in `server.app`) -- and the
first `chunkcast send` goes out at the answer. A failure, or an answer that is not a positive number, keeps the rate
`wave_bitrate` chose.

The engine runs with the fake `chunkcast` of `test_chunksend_2533`. Nothing here sleeps: every wait is on a condition.

**Negative control:** `server/rounds/chunksend.py` back to the base -- the first test fails (the pass goes out at
700000 kbps and the hook is never asked).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 -- rig is a fixture


def _first_kbps(rig) -> str:
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=180)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    return cmd[cmd.index("--kbps") + 1]


def test_the_first_pass_goes_out_at_the_start_the_write_check_gave(rig):
    engine = rig.make()
    asked = []
    engine.wave_start = lambda sid, bits, probes: asked.append((sid, bits, probes)) or 900_000_000
    assert _first_kbps(rig) == "900000"
    (sid, bits, probes), = asked
    assert sid == SID and bits == 700_000_000
    assert probes == {(mac, mac.upper() + "1"): 180 for mac in rig.members}
    engine.stop(SID)


@pytest.mark.parametrize("answer", [RuntimeError("boom"), None, 0, -5, True, "900m"])
def test_a_failed_or_strange_answer_keeps_the_chosen_rate(rig, answer):
    engine = rig.make()

    def hook(_sid, _bits, _probes):
        if isinstance(answer, Exception):
            raise answer
        return answer
    engine.wave_start = hook
    assert _first_kbps(rig) == "700000"
    engine.stop(SID)
