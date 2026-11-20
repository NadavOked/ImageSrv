"""#3663 (אפיק #3653, נדב 18/11) S49: האם השרת שולח שאילתת IGMP -- המוח מחליט (`server/brain/igmpquery.py`, כלל K-QUERIER), `net/igmp_querier.py` שולח.

- **ההתנהגות זהה לזו של לפני #3663** (טסט האורקל עובר גם בבסיס, וזה בכוונה: הוא ההוכחה שהעברה לא שינתה כלום). `OldQuerier` למטה הוא העתק מילולי של `should_send` / `other_wins` / `other_present` של הבסיס (`lab/vm-1114f`,
  13828c0e4), כאורקל בטסט בלבד: רשת של הגדרה, שער, querier אחר (נמוך, גבוה, ישן) ושעות שליחה, והפסק חייב להיות זהה.
- **ההחלטה נרשמת** ב-`brain_decisions`: **רק מעבר** (הלולאה שואלת כל שנייה), עם הראיות; `SEND` נסגרת במה שבא אחריה; ב-`off` מחליטים ולא רושמים.
- **אין החלטה = לא שולחים**, והמסך אומר את זה: כלל שנפל אינו "תקין" ואינו שאילתה.

**בקרה שלילית:** בבסיס אין `igmpquery.py` (`igmpquery = None`: נבדקת ההתנהגות, לא ה-import) והשרת אינו רושם דבר ב-`brain_decisions`.
"""

from __future__ import annotations

import ipaddress
import itertools

import pytest

from server.brain import memory, veto
from server.db import set_setting
from server.net import igmp_querier as q

from test_igmp_querier_1972 import Clock, conn, ip_packet, make, run  # noqa: F401 -- conn is a fixture

try:                                    # בקרה שלילית: בלי המודול נבדקת ההתנהגות, לא ה-import
    from server.brain import igmpquery
except ImportError:                     # pragma: no cover
    igmpquery = None

OWN = "10.44.12.10"


@pytest.fixture(autouse=True)
def _forget():
    veto.forget()
    if igmpquery is not None:
        igmpquery.forget()
    yield
    veto.forget()
    if igmpquery is not None:
        igmpquery.forget()


def _need() -> None:
    assert igmpquery is not None, "server/brain/igmpquery.py is missing: nothing decides whether the server queries"


def rows(conn):
    return memory.decisions(conn, rule="K-QUERIER", limit=64)


class OldQuerier:
    """`Querier` at the base: the three methods that decided, verbatim, over plain state."""

    def __init__(self, *, enabled, gate, own, other, other_seen, last_sent):
        self.enabled_, self.gate_, self.own, self.other, self.other_seen, self.last_sent = enabled, gate, own, other, other_seen, last_sent

    def other_present(self, now):
        if self.other_seen is None or now - self.other_seen > q.OTHER_PRESENT:
            return None
        return self.other

    def other_wins(self, now):
        other = self.other_present(now)
        if other is None or self.own is None:
            return False
        return ipaddress.ip_address(other) < ipaddress.ip_address(self.own)

    def refused(self):
        if not self.enabled_:
            return "כבוי בהגדרות"
        return self.gate_

    def should_send(self, now):
        if self.refused() is not None or self.other_wins(now):
            return False
        return self.last_sent is None or now - self.last_sent >= q.QUERY_INTERVAL


NOW = 5000.0
ENABLED = (True, False)
GATES = (None, "בלי כתובת IPv4")
OTHERS = (None, "10.44.12.1", "10.44.12.99", "10.44.12.10")       # none, lower than ours, higher, the very same address
OTHER_SEEN = (None, NOW - 1, NOW - q.OTHER_PRESENT, NOW - q.OTHER_PRESENT - 0.5)    # never, fresh, on the edge, expired
LAST_SENT = (None, NOW, NOW - q.QUERY_INTERVAL + 0.5, NOW - q.QUERY_INTERVAL, NOW - 3 * q.QUERY_INTERVAL)
OWNS = (OWN, None)


def test_should_send_is_the_old_decision_over_the_whole_grid(conn):
    sent = held = 0
    for enabled in ENABLED:
        set_setting(conn, q.SETTING, "true" if enabled else "false")
        for gate, own, other, seen, last in itertools.product(GATES, OWNS, OTHERS, OTHER_SEEN, LAST_SENT):
            old = OldQuerier(enabled=enabled, gate=gate, own=own, other=other, other_seen=seen, last_sent=last)
            new = make(conn, Clock(), gate=(lambda _i, g=gate: g))
            new.own, new.other, new.other_seen, new.last_sent = own, other, seen, last
            want = old.should_send(NOW)
            assert new.should_send(NOW) is want, (enabled, gate, own, other, seen, last)
            assert new.other_wins(NOW) is old.other_wins(NOW)
            assert new.other_present(NOW) == old.other_present(NOW)
            sent += want
            held += not want
    assert sent > 20 and held > 100, (sent, held)


