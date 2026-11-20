import json

import pytest

from test_chunksend_2533 import ALL, SID, rig, wait_for  # noqa: F401 -- pytest fixture

from server import db
from server.brain import fecrule


def report(mask):
    return dict(k=32, chunks=1, packets=40, longest=mask.bit_count(), lost=mask.bit_count(),
                groups=int(bool(mask)), masks=[[0, 0, mask]] if mask else [])


@pytest.mark.parametrize("a,b,shared,private,method,count,timing", [
    (0, 0, 0, 0, "none", 0, "on-demand"),
    (1, 2, 0, 2, "xor", 1, "on-demand"),
    (1, 1, 1, 0, "xor", 2, "proactive"),
    (3, 2, 1, 1, "reed-solomon", 4, "proactive"),
])
def test_wave_records_identity_intersection_and_the_hypothetical_policy(
        tmp_path, a, b, shared, private, method, count, timing):
    evidence = fecrule.partition([report(a), report(b)], [40 * 1440])
    assert (evidence["shared"], evidence["private"]) == (shared, private)
    conn = db.connect(tmp_path / "db")
    fecrule.record(conn, "wave", {3: evidence})
    row = conn.execute("SELECT * FROM brain_decisions WHERE rule='K-FEC'").fetchone()
    assert (row["mode"], row["outcome"], row["round_id"]) == ("shadow", "n/a: shadow", "wave")
    decision, = json.loads(row["evidence_json"])
    assert decision == dict(method=method, packets=count, timing=timing, partitions={"3": evidence})


@pytest.mark.parametrize("other", [None, {}, dict(report(1), chunks=0), dict(report(1), groups=129),
                                   dict(report(1), masks=[[0, 1, 1 << 8]])])
def test_missing_or_partial_evidence_is_unknown(other):
    evidence = fecrule.partition([report(1), other], [40 * 1440])
    assert "shared" not in evidence and evidence["measured"] == 1
    assert fecrule.judge([evidence]) == dict(method="unmeasured", packets=None, timing=None)


def test_normal_wave_records_one_shadow_row_without_enabling_the_sender(rig, tmp_path):
    # #3870: the rig's 1 s start_timeout is for the tests of a wave nobody joins. Here it counted `db.connect` (a whole
    # schema) and the three tells under a loaded host, and the wave failed "no machine settled within 1 seconds" before
    # anything this test checks (1120-052839-argus-r44d2-bt4-9828, testrunner2). The wait ends as soon as all three listen.
    conn = db.connect(tmp_path / "brain.db")
    engine = rig.make(start_timeout=60)
    engine.brain_conn = conn
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper(): []})
    assert wait_for(lambda: len(rig.runner.commands) == 1, 60)
    for mac in rig.members:
        shape = dict(report(1), packets=10, chunks=10)
        engine.report(SID, mac, {"part": 3, "pass": 1, "state": "ended", "loss_shape": shape,
                                 "targets": [{"serial": mac.upper(), "durable": [{"part": 3, "ranges": ALL}]}]})
    query = "SELECT * FROM brain_decisions WHERE round_id=? AND rule='K-FEC'"
    assert wait_for(lambda: conn.execute(query, (SID,)).fetchone() is not None, 60)
    row, = conn.execute(query, (SID,)).fetchall()
    decision, = json.loads(row["evidence_json"])
    assert (decision["method"], decision["timing"], decision["packets"]) == ("xor", "proactive", 10)
    assert all("--fec" not in command for command in rig.runner.commands)
