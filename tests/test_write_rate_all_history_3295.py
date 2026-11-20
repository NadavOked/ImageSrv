"""#3295 (metal 18/11, R1): a drive that fell behind ONCE is not a slow drive -- it does not bring the whole wave down to the floor.

R1 (cloner 2, LabRoot `1ecf662d7`, `lab/metal-1118-m/runs/R1`): an APS-SL3N-512 (check 209MB/s, wrote 48MB/s) and a Samsung MZ7LN256
(check 209MB/s) in one wave. Both had a `write_history` -- the APS 48MB/s, the Samsung 248MB/s from an old wave in which it skipped
a hiccup over `BEHIND_PERMILLE` (`journal.txt`: `chunk_write_behind ... wrote=248MB/s ... counted:all_behind`). `ChunkEngine._write_base`
left out every drive with a history, nobody was left, and the fallback "everyone was behind: the slowest sets the pace" counted BOTH:
`chunk_rate._ceiling` took the APS's 48MB/s, K8 cut `--write-kbps` to `CUT other_drives+sustained` -> the floor (74MB/s), and the
Samsung wrote 84MB/s instead of ~250 -- the wave took 1255 s, the Samsung alone ~319 s. Nadav 15/11 on #3295: "the stream goes at the
pace of the fast drives; the slow one misses chunks and completes them later."

The fix (`ChunkEngine._slow_by_history`): when every drive has a history, a drive whose ceiling (what it wrote x `WRITE_MARGIN`) is at or
above the pace of the slowest check keeps up with it and counts; only the drives under it are left out. When none is above it, all are
counted, as before (#3526: the real cut when everyone is behind stays). A drive WITHOUT a history still counts (nothing changed there).
The numbers are R1's (`CHECK`, the two `wrote` figures), the floor is the rig's 50MB/s (R1: 74).

**Negative control:** with `server/rounds/chunksend.py` back at the base, the tests marked (neg) fail on behaviour: `--write-kbps` is the
floor (400000), both drives are in the `chunk_write_behind` lines, and the Samsung's check is not alone in the start rate. The tests marked
(guard) pass in both states.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, chunksend

from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_write_rate_3295 import CHECK, SECONDS, TODAY, _earlier

APS, SAMSUNG = "B1", "A1"                                  # the rig's serials: `mac.upper() + "1"`
WROTE_APS, WROTE_SAMSUNG = 48, 248                         # MB/s, `journal.txt` of R1: `wrote=48MB/s`, `wrote=248MB/s`
FLOOR_KBPS = 50 * 8000                                     # the rig's `write_floor` (R1: 74MB/s)


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _behind(mbps: int) -> dict:
    """`rate_outcome.behind[serial]` of a drive that made `mbps` MB/s durable in a 306 s pass."""
    return {"bytes": mbps * 10 ** 6 * int(SECONDS), "seconds": SECONDS}


def _history(conn, aps: int | None = WROTE_APS, samsung: int | None = WROTE_SAMSUNG) -> None:
    """The past waves of R1: the Samsung's old one, then the APS's (None = that drive has no history)."""
    if samsung is not None:
        _earlier(conn, "ses_samsung", {SAMSUNG: _behind(samsung)})
    if aps is not None:
        _earlier(conn, "ses_aps", {APS: _behind(aps)})


def _wave(rig, conn, checks=None):
    """A wave of the rig with the two hooks `server.app` wires -- `write_history` and K8's `write_rate` -- and the start-rate hook.
    Returns (`--write-kbps` of its first pass, the write checks `wave_start` was given)."""
    checks = checks or {"a": CHECK, "b": CHECK}
    rig.members = ["a", "b"]
    seen: list[dict] = []
    engine = rig.make()
    engine.write_history = lambda serial: chunk_rate.write_history(conn, serial)
    engine.write_rate = lambda sid, part, base, serials: chunk_rate.write_rate(conn, sid, part, base, serials)
    engine.wave_start = lambda sid, bits, probes: seen.append(dict(probes)) or bits
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=checks[mac])["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    engine.stop()
    return int(cmd[cmd.index("--write-kbps") + 1]), seen[0]


def _k8(conn) -> list:
    return [json.loads(r["evidence_json"])[0] | {"verdict": r["verdict"], "reason": r["reason"]}
            for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = 'K8' AND action = 'WAVE_WRITE_RATE' ORDER BY id")]


def test_r1_a_drive_that_kept_up_with_the_pace_is_not_cut_down_to_the_apss_ceiling(rig, conn):
    """(neg) R1: both drives have a history; the Samsung's (248MB/s) is above the pace (188MB/s), the APS's (48) is not. The wave goes at the
    Samsung's pace -- 1504000 -- and K8 does not cut it to the APS's 48MB/s and then to the floor."""
    _history(conn)
    got, _probes = _wave(rig, conn)
    assert got == TODAY == 1504000, got
    assert got > FLOOR_KBPS
    [row] = _k8(conn)[:1]
    assert row["verdict"] == "PROBE" and "sustained" not in row["reason"], row
    assert row["serials"] == [SAMSUNG], row["serials"]                       # K8 counted the Samsung alone: the APS is left out


def test_r1_only_the_slow_drive_is_journaled_as_left_out(rig, conn):
    """(neg) `chunk_write_behind` names the APS, `left_out` -- and not the Samsung (R1: both, `counted:all_behind`)."""
    _history(conn)
    _wave(rig, conn)
    assert rig.named("chunk_write_behind") == [f'{SID} "{APS}" check={CHECK}MB/s wrote={WROTE_APS}MB/s in=ses_aps left_out']


def test_r1_the_slow_drives_check_does_not_set_the_start_rate_either(rig, conn):
    """(neg) `_start_rate` follows `_write_kbps`: the APS's (lower) check is not a candidate for `--kbps` -- the Samsung's is."""
    _history(conn)
    _got, probes = _wave(rig, conn, checks={"a": CHECK, "b": 120})
    assert probes == {("a", SAMSUNG): CHECK}, probes


def test_when_every_drive_is_really_behind_the_slowest_still_sets_the_pace_and_k8_cuts(rig, conn):
    """(guard, both states) #3526: both histories are under the pace -- nobody keeps up, everyone counts, K8 cuts to the slowest's ceiling."""
    _history(conn, aps=WROTE_APS, samsung=61)
    got, probes = _wave(rig, conn)
    assert got == max(FLOOR_KBPS, int(WROTE_APS * 8000 * writerate.MARGIN)) == FLOOR_KBPS
    assert probes == {("a", SAMSUNG): CHECK, ("b", APS): CHECK}
    assert _k8(conn)[0]["verdict"] == "CUT"
    assert [l.endswith("counted:all_behind") for l in rig.named("chunk_write_behind")] == [True, True]


def test_a_drive_with_no_history_still_sets_the_pace_and_a_drive_with_a_history_is_left_out(rig, conn):
    """(guard, both states) Nadav 15/11: the APS has a history, the Samsung has none -- the Samsung's check sets the pace, the APS is out."""
    _history(conn, samsung=None)
    got, probes = _wave(rig, conn)
    assert got == TODAY and probes == {("a", SAMSUNG): CHECK}
    assert rig.named("chunk_write_behind") == [f'{SID} "{APS}" check={CHECK}MB/s wrote={WROTE_APS}MB/s in=ses_aps left_out']


def test_with_no_history_at_all_every_drive_counts(rig, conn):
    """(guard, both states) no past wave: the slowest check, as before."""
    got, probes = _wave(rig, conn, checks={"a": 400, "b": CHECK})
    assert got == TODAY and probes == {("a", SAMSUNG): 400, ("b", APS): CHECK}
    assert rig.named("chunk_write_behind") == []
