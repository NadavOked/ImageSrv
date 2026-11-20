"""#3064 (B7, B12, B47; X9, X21): one more full multicast pass ("carousel") in place of the repair plan, when many cloners
lack a lot. The decision runs in shadow and is recorded in `brain_decisions`; it is executed only behind its own switch
(`brain_carousel` = on), off by default.

The numbers in the pure tests: a partition of 18,254 chunks (the design draft, 6ג: "a whole partition each"), 1.5MB a
compressed chunk and 700Mb/s for the stream -- numbers for the test, not measurements -- and KT4 (42 chunks/s over HTTP).

**Negative control:** with `server/rounds/chunksend.py` and `server/app.py` back at the base (`0222c6353`) and
`server/brain/carousel.py` removed, the tests marked (neg) fail on behaviour: the engine ignores `carousel` (the machines
are told `fetch`, and no full pass goes out), a full pass is never cut, and the server wires no carousel. The tests that
import `carousel` fail there on the import -- that is NOT a negative control, and they are not counted as one.
"""

from __future__ import annotations

import dataclasses
import subprocess
import threading

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory, veto
from server.rounds import repair

from test_chunksend_2533 import ALL, Runner, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import room_server                    # noqa: F401 -- shared fixture

N = 18_254
Z = 1_500_000
BITS = 700_000_000


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "b7.db")
    yield c
    veto.forget()


def _rows(conn, action="CAROUSEL"):
    return [d for d in memory.decisions(conn, rule="B7", limit=100) if d["action"] == action]


def _distinct(machines: int, each: int) -> dict:
    """``machines`` cloners, one drive each, each lacking ``each`` chunks no other one lacks (drawers pulled at
    different times)."""
    return {f"m{k}": {f"S{k}": repair.missing(N, [(k * each, k * each + each - 1)])} for k in range(machines)}


# --- the engine: the switch puts the full pass in place of the plan, and cuts it ---------------------------------------


class _FullOnce:
    """A carousel that is on: after the first pass, a full pass in place of the plan; after that, the plan."""

    def __init__(self):
        self.asked = []

    def __call__(self, session_id, part, rnd, chunks, durable, plan, sizes, bits):
        self.asked.append((part, rnd, sorted(durable)))
        if rnd != 1:
            return None
        lacking = {mac for mac, disks in durable.items() for have in disks.values() if repair.missing(chunks, have)}
        return dataclasses.replace(plan, multicast=[(0, chunks - 1)], receive=lacking, fetch={})


def test_with_the_switch_on_the_machines_that_lack_receive_a_full_pass_instead_of_fetching(rig):
    """(neg) b and c each lack chunks no one else lacks: the plan has them fetch. The carousel puts a full pass there."""
    engine = rig.make()
    engine.carousel = _FullOnce()
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})
    assert rig.ask("c", 1, {"C1": [[0, 8]]}) == {"ok": True, "action": "receive", "pass": 2}
    assert rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]}) == {"ok": True, "action": "receive", "pass": 2}
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    assert rig.runner.lists[1] == "0-9"                                  # every chunk, not only what is missing
    assert engine.status()["state"] != "failed"


class _Proc:
    """A `chunkcast send` that runs until it is terminated, and answers `wait(timeout)` like `subprocess.Popen`."""

    def __init__(self):
        self.code, self._done, self.terminated = None, threading.Event(), False

    def wait(self, timeout=None):
        if self._done.wait(10 if timeout is None else timeout):
            return self.code
        if timeout is None:
            raise AssertionError("the fake chunkcast was never released")
        raise subprocess.TimeoutExpired("chunkcast", timeout)

    def poll(self):
        return self.code if self._done.is_set() else None

    def terminate(self):
        self.code, self.terminated = -15, True
        self._done.set()

    kill = terminate


class _Runner(Runner):
    """The first pass ends at once; the full pass after it runs until it is cut."""

    def __call__(self, cmd):
        super().__call__(cmd)
        if len(self.processes) > 1:
            self.processes[-1] = _Proc()
        return self.processes[-1]


