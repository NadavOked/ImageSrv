"""#3605 (Nadav 17/11, option 1): a drive the brain marked slow is held -- it completes in the background and is NOT failed -- so the fast
drive next to it, and everybody else, do not wait for it.

J1 (metal 17/11, `room_b2ddf7cd`): machine 2 had a fast Samsung with 2 chunks missing (`chunk_net_gap`) and an APS with 13,056 missing, in ONE
HTTP queue. The Samsung held the whole partition at 17:30:10 and still waited until the APS finished at 17:45:54: ~15.7 minutes idle. #2023 built
the way out (`hold`: the drawer completes in the background, the others go on) and left it behind `brain_drawer_hold`, off. The decision:
`brain_drawer_hold` has a mode of its own, `slow` (the default): the brain's own mark -- K3 `DRAWER_HOLD` = DROP, "completing is slower than
writing it again", twice in a row -- is executed, but as a HOLD and not as the failure `on` makes of it ("no disk is disqualified", 16/11).
Every other verdict is what it was: recorded, nothing moves.

**Negative control:** `server/brain/drawerhold.py` (and `server/console/api.py`) back at the base: `SLOW` does not exist and the engine holds
nothing, so the tests marked (neg) fail on behaviour (no `hold` in the answer; the fast drive waits). (guard) tests pass in both states.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import drawerhold, memory

from test_chunksend_2533 import Lib, SID, rig, wait_for   # noqa: F401 -- rig is a fixture

CHUNKS, HELD = 8000, 4000                  # `c`: C1 (the slow one) holds 4,000 of 8,000; C2 (the fast one) misses the last 2


def _hold(tmp_path, setting: str) -> drawerhold.DrawerHold:
    tmp_path.mkdir(parents=True, exist_ok=True)
    conn = db.connect(tmp_path / "imagesrv.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    brain = drawerhold.DrawerHold(conn, mode_fn=lambda: "shadow", hold=lambda: setting)
    brain.gap(SID, 0.0)                    # the overhead per partition was measured (0 s): rewriting C1 is far shorter than completing it
    return brain


def _j1(rig, tmp_path, setting: str):
    """`a` and `b` whole; `c` -- two drawers -- fetches 4,000..7,999 (C1's, and C2's last two chunks inside them)."""
    brain = _hold(tmp_path, setting)
    engine = rig.make(lib=Lib(tmp_path, chunks=CHUNKS))
    engine.drawer_hold, engine.drawer_held = brain.evaluate, brain.executed
    everything = [[0, CHUNKS - 1]]
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": [], **({"C2": []} if mac == "c" else {})})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": everything})
    rig.tell("b", 1, "ended", {"B1": everything})
    assert rig.ask("c", 1, {"C1": [[0, HELD - 1]], "C2": [[0, CHUNKS - 3]]}) == {
        "ok": True, "action": "fetch", "pass": 2, "ranges": [[HELD, CHUNKS - 1]]}
    return engine, brain, everything


def _fetching_both(rig, fetched, received):
    """A `fetching` report of `c`: C1 crawls (`fetched` of its 4,000 missing), C2 -- the fast one -- already holds everything."""
    return rig.engine.report(SID, "c", {"part": 3, "pass": 2, "state": "fetching", "fetched": fetched, "received": received, "targets": [
        {"serial": "C1", "write_mbps": 120, "durable": [{"part": 3, "ranges": [[0, fetched - 1]]}]},
        {"serial": "C2", "write_mbps": 120, "durable": [{"part": 3, "ranges": [[0, CHUNKS - 1]]}]}]})


def test_a_drive_the_brain_marked_slow_is_held_not_failed_and_the_fast_one_goes_on_without_it(rig, tmp_path):
    """(neg) J1. The brain says DROP twice (4,000 chunks at a few a second; rewriting is far shorter): the second `fetching` report is
    answered with `hold: [C1]` -- not `fail` -- C1 is in `held_disks` and NOT in `failed_disks`, and `c` finishes with C2 alone: `c`, `a`
    and `b` are told `done` while C1 completes in the background."""
    engine, brain, everything = _j1(rig, tmp_path, drawerhold.SLOW)
    time.sleep(0.3)
    assert _fetching_both(rig, 4001, 1) == {"ok": True, "action": "wait"}                     # DROP once: counted, not executed
    assert engine._wave.held_disks == set()
    time.sleep(0.3)
    got = _fetching_both(rig, 4002, 2)
    assert got == {"ok": True, "action": "wait", "hold": ["C1"]}, got                          # the second one: held -- and no `fail`
    assert (3, "c", "C1") in engine._wave.held_disks and engine._wave.failed_disks == {}
    assert len(rig.named("chunk_disk_held")) == 1 and rig.named("chunk_disk_failed") == [], rig.events
    assert "הדיסק סומן איטי" in rig.named("chunk_disk_held")[0], rig.named("chunk_disk_held")
    assert rig.ask("c", 2, {"C2": everything})["action"] == "done"                              # the fast drive does not wait for C1
    assert rig.ask("a", 1, {"A1": everything})["action"] == "done" and rig.ask("b", 1, {"B1": everything})["action"] == "done"
    assert rig.named("send_failed") == [] and rig.named("chunk_disk_failed") == []
    rows = [d for d in memory.decisions(brain.conn, rule="K3", limit=20) if d["action"] == "DRAWER_HOLD"]
    assert rows and rows[0]["outcome"].startswith("בוצע: המגירה הוחזקה"), rows[0]               # the decision is closed by what was done
    assert "execution=slow" in rows[0]["evidence"] and "execution_wired=yes" in rows[0]["evidence"], rows[0]["evidence"]


def test_the_fast_drive_waits_for_the_slow_one_when_the_mode_is_off(rig, tmp_path):
    """(guard) `off`: the brain only records. The same two reports, and the answer is the plain `wait` -- nothing held, and `a`
    still waits for C1 (what J1 did for 15.7 minutes)."""
    engine, _brain, everything = _j1(rig, tmp_path, drawerhold.OFF)
    for n in (1, 2, 3):
        time.sleep(0.3)
        assert _fetching_both(rig, 4000 + n, n) == {"ok": True, "action": "wait"}
    assert engine._wave.held_disks == set() and engine._wave.failed_disks == {}
    assert rig.tell("a", 1, "ended", {"A1": everything})["action"] == "wait"


def _evaluate(brain, monkeypatch, verdict: str, below_floor=None):
    """One `fetching` evaluation of one drawer, with the judgment fixed: what `evaluate` hands the engine to execute."""
    monkeypatch.setattr(drawerhold, "judge", lambda **_kw: drawerhold.Judgment(verdict, "reason", ("x=1",), 5.0, 5.0, 3.0, 1.0, 4.0, below_floor))
    return brain.evaluate(session_id=SID, part=3, mac="c", drawers=[{"serial": "C1", "remaining": 9, "grown": 1}], received=1, elapsed_s=10.0,
                          later_chunks=0, chunks_total=10, zbytes_total=10, bits=700_000_000, parts=1)


def test_in_slow_mode_only_a_drop_said_twice_is_executed_and_as_a_hold(tmp_path, monkeypatch):
    """(neg) The brain's mark is DROP, twice in a row (`DROP_CONFIRMS`): then `C1` is handed over as CONTINUE -- a hold, never a DROP, so
    the engine does not fail it. Once is counted only; CONTINUE and UNDECIDED are what they were (nothing is executed)."""
    brain = _hold(tmp_path, drawerhold.SLOW)
    assert _evaluate(brain, monkeypatch, drawerhold.DROP) == {}                                # once: counted
    got = _evaluate(brain, monkeypatch, drawerhold.DROP)                                        # twice: marked slow
    assert list(got) == ["C1"] and got["C1"][0] == drawerhold.CONTINUE and got["C1"][1].startswith(drawerhold.SLOW_HE), got
    assert _evaluate(_hold(tmp_path / "x", drawerhold.SLOW), monkeypatch, drawerhold.CONTINUE) == {}
    assert _evaluate(_hold(tmp_path / "y", drawerhold.SLOW), monkeypatch, drawerhold.UNDECIDED) == {}


def test_on_is_what_it_was_a_drop_fails_and_a_continue_holds(tmp_path, monkeypatch):
    """(guard) `on`: unchanged -- CONTINUE holds at once, DROP (twice) is a DROP. `off`: nothing is executed."""
    on = _hold(tmp_path / "on", drawerhold.ON)
    assert _evaluate(on, monkeypatch, drawerhold.CONTINUE)["C1"][0] == drawerhold.CONTINUE
    on2 = _hold(tmp_path / "on2", drawerhold.ON)
    _evaluate(on2, monkeypatch, drawerhold.DROP, below_floor=True)
    assert _evaluate(on2, monkeypatch, drawerhold.DROP, below_floor=True)["C1"][0] == drawerhold.DROP     # #3895: measured below the floor
    off = _hold(tmp_path / "off", drawerhold.OFF)
    for _ in range(3):
        assert _evaluate(off, monkeypatch, drawerhold.DROP) == {}


# --- the setting and the decision, without the engine ------------------------------------------------------------------


def test_the_default_of_the_setting_is_slow_and_a_value_nobody_set_is_not_off(tmp_path):
    """(neg) Nadav 17/11: on by default, for a drive the brain marked slow. Not set = `slow`; an unknown stored value, or a setting that
    cannot be read, = `off` (a failure to read is no reason to execute)."""
    conn = db.connect(tmp_path / "imagesrv.db")
    assert drawerhold.mode(conn) == "slow"
    for raw, want in (("on", "on"), ("off", "off"), ("slow", "slow"), ("enforce", "off")):
        db.set_setting(conn, drawerhold.SETTING, raw)
        assert drawerhold.mode(conn) == want, raw
    assert drawerhold.mode(object()) == "off"
