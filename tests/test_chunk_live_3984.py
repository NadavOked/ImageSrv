"""A roster read failure must stop the wave before a repair or completion decision."""

from test_chunksend_2533 import SID, rig, wait_for


def test_failed_roster_read_cannot_complete_or_disconnect_receivers(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)

    def unreadable(sid):
        assert sid == SID
        raise OSError("roster database unavailable")

    engine.live_members = unreadable
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert "roster database unavailable" in engine.status()["error"]
    assert not engine._wave.away
    assert not rig.named("send_done")


def test_empty_roster_is_still_a_valid_read(rig):
    engine = rig.make()
    rig.members = []
    assert engine._live(engine._wave) == []