def test_the_comparison_is_the_one_of_rfc_2236_the_lower_address_wins():
    _need()
    assert igmpquery.other_wins(OWN, "10.44.12.1") and not igmpquery.other_wins(OWN, "10.44.12.99")
    assert not igmpquery.other_wins(OWN, OWN) and not igmpquery.other_wins(None, "10.44.12.1") and not igmpquery.other_wins(OWN, None)
    assert igmpquery.other_wins("10.44.12.200", "10.44.12.30"), "the comparison is numeric, not by text ('...30' > '...200' as text)"


def test_the_querier_holds_no_comparison_of_its_own():
    """The old three decided in igmp_querier.py; now they ask the brain (the protocol constants stay there, as inputs)."""
    import inspect
    for name in ("other_wins", "other_present", "should_send", "decide"):
        source = inspect.getsource(getattr(q.Querier, name))
        assert "igmpquery" in source and "ip_address" not in source, name
    assert not hasattr(q, "ipaddress"), "igmp_querier.py still imports ipaddress: a comparison of addresses is still done here"


# --- recorded --------------------------------------------------------------------------------------------------------

def test_a_cycle_records_one_row_per_state_and_closes_it_with_what_came_after(conn):
    clock = Clock()
    querier = make(conn, clock)
    # at once: SEND. 60 s, 60 s, 4 s: the other querier (lower) is heard and wins. 5 s, 125 s later: still heard? no -> it expires, we send again
    lower = ip_packet("10.44.12.1", q.query_packet())
    sock = run(querier, clock, [60, 60, lower, 1, 300])
    states = [r["verdict"] for r in rows(conn)]
    assert states[::-1] == ["SEND", "YIELD", "SEND"], states
    first = rows(conn)[-1]
    assert first["point"] == "S49" and first["action"] == "QUERIER" and first["mode"] == "shadow"
    assert (first["entity_kind"], first["entity_key"]) == ("network", "eth-deploy")
    assert "YIELD" in first["outcome"], first
    assert "own=10.44.12.10" in first["evidence"] and "interval_s=125.0" in first["evidence"], first["evidence"]
    yielded = rows(conn)[1]
    assert "other=10.44.12.1" in yielded["evidence"] and "10.44.12.1" in yielded["reason"], yielded
    assert len(sock.sent) == 2, "the first query, and the one after the other querier went quiet"


def test_every_tick_is_asked_but_only_a_change_is_recorded(conn):
    clock = Clock()
    querier = make(conn, clock)
    run(querier, clock, [10] * 40)                  # 400 s: three queries, 400 questions
    assert [r["verdict"] for r in rows(conn)] == ["SEND"]


def test_a_refusal_is_recorded_with_its_reason_and_nothing_is_sent(conn):
    set_setting(conn, q.SETTING, "false")
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [60, 60, 60])
    assert sock.sent == []
    (row,) = rows(conn)
    assert row["verdict"] == "REFUSED" and "כבוי בהגדרות" in row["reason"], row


def test_off_decides_and_does_not_record(conn):
    set_setting(conn, veto.SETTING, "off")
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [60, 130])
    assert len(sock.sent) == 2 and rows(conn) == []


# --- no decision = no query -------------------------------------------------------------------------------------------

def test_a_rule_that_raised_sends_nothing_and_the_health_row_says_so(conn, monkeypatch):
    _need()
    def boom(**_):
        raise RuntimeError("the rule fell")
    monkeypatch.setattr(igmpquery, "judge", boom)
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [60, 60, 60])
    assert sock.sent == [], "no decision, and a query was sent"
    state, detail = querier.health()
    assert state == "bad" and "לא הוכרע" in detail, (state, detail)
    (row,) = rows(conn)
    assert row["verdict"] == "UNDECIDED" and "RuntimeError" in row["reason"], row


def test_the_health_row_is_not_green_when_the_decision_is_missing(conn, monkeypatch):
    _need()
    clock = Clock()
    querier = make(conn, clock)
    run(querier, clock, [10])
    assert querier.health()[0] == "ok"
    monkeypatch.setattr(igmpquery, "judge", lambda **_: 1 / 0)
    querier.should_send(clock.now + 1)
    assert querier.health()[0] == "bad"
