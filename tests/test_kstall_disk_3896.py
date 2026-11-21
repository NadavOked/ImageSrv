"""#3896 -- K-STALL says "the stream stopped" only on evidence of the stream; a frozen drive is `DISK_STALLED`.

Hardware, 19/11 (#3189, `room_63714d82`, E3): `dm suspend` on the APS from 23:01:30 to 23:12:58 (688 s, `poll.txt`). The agent said
`waiting_disk` (stall.sh: the stream kept coming after that drawer stopped); K-STALL wrote "the stream stopped, not the drive" or nothing.
E1 (22:21): the whole completion ran over HTTP -- no multicast stream -- and the reasoning was the same.

What is checked, on the pure judgment and through the room label with the journal: (1) a drawer whose cause is `waiting_disk` for at least
the limit is `DISK_STALLED` -- with one drawer, with two where only one froze (the other still moves: that was NOT_STALLED), and never the
stream; (2) a short `waiting_disk` is nothing yet; (3) `waiting_net` on all, or no cause, stays what it was (`STREAM_STALLED`); (4) drawers
that all complete over HTTP are `NO_STREAM` -- not the stream, and not the disk; (5) the row is written once, closed when it ends, and a
change disk <-> stream closes the old row and opens a new one.
"""

from __future__ import annotations

import pytest

from server import db
from server.brain import memory, silence, veto
from server.rounds import room

R = "room_63714d82"


def w(stalled_s, stall=None, stall_s=None, step=None):
    d = {"state": "writing", "stalled_s": stalled_s}
    if stall:
        d.update(stall=stall, stall_s=stall_s)
    if step:
        d["rejoin"] = {"missing": 100, "chunks": None, "part": 3, "step": step}
    return d


def M(*drawers):
    return [{"drawer_list": list(drawers)}]


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    silence.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()
    silence.forget()


def _rows(conn):
    return list(reversed(memory.decisions(conn, rule="K-STALL", limit=64)))


def test_replay_e3_one_frozen_drive_while_the_other_moves_is_the_drive():
    """APS frozen 688 s (`waiting_disk`), the other drawer moving: before, NOT_STALLED (not all stopped) and no word on the drive."""
    c = silence.judge_stall(M(w(688, "waiting_disk", 688), w(3)), R)
    assert c.verdict == "DISK_STALLED" and "הכונן קפא" in c.reason and "cause=disk" in c.evidence, c
    assert c.verdict != "STREAM_STALLED" and "הזרם נעצר ולא הכונן" not in c.reason


def test_both_drawers_frozen_on_their_disks_is_not_the_stream():
    """The old rule: two writing drawers stopped together = "the stream stopped". With `waiting_disk` on both it is the drives."""
    c = silence.judge_stall(M(w(400, "waiting_disk", 400), w(400, "waiting_disk", 400)), R)
    assert c.verdict == "DISK_STALLED" and "waiting_disk=2" in c.evidence, c


def test_a_single_writing_drawer_with_a_cause_has_a_verdict_not_no_basis():
    """HTTP-only completion or one drawer: used to be NOT_MEASURED (no majority). The agent's own cause is direct evidence."""
    assert silence.judge_stall(M(w(400, "waiting_disk", 400)), R).verdict == "DISK_STALLED"
    assert silence.judge_stall(M(w(400)), R).verdict == "NOT_MEASURED"          # no cause: as before


def test_a_short_waiting_disk_is_not_a_frozen_drive():
    c = silence.judge_stall(M(w(6, "waiting_disk", 6), w(3)), R)
    assert c.verdict == "NOT_STALLED", c


def test_waiting_net_everywhere_and_no_cause_stay_the_stream():
    assert silence.judge_stall(M(w(400, "waiting_net", 400), w(400, "waiting_net", 400)), R).verdict == "STREAM_STALLED"
    assert silence.judge_stall(M(w(400), w(400)), R).verdict == "STREAM_STALLED"


def test_a_completion_that_runs_only_over_http_has_no_stream_to_stop():
    """E1 (22:21): every drawer completes over HTTP; their silence is not "the stream stopped"."""
    c = silence.judge_stall(M(w(400, step="http"), w(400, step="http")), R)
    assert c.verdict == "NO_STREAM" and c.verdict not in ("STREAM_STALLED", "DISK_STALLED"), c
    mixed = silence.judge_stall(M(w(400, step="http"), w(400, step="neighbor")), R)
    assert mixed.verdict == "STREAM_STALLED", mixed                              # one is not over HTTP: as before


def test_http_only_but_a_drive_that_froze_is_still_the_drive():
    assert silence.judge_stall(M(w(400, "waiting_disk", 400, step="http"), w(400, step="http")), R).verdict == "DISK_STALLED"


# --- the label and the journal -----------------------------------------------------------------------------------------


def test_a_frozen_drive_is_recorded_once_closed_when_it_moves_and_the_label_does_not_blame_the_stream(conn):
    frozen = M(w(688, "waiting_disk", 688), w(3))
    assert room._stream_stalled(frozen, conn, R) is False                      # the label is for the stream only
    assert room._stream_stalled(frozen, conn, R) is False
    (row,) = _rows(conn)
    assert row["verdict"] == "DISK_STALLED" and "הכונן קפא" in row["reason"], row
    room._stream_stalled(M(w(2), w(3)), conn, R)
    (row,) = _rows(conn)
    assert row["outcome"], "the frozen-drive row must be closed when the drive moves again"


def test_a_change_from_disk_to_stream_closes_the_old_row_and_opens_a_new_one(conn):
    room._stream_stalled(M(w(400, "waiting_disk", 400), w(3)), conn, R)
    assert room._stream_stalled(M(w(400, "waiting_net", 400), w(400, "waiting_net", 400)), conn, R) is True
    first, second = _rows(conn)
    assert (first["verdict"], second["verdict"]) == ("DISK_STALLED", "STREAM_STALLED") and first["outcome"] and not second["outcome"]


def test_no_stream_is_not_recorded_as_a_stall(conn):
    assert room._stream_stalled(M(w(400, step="http"), w(400, step="http")), conn, R) is False
    assert _rows(conn) == []