def test_a_full_pass_left_with_one_live_listener_is_cut_and_the_round_goes_on(rig):
    """(neg) B12/X9: during the full pass c is gone, and b listens alone. The pass is cut -- it is not the sender
    failing -- and the round goes on with b."""
    rig.runner = _Runner()
    engine = rig.make()
    engine.carousel = _FullOnce()
    cuts = []
    engine.carousel_cut = lambda sid, part, rnd, listening: (
        cuts.append(list(listening)) or ("one left" if len(listening) <= 1 else None))
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})
    rig.ask("c", 1, {"C1": [[0, 8]]})
    rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]})
    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    assert wait_for(lambda: cuts) and len(cuts[0]) == 2                  # both listen: not cut
    rig.members = ["a", "b"]                                             # c is gone mid-pass
    assert wait_for(lambda: rig.runner.processes[1].terminated)
    assert cuts[-1] == ["b"]
    assert engine.status()["state"] != "failed", engine.status()
    got = rig.ask("b", 2, {"B1": ALL})
    assert got.get("ok") and got.get("action") != "fail" and not got.get("fail"), got


def test_the_server_wires_the_carousel_to_the_chunk_engine(room_server):
    """(neg) The app hands the engine the brain's carousel and its cut."""
    chunk = room_server["ctx"].chunk
    assert chunk.carousel is not None and chunk.carousel_cut is not None
    assert type(chunk.carousel.__self__).__name__ == "Carousel"


# --- the decision (pure) -----------------------------------------------------------------------------------------------


def _plan(durable):
    return repair.plan(N, durable, repairs_done=1)


def test_six_cloners_each_lacking_their_own_chunks_get_a_full_pass():
    """B7: 6 x 3,000 chunks no one else lacks -- the plan is all HTTP through the server's link (~433 s); a full pass
    of the partition is ~313 s."""
    from server.brain import carousel as B
    durable = _distinct(6, 3_000)
    plan = _plan(durable)
    assert plan.multicast == [] and len(plan.fetch) == 6
    got = B.decide(N, [Z] * N, BITS, B.lacks_of(N, durable, plan.failed), plan)
    assert got.verdict == B.CAROUSEL, got
    ev = dict(e.split("=", 1) for e in got.evidence)
    assert ev["needy"] == "6" and ev["missing"] == "18000" and ev["t_pass_s"] == "313" and ev["t_plan_s"] == "433"


def test_two_cloners_lacking_the_same_few_chunks_keep_the_plan():
    """The same 300 chunks: one targeted multicast of 300 chunks (~5 s) beats the whole partition."""
    from server.brain import carousel as B
    have = repair.missing(N, [(100, 399)])
    durable = {"m0": {"S0": have}, "m1": {"S1": have}}
    plan = _plan(durable)
    got = B.decide(N, [Z] * N, BITS, B.lacks_of(N, durable, plan.failed), plan)
    assert got.verdict == B.KEEP, got


def test_one_cloner_lacking_outside_a_carousel_is_not_a_decision():
    from server.brain import carousel as B
    durable = _distinct(1, 3_000)
    plan = _plan(durable)
    assert B.decide(N, [Z] * N, BITS, B.lacks_of(N, durable, plan.failed), plan) is None


def test_in_a_carousel_one_cloner_left_hands_off_to_http():
    """B12: a carousel with one needy machine left does not multicast to one."""
    from server.brain import carousel as B
    durable = _distinct(1, 3_000)
    plan = _plan(durable)
    got = B.decide(N, [Z] * N, BITS, B.lacks_of(N, durable, plan.failed), plan, carouseling=True)
    assert got.verdict == B.LONE and "HTTP" in got.reason, got


def test_in_a_carousel_a_pass_that_gave_nobody_anything_suspends_it():
    """B47/X21: an empty full pass -- the carousel stops, and the plan is back."""
    from server.brain import carousel as B
    durable = _distinct(6, 3_000)
    plan = _plan(durable)
    got = B.decide(N, [Z] * N, BITS, B.lacks_of(N, durable, plan.failed), plan, carouseling=True,
                   empty=B.EMPTY_PASSES)
    assert got.verdict == B.SUSPEND, got


def test_unknown_sizes_or_rate_are_undecided_not_a_default():
    """Principle 5: what was not measured decides nothing."""
    from server.brain import carousel as B
    durable = _distinct(6, 3_000)
    plan = _plan(durable)
    lacks = B.lacks_of(N, durable, plan.failed)
    for sizes, bits in ((None, BITS), ([Z] * (N - 1), BITS), ([Z] * N, 0), ([Z] * N, None)):
        assert B.decide(N, sizes, bits, lacks, plan).verdict == B.UNDECIDED, (sizes is None, bits)


