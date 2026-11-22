"""#3512 + #3511 + #3520: the in-wave feedback's `--rate-max` is 2 x the start, or the maximum K8 learned when that is higher.

`chunk_rate.write_ceiling` (what the drives of the partition were proven to write when they fell behind, x the margin) reaches
`chunkcast send` through `ChunkEngine.write_ceiling` -> `_learned_max` -> `feedback_bounds`. Nothing here invents a rate.
#3520 (metal 16/11, Zeus): the learned maximum only WIDENS the room (`max(2 x start, learned_max)`), it never narrows it to the
start's neighbourhood -- the live feedback protects the drive in the wave, the learned maximum decides where the wave starts. Two tests
of #3512 that pinned `max(start, learned_max)` are inverted here on purpose, not softened.

**Negative control:** `server/rounds/chunk_rate.py`, `server/rounds/chunksend.py` and `server/app.py` back at the base: `write_ceiling`
does not exist and `feedback_bounds` takes no learned maximum -- every test fails on behaviour, except the two marked (guard): a hook
that fails or answers nonsense is the 2 x fallback, in both states.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, chunksend, drive_rates
from server.rounds.chunksend import ChunkEngine, feedback_bounds

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import room_server                   # noqa: F401 -- shared fixture

START = 400000                                  # kbit/s


def _cmd(learned_max, kbps=100000, write_kbps=START):
    engine = SimpleNamespace(port=7713, interface=None, feedback=True, write_floor=lambda: 20)   # פורט-הפצה-מכוון
    wave = SimpleNamespace(key="0123456789abcdef", round=1)
    return ChunkEngine._command(engine, wave, {"index": 3}, Path("image.zst"), kbps, write_kbps, None, learned_max)


def _max(cmd):
    return int(cmd[cmd.index("--rate-max") + 1])


def test_a_learned_maximum_above_2x_the_start_is_the_rate_max():
    assert feedback_bounds(100000, START, 20, 900000) == (160000, 900000)
    assert _max(_cmd(900000)) == 900000


def test_a_learned_maximum_under_2x_does_not_narrow_the_room_of_the_feedback():
    """#3520, the metal: a ceiling of 1.25 x the start from one old wave held the feedback for four runs. The room is 2 x the start."""
    assert _max(_cmd(500000)) == 2 * START
    assert feedback_bounds(100000, START, 20, 500000)[1] == 2 * START


def test_a_learned_maximum_below_the_start_never_takes_the_max_under_2x_the_start():
    assert feedback_bounds(100000, START, 20, 300000)[1] == 2 * START


@pytest.mark.parametrize("none", [None, 0, -5])
def test_without_a_learned_maximum_the_fallback_is_2x_the_start(none):
    assert feedback_bounds(100000, START, 20, none)[1] == START * writerate.RATE_MAX_FACTOR
    assert _max(_cmd(none)) == 2 * START


def test_wire_only_ignores_the_learned_maximum():
    assert feedback_bounds(100000, 0, 20, 900000) == (100000, 100000)


# --- K8 ----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _behind(conn, sid, serial="S1", mbps=61):
    drive_rates.decide(conn, sid, lambda: [{"serial": serial, "model": "M", "mac": "aa:bb:cc:00:00:31", "port": 1}], None,
                       adjust=lambda chosen, drives: chunk_rate.adjust(conn, chosen, drives))
    chunk_rate.record(conn, sid, {"chunks": 5000, "bytes": 5000 * 4194304, "first_pass_s": 300.0, "lost_chunks": 0, "away": 0,
                                  "cloners": 1, "repair_rounds": 0, "ended": "send_done",
                                  "paced_ms": {"wire": 0, "disk": 222_000},         # #3530: the sender's sleep and the skipped chunks, as a real wave has them
                                  "behind": {serial: {"bytes": mbps * 300 * 1_000_000, "seconds": 300.0}}})
    skipped = 5000 - mbps * 300 * 1_000_000 // 4194304
    conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                 " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')",
                 (sid, 3, 1, "aa:bb:cc:00:00:31", "receive", skipped, json.dumps({serial: skipped})))
    conn.commit()


def test_write_ceiling_is_what_the_slowest_drive_was_proven_to_write_times_the_margin(conn):
    _behind(conn, "w0", "S1", 61)
    assert chunk_rate.write_history(conn, "S1")[0] == 61
    assert chunk_rate.write_ceiling(conn, ["S1"]) == int(61 * 8000 * writerate.MARGIN)
    assert chunk_rate.write_ceiling(conn, ["S1", "S9"]) == chunk_rate.write_ceiling(conn, ["S1"])    # S9 has no proof: not a bound


def test_write_ceiling_is_none_with_no_proof_or_with_the_learning_off(conn):
    assert chunk_rate.write_ceiling(conn, ["S1"]) is None
    _behind(conn, "w0")
    db.set_setting(conn, drive_rates.SETTING, "false")
    assert chunk_rate.write_ceiling(conn, ["S1"]) is None


def test_write_ceiling_never_raises():
    """(guard) the learning never stops a wave."""
    assert chunk_rate.write_ceiling(None, ["S1"]) is None


# --- the engine --------------------------------------------------------------------------------------------------


def _listen(rig):
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=120)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    return rig.runner.commands[0]


def _flag(cmd, name):
    return int(cmd[cmd.index(name) + 1])


def test_the_engine_asks_k8_for_the_drives_it_counted_and_sends_its_answer_as_the_rate_max(rig):
    asked = []
    engine = rig.make()
    engine.write_ceiling = lambda serials: (asked.append(serials), 2_000_000)[1]      # above 2 x the rig's 864000
    cmd = _listen(rig)
    assert asked == [["A1", "B1", "C1"]] and _flag(cmd, "--rate-max") == 2_000_000
    assert _flag(cmd, "--rate-max") != 2 * _flag(cmd, "--write-kbps")
    engine.stop()


@pytest.mark.parametrize("answer", [lambda s: 1 / 0, lambda s: "9", lambda s: 0, lambda s: True, lambda s: None])
def test_a_failing_or_nonsense_ceiling_is_the_2x_fallback(rig, answer):
    """(guard) a hook that fails or answers nonsense never stops a wave, and never changes the feedback's room."""
    engine = rig.make()
    engine.write_ceiling = answer
    cmd = _listen(rig)
    assert _flag(cmd, "--rate-max") == 2 * _flag(cmd, "--write-kbps")
    engine.stop()


def test_not_wired_is_the_2x_fallback_as_before(rig):
    engine = rig.make()
    cmd = _listen(rig)
    assert getattr(engine, "write_ceiling", None) is None
    assert _flag(cmd, "--rate-max") == 2 * _flag(cmd, "--write-kbps")
    engine.stop()


def test_the_server_wires_the_ceiling_into_its_chunk_engine(room_server):
    """(neg) `server.app` gives the engine `chunk_rate.write_ceiling`; no proof for a drive is None -- the fallback."""
    hook = getattr(room_server["ctx"].chunk, "write_ceiling", None)
    assert hook is not None
    assert hook(["S_NO_SUCH_DRIVE"]) is None
