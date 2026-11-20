"""#2388 in the chunk engine -- a machine that was not asked fails at the start ceiling (600 s)
while the server holds the wave for ANOTHER machine's operator question (#2202, up to
ASK_HOLD_MAX_SECONDS = 2100, prompt_hold.py).

The agent's side (`chunk_partition_drawers`, chunkrecv.sh): a first pass that never came
(chunkcast `pass no-stream`, rc 3) failed the machine at once. Now it listens again, and
the server's answer to that `listening` is the evidence: `held: true` = the sender holds
this pass for a question elsewhere, and the machine waits another start ceiling. No
`held`, or no answer at all, and it fails by name as before (principle 5). The server
bounds the hold (prompt_hold.release_overdue), so the wait is bounded too.

The server's side: tests/test_chunk_hold_2388_server.py.

The harness is test_chunkrecv_2533.py's, with one more fake: RECV_NOSTREAM=<n> -- the
first n receivers say they listen, hear nothing, and end `pass no-stream`.

Negative control: agent/lib/restore/chunkrecv.sh and server/rounds/chunksend.py back to the base -- see the PR.
"""

from __future__ import annotations

import pytest

import test_chunkrecv_2533 as R

pytestmark = R.pytestmark

HELD = {**R.RECEIVE1, "held": True}
ANCHOR = '            [ -z "${RECV_MUTE:-}" ] || return 2\n'
NOSTREAM = ('            _nn=$(cat "$RUN_DIR/nostream" 2>/dev/null || echo 0)\n'
            '            if [ "$_nn" -lt "${RECV_NOSTREAM:-0}" ]; then echo $((_nn + 1)) > "$RUN_DIR/nostream";'
            ' echo listening; echo "pass no-stream"; return 3; fi\n')
NO_STREAM_FAIL = "הזרם לא הגיע"


@pytest.fixture(autouse=True)
def _nostream_harness(monkeypatch):
    assert R.HARNESS.count(ANCHOR) == 1, "the fake chunkcast changed: the anchor of RECV_NOSTREAM is gone"
    monkeypatch.setattr(R, "HARNESS", R.HARNESS.replace(ANCHOR, NOSTREAM + ANCHOR))


def _receivers(trace: list[str]) -> int:
    return sum(1 for t in trace if t.startswith("chunkcast recv"))


def test_a_machine_that_was_not_asked_waits_while_the_server_holds_the_wave(tmp_path):
    """RED: the first `pass no-stream` failed both drawers ("פג הזמן -- הזרם לא הגיע"), while
    the server was holding the wave for another machine's question."""
    got = R._run(tmp_path, [R.RECEIVE1, HELD, HELD, R.DONE], env="RECV_NOSTREAM=2")
    trace = got["trace"]
    assert trace[-1] == "rc=0", got
    assert "DONE sda" in trace and "DONE sdb" in trace, trace
    assert not [t for t in trace if t.startswith("FAIL")], trace
    assert _receivers(trace) == 3, trace
    assert [b["state"] for b in got["bodies"]] == ["listening", "listening", "listening", "ended"]


def test_it_fails_by_name_once_the_server_no_longer_holds_the_wave(tmp_path):
    """RED (the count): held once, then not -- it fails at the next ceiling, not before and not
    never. On the base it failed at the first one (one receiver)."""
    got = R._run(tmp_path, [R.RECEIVE1, HELD, R.RECEIVE1, R.DONE], env="RECV_NOSTREAM=9")
    trace = got["trace"]
    assert trace[-1] == "rc=1", got
    assert any(t.startswith("FAIL sda") and NO_STREAM_FAIL in t for t in trace), trace
    assert _receivers(trace) == 3, trace


def test_no_hold_still_fails_by_name(tmp_path):
    """Guard (passes in both states): the server never says `held` -- the stream that did not come
    fails both drawers by name, after at most one more listen."""
    got = R._run(tmp_path, [R.RECEIVE1, R.RECEIVE1, R.DONE], env="RECV_NOSTREAM=9")
    trace = got["trace"]
    assert trace[-1] == "rc=1", got
    assert any(t.startswith("FAIL sda") and NO_STREAM_FAIL in t for t in trace), trace
    assert any(t.startswith("FAIL sdb") and NO_STREAM_FAIL in t for t in trace), trace
    assert "DONE sda" not in trace and _receivers(trace) <= 2, trace


def test_a_server_that_does_not_answer_is_no_hold(tmp_path):
    """Guard (passes in both states): no answer is not `held` (principle 5)."""
    got = R._run(tmp_path, [R.RECEIVE1, HELD, R.DONE], env="RECV_NOSTREAM=9 SERVER_GONE_AFTER=1")
    trace = got["trace"]
    assert trace[-1] == "rc=1", got
    assert any(t.startswith("FAIL sda") and NO_STREAM_FAIL in t for t in trace), trace
    assert _receivers(trace) <= 2, trace
