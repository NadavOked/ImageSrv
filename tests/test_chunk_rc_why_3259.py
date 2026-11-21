"""#3259 -- a chunk receiver that ends with exit code 8 or 2 says WHY, in words, and the two are not the same sentence.

`chunk_partition_drawers` (agent/lib/restore/chunkrecv.sh) named only 6 (the round was closed) and 7 (another
image's stream). Everything else was "the chunk engine's receiver failed (rc=N)": a stream that hurt (8, bad-chunk,
agent/chunkcast.c) and a file or a call that was not read (2) looked the same, and the technician got a number
where #73 once gave the old receiver's operator a reason (principle 5: "the receiver said something" is not a reason).

Negative control: agent/lib/restore/chunkrecv.sh back to the base, agent/lib/restore/chunkrc.sh removed
(the harness of test_chunkrecv_2533 loads chunkrc.sh only when it is there) -- both codes fall to the generic line.
"""

from __future__ import annotations

from native import requires_native
from test_agent import BASH
from test_chunkrecv_2533 import DONE, RECEIVE1, _run   # the harness is that file's

pytestmark = requires_native(("bash", BASH), "jq", why="the agent's shell and its jq, as test_chunkrecv_2533")

GENERIC = "המקלט של מנוע החתיכות נכשל"


def _failed(tmp_path, rc: int) -> str:
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_RC={rc}")
    lines = [t for t in got["trace"] if t.startswith("FAIL sda")]
    assert lines and got["trace"][-1] == "rc=1", got["trace"]
    return lines[0]


def test_a_bad_chunk_is_named_as_the_stream_not_the_disk(tmp_path):
    line = _failed(tmp_path, 8)
    assert "לא תאמה את האינדקס" in line and "(rc=8)" in line, line
    assert GENERIC not in line, line


def test_a_receiver_that_could_not_read_or_write_its_files_is_named_for_that(tmp_path):
    line = _failed(tmp_path, 2)
    assert "קובץ המצב" in line and "(rc=2)" in line, line
    assert GENERIC not in line, line


def test_the_two_codes_do_not_read_the_same(tmp_path):
    """A stream that hurt and a file that was not read are two different places to look (#3259)."""
    eight, two = _failed(tmp_path / "e8", 8), _failed(tmp_path / "e2", 2)
    assert eight != two and "האינדקס" in eight and "קובץ המצב" not in eight and "לא תאמה" not in two


def test_a_code_nobody_named_keeps_the_generic_line_with_its_number(tmp_path):
    """The default stays: a code chunkcast does not say is shown as it is, never as one of the named ones."""
    line = _failed(tmp_path, 9)
    assert f"{GENERIC} (rc=9)" in line and "לא תאמה" not in line and "קובץ המצב" not in line, line


def test_the_codes_that_were_already_named_still_are(tmp_path):
    """6 = the round was closed, 7 = another image's stream: not swallowed by the new branches."""
    assert "הזרם שהגיע אינו של האימג'" in _failed(tmp_path / "s7", 7)
    assert GENERIC not in _failed(tmp_path / "s6", 6)
