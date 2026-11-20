"""#3064 (K5): the pure part of the completion route (`server/brain/route.py`) -- HTTP or a multicast pass.

The numbers are the opening constants of the module (research and the design draft, not measurements), except
`R_HTTP_START` (KT4: 42 chunks/s, the lowest measured on metal 13/11). What is checked is the ORDER of the rule:
the guard first, then a small tail, then `F` and the two times, then "undecided" -- and that a route is not flipped
inside the dwell or for less than the margin.

**Negative control:** this file imports the new module, so with it removed it fails on the import -- that is NOT a
negative control. The behavioural one is `test_brain_route_server_3064.py`, through the real server.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import memory, route
from server.brain.route import Guard, Need

R_HTTP, R_MC = route.R_HTTP_START, route.R_MC_START


def _need(mac: str, chunks: int, ranges: int = 1) -> Need:
    return Need(mac, chunks, ranges)


def test_a_small_tail_goes_to_http_now():
    got = route.choose([_need("a", 100)], 0, [], [_need("a", 100)], R_HTTP, R_MC, Guard(0, 0, 0.0))
    assert got.verdict == route.TAIL and got.route == route.HTTP and got.t_a <= route.TAIL_S


def test_many_cloners_missing_the_same_chunks_choose_the_multicast_pass():
    """Six cloners lack the same 6,000 chunks: one pass of 6,000 at r_mc beats six downloads two at a time."""
    whole = [_need(m, 6000) for m in "abcdef"]
    got = route.choose(whole, 6000, [], [], R_HTTP, R_MC, Guard(0, 0, 0.0))
    assert got.verdict == route.MULTICAST, got
    assert got.fanout == pytest.approx(6.0) and got.t_b < got.t_a


def test_one_cloner_missing_much_alone_chooses_http():
    """No chunk is shared: (a) and (b) are the same downloads, F is 1 -- HTTP, never the pass."""
    got = route.choose([_need("a", 20000, 900)], 0, [], [_need("a", 20000, 900)], R_HTTP, R_MC, Guard(0, 0, 0.0))
    assert got.verdict == route.HTTP and got.fanout == pytest.approx(1.0), got


@pytest.mark.parametrize("guard, word", [(Guard(rx_dropped=3, abandoned=0, io_full=0.0), "RcvbufErrors"),
                                         (Guard(rx_dropped=0, abandoned=2, io_full=0.0), "נטשו"),
                                         (Guard(rx_dropped=0, abandoned=0, io_full=0.5), "לחוץ")])
def test_loss_at_the_receivers_or_a_loaded_server_opens_no_new_http(guard, word):
    """B15, B19: before the tail and before the times -- protection first."""
    got = route.choose([_need("a", 10)], 0, [], [_need("a", 10)], R_HTTP, R_MC, guard)
    assert got.verdict == route.GUARD and word in got.reason, got


def test_a_guard_that_was_not_measured_is_not_a_reason_and_is_said():
    got = route.choose([_need("a", 10)], 0, [], [_need("a", 10)], R_HTTP, R_MC, Guard())
    assert got.verdict == route.TAIL
    assert {"rcvbuf=unmeasured", "abandoned=unmeasured", "io_full=unmeasured"} <= set(got.evidence)


def test_the_kernels_receive_buffer_errors_are_named_rcvbuf_in_the_evidence_and_not_rx_dropped():
    """#3618: `rx_dropped` is another counter (the card's, `nic_history`); the guard's is `RcvbufErrors`."""
    got = route.choose([_need("a", 10)], 0, [], [_need("a", 10)], R_HTTP, R_MC, Guard(rx_dropped=3, abandoned=0, io_full=0.0))
    assert "rcvbuf=3" in got.evidence and "abandoned=0" in got.evidence
    assert not any(e.startswith("rx_dropped=") for e in got.evidence), got.evidence


def test_between_the_thresholds_is_undecided_not_a_quiet_default():
    """Two cloners share some chunks: F is under 1.5 and HTTP is not 20% faster -- "undecided", today's code stands."""
    whole = [_need("a", 5000), _need("b", 5000)]
    got = route.choose(whole, 2000, [_need("a", 3000), _need("b", 3000)], [], R_HTTP, R_MC, Guard(0, 0, 0.0))
    assert got.fanout == pytest.approx(10000 / 8000)
    assert got.verdict == route.BAND and got.route == "TODAY" and "לא הוכרע" in got.reason, got