def test_a_failed_drive_is_not_counted_as_lacking():
    from server.brain import carousel as B
    durable = _distinct(2, 3_000)
    assert B.lacks_of(N, durable, {("m1", "S1"): "slow"}) == {"m0": repair.missing(N, durable["m0"]["S0"])}


# --- the state and the record ------------------------------------------------------------------------------------------


def test_shadow_records_the_carousel_and_runs_the_plan_and_the_switch_executes_it(conn):
    from server.brain import carousel as B
    durable = _distinct(6, 3_000)
    plan = _plan(durable)
    shadow = B.Carousel(conn, mode=lambda: "shadow", switch=lambda: None)
    assert shadow.consider("w1", 3, 2, N, durable, plan, [Z] * N, BITS) is None
    row = _rows(conn)[0]
    assert row["verdict"] == B.CAROUSEL and row["round_id"] == "w1" and "executed=no" in row["evidence"], row
    assert row["outcome"] and "צל" in row["outcome"]
    on = B.Carousel(conn, mode=lambda: "shadow", switch=lambda: B.ON)
    full = on.consider("w2", 3, 2, N, durable, plan, [Z] * N, BITS)
    assert full.multicast == [(0, N - 1)] and full.receive == set(durable) and full.fetch == {}
    assert "executed=yes" in _rows(conn)[0]["evidence"]
    off = B.Carousel(conn, mode=lambda: "off", switch=lambda: B.ON)
    assert off.consider("w3", 3, 2, N, durable, plan, [Z] * N, BITS) is None
    assert not [r for r in _rows(conn) if r["round_id"] == "w3"]


def test_the_shadow_carousel_follows_its_decisions_to_the_lone_handoff(conn):
    from server.brain import carousel as B
    c = B.Carousel(conn, mode=lambda: "shadow", switch=lambda: None)
    six = _distinct(6, 3_000)
    c.consider("w", 3, 2, N, six, _plan(six), [Z] * N, BITS)
    one = _distinct(1, 3_000)
    c.consider("w", 3, 3, N, one, _plan(one), [Z] * N, BITS)
    assert [r["verdict"] for r in reversed(_rows(conn))] == [B.CAROUSEL, B.LONE]
    c.consider("w", 3, 4, N, one, _plan(one), [Z] * N, BITS)            # out of the carousel: nothing to record
    assert len(_rows(conn)) == 2


def test_the_shadow_carousel_counts_an_empty_pass_and_suspends(conn):
    from server.brain import carousel as B
    c = B.Carousel(conn, mode=lambda: "shadow", switch=lambda: None)
    six = _distinct(6, 3_000)
    c.consider("w", 3, 2, N, six, _plan(six), [Z] * N, BITS)
    c.consider("w", 3, 3, N, six, _plan(six), [Z] * N, BITS)            # the same lacks: nobody got anything
    got = [r["verdict"] for r in reversed(_rows(conn))]
    assert got == [B.CAROUSEL, B.SUSPEND], got
    assert "empty_passes=1" in _rows(conn)[0]["evidence"]


def test_the_cut_waits_the_lone_dwell_and_records_it(conn):
    from server.brain import carousel as B
    now = [100.0]
    c = B.Carousel(conn, mode=lambda: "shadow", clock=lambda: now[0])
    assert c.cut("w", 3, 2, ["a", "b"]) is None
    assert c.cut("w", 3, 2, ["b"]) is None                              # alone from now
    now[0] += B.LONE_DWELL_S - 1
    assert c.cut("w", 3, 2, ["b"]) is None
    now[0] += 1
    assert c.cut("w", 3, 2, ["b"])
    row = _rows(conn, B.CUT_ACTION)[0]
    assert row["verdict"] == B.LONE and "listening=1" in row["evidence"], row
    now[0] += 1
    assert c.cut("w", 3, 2, []) is None                                  # no one: the dwell starts again
    now[0] += B.LONE_DWELL_S
    assert c.cut("w", 3, 2, [])
    assert _rows(conn, B.CUT_ACTION)[0]["verdict"] == B.SUSPEND
