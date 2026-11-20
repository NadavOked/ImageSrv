"""#3064 (K7: B10, X6, KT7): how many HTTP downloads of image files are in flight, and the ceiling the brain keeps.

KT7's opening value (the design draft, an estimate): 2 downloads server-wide, 1 per cloner. X6: the server-wide
ceiling moves inside 1-12 with the network budget's own AIMD (a cut halves it, a measured quiet adds one) -- Nadav
14/11, "no fixed numbers, the brain measures and adjusts". **B10 is recorded only**: no download ever waits for the
ceiling, in shadow or in enforce, until M14 (six cloners downloading together) gives it a number.

**Negative control:** with `server/brain/budget.py` and `server/agent/api.py` back at the base, the test marked (neg)
fails on behaviour: the download route counts nothing (`enter`/`leave` are never called). The tests on `Budget.conc`
fail there with an AttributeError -- that is NOT a negative control, and they are not counted as one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import PARTITION_BYTES
from server.brain import budget as B

from test_brain_budget_3064 import MBPS, Harness, _file_url


class Counting:
    """A budget for the download route: records the order of what the route asks."""

    def __init__(self):
        self.events = []

    async def acquire(self, ip, nbytes):
        self.events.append(("acquire", ip))

    def enter(self, ip):
        self.events.append(("enter", ip))

    def leave(self, ip):
        self.events.append(("leave", ip))


def test_the_download_route_counts_a_download_in_and_out_around_every_byte(server):
    """(neg) one download: `enter` before its first body, `leave` after its last -- also for a Range request."""
    ctx, anon = server["ctx"], server["anon"]
    ctx.budget = rec = Counting()
    got = anon.get(_file_url())
    assert got.status_code == 200 and got.content == PARTITION_BYTES
    kinds = [k for k, _ip in rec.events]
    assert kinds[0] == "enter" and kinds[-1] == "leave" and kinds.count("enter") == kinds.count("leave") == 1, kinds
    assert "acquire" in kinds[1:-1]
    rec.events.clear()
    assert anon.get(_file_url(), headers={"Range": "bytes=3-9"}).status_code == 206
    assert [k for k, _ip in rec.events][::len(rec.events) - 1] == ["enter", "leave"]


def test_a_budget_without_the_count_still_serves(server):
    """Guard, both states: a budget that only paces (`acquire`) -- the download arrives whole."""
    class Pacing:
        async def acquire(self, ip, nbytes):
            return None
    server["ctx"].budget = Pacing()
    got = server["anon"].get(_file_url())
    assert got.status_code == 200 and got.content == PARTITION_BYTES


# --- `Conc`: pure ----------------------------------------------------------------------------------------------------


def test_over_the_ceiling_is_the_third_download_or_the_second_from_one_cloner():
    c = B.Conc()
    c.enter("10.0.0.1")
    c.enter("10.0.0.2")
    assert c.over == 0 and c.peak == 2
    c.enter("10.0.0.3")                                   # 3 > 2 server-wide
    c.enter("10.0.0.1")                                   # 2 > 1 from one cloner
    got = c.drain()
    assert got["over"] == 2 and got["who"] == ["10.0.0.1", "10.0.0.3"] and got["peak"] == 4 and got["peak_one"] == 2
    for ip in ("10.0.0.1", "10.0.0.1", "10.0.0.2", "10.0.0.3"):
        c.leave(ip)
    assert c.active == {}
    assert c.drain()["peak"] == 4                          # a window starts from what was in flight when it began
    assert c.drain() == {"over": 0, "who": [], "peak": 0, "peak_one": 0, "cap": 2}


def test_the_ceiling_follows_the_budget_and_stays_inside_its_range():
    c = B.Conc()
    assert c.step(B.RAISE) == (2, 3)
    assert c.step(B.CUT_) == (3, 1)
    assert c.step(B.CUT_) == (1, 1)                       # never under 1
    for _ in range(20):
        c.step(B.RAISE)
    assert c.cap == B.CONC_RANGE[1] == 12
    assert c.step(B.HOLD) == (12, 12) and c.step(B.OPEN) == (12, 12) and c.step(B.CLAMP) == (12, 12)


# --- in the budget's cycle ---------------------------------------------------------------------------------------------


class Memory:
    def __init__(self):
        self.rows, self.outcomes = [], {}

    def remember(self, **row):
        self.rows.append(row)
        return len(self.rows)

    def resolve(self, row, outcome):
        self.outcomes[row] = outcome


def _conc_rows(mem):
    return [r for r in mem.rows if r["action"] == "HTTP_CONC"]


def test_downloads_over_the_ceiling_are_one_row_while_it_lasts_and_its_outcome_when_it_ends():
    mem = Memory()
    h = Harness(remember=mem.remember, resolve=mem.resolve)
    b = h.budget
    for ip in ("a", "b", "c"):
        b.enter(ip)
    h.tick(broadcast=0)
    rows = _conc_rows(mem)
    assert len(rows) == 1 and rows[0]["verdict"] == "WOULD_QUEUE" and rows[0]["mode"] == B.SHADOW
    assert {"over=1", "cap=2", "peak=3", "ip=c"} <= set(rows[0]["evidence"])
    b.enter("d")
    h.tick(broadcast=0)
    assert len(_conc_rows(mem)) == 1                     # still the same episode: no new row
    for ip in ("a", "b", "c", "d"):
        b.leave(ip)
    h.tick(broadcast=0)
    row = mem.rows.index(rows[0]) + 1
    assert "2 הורדות מעל התקרה" in mem.outcomes[row] and "עד 4 במקביל" in mem.outcomes[row], mem.outcomes


def test_a_loss_cuts_the_ceiling_and_quiet_measured_cycles_raise_it():
    h = Harness(ceiling=900 * MBPS)
    h.tick(broadcast=300 * MBPS, rx_dropped=0)            # the first cycle: nothing to compare with
    assert h.budget.last.action == B.HOLD and h.budget.conc.cap == 2
    h.tick(broadcast=300 * MBPS, rx_dropped=7)            # the receivers dropped packets: half
    assert h.budget.last.action == B.CUT_ and h.budget.conc.cap == 1
    h.tick(broadcast=300 * MBPS, rx_dropped=7)            # measured, clean: +1
    assert h.budget.last.action == B.RAISE and h.budget.conc.cap == 2
    h.tick(broadcast=300 * MBPS, rx_dropped=7)
    assert h.budget.conc.cap == 3


def test_shadow_and_enforce_never_hold_a_download_for_the_ceiling():
    """B10 is recorded only: `enter` returns at once however many are in flight."""
    for mode in (B.SHADOW, B.ENFORCE):
        h = Harness(mode=mode)
        for _ in range(50):
            assert h.budget.enter("10.0.0.9") is None
        assert h.budget.conc.active == {"10.0.0.9": 50}


def test_off_records_nothing_and_forgets_what_it_counted():
    mem = Memory()
    h = Harness(mode=B.OFF, remember=mem.remember, resolve=mem.resolve)
    for ip in ("a", "b", "c"):
        h.budget.enter(ip)
    assert h.tick(broadcast=0) is None
    assert _conc_rows(mem) == [] and h.budget.conc.over == 0