def test_the_route_is_not_flipped_inside_the_dwell_or_for_less_than_the_margin():
    """B13/B14: the pass was chosen 10 s ago; with a slow multicast (5 chunks/s) HTTP is far faster now, but 30 s
    have not passed -- stay. 100 s later, and 88% faster: switch."""
    two = [_need("a", 10000), _need("b", 10000)]
    calm = Guard(0, 0, 0.0)
    got = route.choose(two, 10000, [], [], R_HTTP, 5.0, calm, prev=(route.MULTICAST, 100.0), now=110.0)
    assert got.verdict == route.MULTICAST and "היסטרזיס" in got.reason, got
    assert route.choose(two, 10000, [], [], R_HTTP, 5.0, calm).verdict == route.HTTP     # no history: HTTP
    later = route.choose(two, 10000, [], [], R_HTTP, 5.0, calm, prev=(route.MULTICAST, 100.0), now=200.0)
    assert later.verdict == route.HTTP, later


def test_a_small_gain_does_not_flip_the_route_even_after_the_dwell():
    """B13: HTTP was chosen; now the pass is 10% faster (F = 2) -- 10% is not 15%: stay with HTTP."""
    two = [_need("a", 10000), _need("b", 10000)]
    calm = Guard(0, 0, 0.0)
    r_mc = 10000 / (0.9 * route.t_all_http(two, R_HTTP))               # T_B = 0.9 x T_A
    assert route.choose(two, 10000, [], [], R_HTTP, r_mc, calm).verdict == route.MULTICAST
    kept = route.choose(two, 10000, [], [], R_HTTP, r_mc, calm, prev=(route.HTTP, 0.0), now=1000.0)
    assert kept.verdict == route.HTTP and "היסטרזיס" in kept.reason, kept


@pytest.mark.parametrize("chunks, uptime, want", [(10, 10_000, 300), (42 * 1000, 10_000, 1800),
                                                   (42 * 200, 10_000, 600), (10, 100, 500), (42 * 1000, 0, 1800)])
def test_the_completion_contract(chunks, uptime, want):
    """KT3: max(300, 3 x chunks / r_http); KT5: plus what is left of 300 s after a restart; KT6: never above 1800."""
    assert route.lease_s(chunks, 42.0, uptime) == pytest.approx(want)


def test_r_http_is_the_median_of_the_last_downloads_or_the_start_value():
    assert route.r_http_of([])[0] == route.R_HTTP_START
    assert route.r_http_of([10.0, 50.0, 30.0, 0.0])[0] == 30.0
    assert route.r_http_of([1.0] * 3 + [100.0] * 5)[0] == 100.0          # only the last WINDOW


# --- the tracker, with an engine that says what it would say -------------------------------------------------------


class FakeChunk:
    no_file = None

    def __init__(self):
        self.st = {"session_id": "W-1", "partition": 3, "round": 2, "state": "repairing",
                   "multicast_chunks": 0, "fetched_chunks": 5, "repair_rounds": 1}

    def status(self):
        return dict(self.st)

    def rate_outcome(self, session_id):
        return {"chunks": 3600, "first_pass_s": 100.0}


class FakeBudget:
    sample = None


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")                # a session with no group: the rows this rule reads, only
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at) VALUES ('W-1', 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)")
    for mac in ("a", "b"):
        c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('W-1', ?, 'writing', 'now')",
                  (mac,))
    c.commit()
    return c


def _ledger(conn, mac: str, serial: str, ranges: list) -> None:
    import json
    conn.execute("INSERT OR REPLACE INTO chunk_ledger (serial, size_bytes, part, round_id, mac, image_id, index_xxh64,"
                 " chunks, ranges_json, updated_at) VALUES (?, 1, 3, 'W-1', ?, 'img', '0', 10, ?, 'now')",
                 (serial, mac, json.dumps(ranges)))
    conn.commit()


def _tracker(conn, clock, budget=None):
    return route.Tracker(conn, chunk=FakeChunk(), budget=budget or FakeBudget(), mode=lambda: "shadow",
                         clock=lambda: clock[0], started=0.0)


def _rows(conn, action):
    return [d for d in memory.decisions(conn, rule="K5", limit=100) if d["action"] == action]


def test_a_cloner_that_lacks_shared_chunks_listens_and_says_why(conn):
    """U4 (D10): both lack 0-4; b lacks 7-9 alone too -- it listens to the pass and fetches its own next round."""
    _ledger(conn, "a", "A1", [[5, 9]])
    _ledger(conn, "b", "B1", [[5, 6]])
    clock = [1000.0]
    t = _tracker(conn, clock)
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=5 to 2 fetch={}", "W-1")
    route_row = _rows(conn, route.ROUTE)[0]
    assert "today=MULTICAST" in route_row["evidence"] and "engine_plan=matches" in route_row["evidence"]
    assert "r_mc=36.0" in route_row["evidence"] and "r_mc_from=המעבר הראשון של הגל הזה" in route_row["evidence"]
    why = _rows(conn, route.WHY)
    assert [(w["entity_key"], w["verdict"]) for w in why] == [("b", "D10")] and "own_chunks=3" in why[0]["evidence"]


