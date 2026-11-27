"""The fetch count follows what each receiver has durable after the resend."""

import pytest

from test_chunksend_2533 import ALL, rig, wait_for  # noqa: F401 -- rig is a fixture


@pytest.mark.parametrize("c_missed_resend", [False, True], ids=["resend-received", "resend-missed"])
def test_single_receiver_missing_the_resend_fetches_one_more_chunk(rig, c_missed_resend):
    engine = rig.make()
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)

    b_first = {"B1": [[0, 0], [2, 2], [4, 9]]}
    c_first = {"C1": [[0, 2], [5, 9]]}
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", b_first)
    assert rig.ask("c", 1, c_first) == {"ok": True, "action": "receive", "pass": 2}
    assert rig.ask("b", 1, b_first) == {"ok": True, "action": "receive", "pass": 2}
    for mac, disks in (("b", b_first), ("c", c_first)):
        assert rig.tell(mac, 2, "listening", disks)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 2)

    c_second = c_first if c_missed_resend else {"C1": [[0, 3], [5, 9]]}
    rig.tell("b", 2, "ended", {"B1": [[0, 0], [2, 9]]})
    assert rig.ask("c", 2, c_second) == {
        "ok": True, "action": "fetch", "pass": 3, "ranges": [[3 if c_missed_resend else 4, 4]],
    }
    assert rig.ask("b", 2, {"B1": [[0, 0], [2, 9]]}) == {
        "ok": True, "action": "fetch", "pass": 3, "ranges": [[1, 1]],
    }
    rig.tell("b", 3, "ended", {"B1": ALL})
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")

    assert rig.runner.lists == [None, "3-3"]
    status = engine.status()
    assert status["multicast_chunks"] == 1
    assert status["fetched_chunks"] == (3 if c_missed_resend else 2)
