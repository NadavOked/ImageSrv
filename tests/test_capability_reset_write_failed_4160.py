"""#4160 -- a capability reset whose write FAILED must not be reported as done (principle 5).

`capability._check_card` threw the learned capability away with `memory.remember(..., replace=True)`, which returns `False` when the
write failed (a locked database, a full disk) -- and ignored that. The `capability_reset` journal line and the LR24/RESET decision
were written anyway: the journal said "the learned capability was thrown away" while the next precheck still read the old ceiling.
Found by a static review of #4123 (Codex 26/11); proved here with a real failing write (a trigger that aborts the UPDATE of the
replacement), not with a mock.

**Negative control:** with `server/brain/capability.py` back at the base of this PR the failing-write tests fail on behaviour: a
`capability_reset` journal line and a RESET decision exist while `C.learned()` still answers the old ceiling. The success test passes
in both states (the guard against a fix that never resets).
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import capability as C, memory, veto

MAC = "78:ac:c0:9b:18:85"
LEARNED_BPS = 90 * 10 ** 6
REFUSE = "CREATE TRIGGER refuse_replace BEFORE UPDATE ON brain_memory BEGIN SELECT RAISE(ABORT, 'locked'); END"


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    veto.forget()


def _taught_on_100(conn):
    assert memory.remember(conn, "machine", MAC, "capability", {"limited_bps": float(LEARNED_BPS), "link_mbps": 100.0},
                           {"iface": "eth0"})
    assert C.learned(conn, MAC) == LEARNED_BPS


def _resets(conn):
    return [d for d in memory.decisions(conn, kind="machine", key=MAC, rule="LR24", limit=50)
            if d["action"] == "MACHINE_CAPABILITY" and d["verdict"] == "RESET"]


def _journal(conn):
    return conn.execute("SELECT detail FROM journal WHERE event = 'capability_reset'").fetchall()


def test_a_reset_whose_write_failed_is_not_journalled_as_done(conn):
    """(neg) The replacement is refused by the database: the old ceiling is still what the brain reads, so no line may say it was thrown away."""
    _taught_on_100(conn)
    conn.execute(REFUSE)
    C.reset_on_hello(conn, MAC, 1000)
    assert C.learned(conn, MAC) == LEARNED_BPS, "the write failed: the old capability is still there"
    assert _journal(conn) == [], "a reset that did not happen is not journalled"
    assert _resets(conn) == [], "and has no RESET decision"


def test_the_next_hello_retries_a_reset_that_failed(conn):
    """(neg) The row still carries the old link speed, so the same hello speed is a change again -- the reset is not lost, only late."""
    _taught_on_100(conn)
    conn.execute(REFUSE)
    C.reset_on_hello(conn, MAC, 1000)
    conn.execute("DROP TRIGGER refuse_replace")
    C.reset_on_hello(conn, MAC, 1000)
    assert C.learned(conn, MAC) is None
    assert len(_journal(conn)) == 1 and len(_resets(conn)) == 1


def test_a_reset_that_worked_is_still_journalled_once(conn):
    """The same hello with a working database: the line and the decision are written (passes with and without the fix)."""
    _taught_on_100(conn)
    C.reset_on_hello(conn, MAC, 1000)
    assert C.learned(conn, MAC) is None
    assert len(_journal(conn)) == 1 and len(_resets(conn)) == 1