def test_what_the_engine_said_and_the_ledger_disagree_is_evidence_not_clean(conn):
    _ledger(conn, "a", "A1", [[0, 4]])
    t = _tracker(conn, [0.0])
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=9 to 3 fetch={}", "W-1")
    assert any(str(e).startswith("engine_plan=differs") for e in _rows(conn, route.ROUTE)[0]["evidence"])


def test_a_loaded_server_in_the_budget_sample_is_the_guard(conn):
    from server.brain.budget import Sample
    _ledger(conn, "a", "A1", [[0, 4]])
    budget = FakeBudget()
    budget.sample = Sample(broadcast_bps=0, served_bps=0.0, rx_dropped=0, tx_dropped=0, io_full=0.9, abandoned=0)
    _tracker(conn, [0.0], budget).on_event("chunk_repair", "W-1 p3 round=2 multicast=0 to 0 fetch={'a': 5}", "W-1")
    assert _rows(conn, route.ROUTE)[0]["verdict"] == route.GUARD


def test_a_wave_that_failed_leaves_the_download_and_the_round_not_measured_not_zero(conn):
    _ledger(conn, "a", "A1", [[0, 4]])
    t = _tracker(conn, [0.0])
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=0 to 0 fetch={'a': 5}", "W-1")
    t.on_event("send_failed", "boom", "W-1")
    assert "לא נמדד" in _rows(conn, route.LEASE)[0]["outcome"]
    assert "לא נמדד" in _rows(conn, route.ROUTE)[0]["outcome"]
    assert _rows(conn, route.FETCH) == []
    assert _rows(conn, route.TOTALS)[0]["verdict"] == "NO_TARGETED_MULTICAST"


def test_a_measured_download_teaches_r_http_to_the_next_round(conn):
    """B28: 5 chunks in 0.5 s = 10 chunks/s; the next round's route says r_http=10.0, from the downloads."""
    _ledger(conn, "a", "A1", [[0, 4]])
    clock = [0.0]
    t = _tracker(conn, clock)
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=0 to 0 fetch={'a': 5}", "W-1")
    _ledger(conn, "a", "A1", [[0, 9]])
    clock[0] = 0.5
    t.on_pass("W-1", "a", {"part": 3, "pass": 2, "state": "ended"})
    assert "rate_cps=10.00" in _rows(conn, route.FETCH)[0]["evidence"]
    _ledger(conn, "a", "A1", [[0, 7]])
    t.on_event("chunk_repair", "W-1 p3 round=3 multicast=0 to 0 fetch={'a': 2}", "W-1")
    assert "r_http=10.0" in _rows(conn, route.ROUTE)[0]["evidence"]


def test_nothing_raises_into_the_engine(conn):
    class Broken(FakeChunk):
        def status(self):
            raise RuntimeError("boom")
    t = route.Tracker(conn, chunk=Broken(), mode=lambda: "shadow")
    t.on_event("chunk_repair", "x", "W-1")
    t.on_pass("W-1", "a", {"part": "x"})
    t.on_pass("W-1", "a", None)


# --- #3446: the route's and the contract's own numbers are `predicted`; what the round took is `actual` ---------------


def test_the_route_and_the_lease_record_their_numbers_and_the_round_closes_with_the_measured_one(conn):
    """(neg) K5 computed T_A/T_B and the lease, and kept them in text. `predicted` is the time of the route the code
    runs today (the one the round is measured against); `actual` is what it took. `matched` stays NULL: K5 has no
    definition of a match until Nadav decides one (#3321)."""
    _ledger(conn, "a", "A1", [[0, 4]])                     # a lacks 5-9 alone: nobody else lacks them, so it fetches them
    _ledger(conn, "b", "B1", [[0, 9]])
    clock = [1000.0]
    t = _tracker(conn, clock)
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=0 to 0 fetch={'a': 5}", "W-1")
    row, lease = _rows(conn, route.ROUTE)[0], _rows(conn, route.LEASE)[0]
    assert row["unit"] == "s" and row["predicted"] is not None and row["predicted"] > 0, row
    assert lease["unit"] == "s" and lease["predicted"] >= route.LEASE_FLOOR_S, lease
    clock[0] = 1012.0
    t.on_event("send_done", "ok", "W-1")
    closed = _rows(conn, route.ROUTE)[0]
    assert closed["actual"] == 12.0 and closed["matched"] is None, closed


def test_a_round_with_nothing_to_decide_predicts_nothing_not_zero(conn):
    _ledger(conn, "a", "A1", [[0, 9]])                     # everyone has everything: the engine has nothing to repair
    _ledger(conn, "b", "B1", [[0, 9]])
    t = _tracker(conn, [0.0])
    t.on_event("chunk_repair", "W-1 p3 round=2 multicast=0 to 0 fetch={}", "W-1")
    got = _rows(conn, route.ROUTE)[0]
    assert got["verdict"] == route.BAND and got["predicted"] is None, got
