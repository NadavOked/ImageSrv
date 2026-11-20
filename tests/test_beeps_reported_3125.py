"""#3125 (S62 in the brain's catalog) -- which tunes a machine's speaker played in a round.

The brain and the journal did not know: a tune was a line in the agent's log at most (G5,
#3106). Now every tune is noted (``roundbeep.sh: _rb_note``) -- requested, then played or not
heard with why -- carried in the round's progress report (``"beeps"``, ``datareport.sh:
beeps_json``), kept in ``session_members.beeps_json`` and said once in the journal
(``round_beeps``) when the machine's round ends. **Not heard is never reported as heard**
(principle 5): a speaker that is missing, a write to it that failed.

The end tune is asked before the final report (``imagesrv-agent``), so the final report carries
it -- as ``requested``; what came of it is decided after the report left, and is in the log.

**Negative control** (Testrunner): ``agent/lib/core/roundbeep.sh``, ``agent/lib/core/datareport.sh``,
``agent/imagesrv-agent``, ``server/agent/reports.py``, ``server/db.py`` and ``server/console/journal_he.py``
at the base, ``server/agent/beeps.py`` removed -> every test here fails
on what it reads back; none of them imports the new module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import db
from server.agent import reports
from test_agent import AGENT, BASH, posix, sh
from test_beeps_2067 import BUSYBOX, prelude, run_ash
from agentlib import agent_sources, flat_lib
from native import requires_native

MAC = "78:ac:c0:9b:31:25"
ASH = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")
SHELL = requires_native(("bash", BASH))


def _noted(tmp_path: Path) -> list[tuple[str, str]]:
    f = tmp_path / "beeps"
    lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    return [tuple(l.split("|", 2)[1:]) for l in lines]


# --- the agent notes every tune -------------------------------------------------------------------

@ASH
def test_a_tune_with_no_speaker_is_noted_as_not_heard(tmp_path):
    run_ash(prelude(tmp_path, speaker=False) + "event_beep failure; wait")
    assert _noted(tmp_path) == [("failure", "requested"), ("failure", "unheard:no PC speaker (pcspkr)")]


@ASH
def test_a_tune_that_was_played_is_noted_as_played(tmp_path):
    run_ash(prelude(tmp_path) + "event_beep success; wait")
    assert _noted(tmp_path) == [("success", "requested"), ("success", "played")]


@ASH
def test_a_tune_turned_off_in_the_console_is_not_heard(tmp_path):
    run_ash(prelude(tmp_path, enabled=False) + "event_beep netlost; wait")
    assert _noted(tmp_path) == [("netlost", "requested"), ("netlost", "unheard:off in the console settings")]


# --- the report carries them ----------------------------------------------------------------------

LIB = flat_lib()


def _report(tmp_path: Path, session: str, beeps: str | None, kept: str | None) -> dict:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "state").write_text("waiting\n", encoding="ascii", newline="\n")   # not writing: no cpu snapshot here
    if beeps is not None:
        (run / "beeps").write_text(beeps, encoding="utf-8", newline="\n")
    if kept is not None:
        (run / "beeps.session").write_text(kept + "\n", encoding="ascii", newline="\n")
    return json.loads(sh(f'export RUN_DIR={posix(run)!r} SYSROOT={posix(tmp_path / "sys")!r}; . {posix(LIB)}/common.sh;'
                         f' {agent_sources("sysinfo", "diskinfo", "progress", "failmark")}. {posix(LIB)}/datareport.sh;'
                         f' build_progress {session} aa:bb:cc:dd:ee:01'))


NOTED = ("1794700000|failure|requested\n1794700001|failure|unheard:no PC speaker (pcspkr)\n"
         "1794700005|netlost|requested\n1794700007|netlost|played\nnot a line\n")


@SHELL
def test_the_report_carries_the_rounds_tunes_as_they_came_out(tmp_path):
    got = _report(tmp_path, "ses_1", NOTED, "ses_1").get("beeps")
    assert got == [{"at": 1794700000, "kind": "failure", "outcome": "requested"},
                   {"at": 1794700001, "kind": "failure", "outcome": "unheard", "why": "no PC speaker (pcspkr)"},
                   {"at": 1794700005, "kind": "netlost", "outcome": "requested"},
                   {"at": 1794700007, "kind": "netlost", "outcome": "played"}]


@SHELL
def test_a_new_round_does_not_carry_the_last_ones_tunes(tmp_path):
    """The guard of the round: the file is the last round's -- the first report of this one
    starts it afresh and carries nothing of it."""
    first = _report(tmp_path, "ses_2", NOTED, "ses_1")
    assert "beeps" not in first
    assert not (tmp_path / "run" / "beeps").exists()
    assert (tmp_path / "run" / "beeps.session").read_text().strip() == "ses_2"


def test_the_end_tune_is_asked_before_the_final_report():
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    body = text[text.index('    if [ "$_rc" -eq 0 ]; then'):]
    assert body.index("round_beep_result $_all") < body.index('report_final "$_ppid" "$2"'), \
        "the end tune is asked after the final report: the report never carries it"


# --- the server keeps them ------------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _session(conn, sid):
    conn.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
                 " state, opened_by, created_at, last_join_at, started_at) VALUES (?, 'grp_CLONERS',"
                 " 'img', 'p', 1, 300, 'running', 't', ?, 0, ?)", (sid, db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES (?, ?,"
                 " 'writing', ?)", (sid, MAC, db.now_iso()))
    conn.commit()


def _kept(conn):
    row = dict(conn.execute("SELECT * FROM session_members").fetchone())
    return json.loads(row["beeps_json"]) if row.get("beeps_json") else None


BEEPS = [{"at": 1794700000, "kind": "failmid", "outcome": "requested"},
         {"at": 1794700001, "kind": "failmid", "outcome": "unheard", "why": "no PC speaker (pcspkr)"},
         {"at": 1794700100, "kind": "success", "outcome": "requested"},
         {"at": 1794700101, "kind": "success", "outcome": "played"},
         {"at": 1794700200, "kind": "failure", "outcome": "requested"}]


def test_the_server_keeps_the_rounds_tunes_and_says_them_once(conn):
    _session(conn, "ses_b1")
    assert reports.ingest(conn, {"session_id": "ses_b1", "mac": MAC, "state": "writing", "targets": [],
                                 "beeps": BEEPS[:2]})["ok"]
    assert _kept(conn) == BEEPS[:2]
    # a report without the field does not erase what was kept
    reports.ingest(conn, {"session_id": "ses_b1", "mac": MAC, "state": "writing", "targets": []})
    assert _kept(conn) == BEEPS[:2]
    reports.ingest(conn, {"session_id": "ses_b1", "mac": MAC, "state": "done", "targets": [], "beeps": BEEPS})
    assert _kept(conn) == BEEPS
    [line] = conn.execute("SELECT detail FROM journal WHERE event = 'round_beeps'").fetchall()
    assert line["detail"] == (f"{MAC} in ses_b1: played: success · not heard: failmid (no PC speaker (pcspkr))"
                              " · asked, outcome not known: failure"), line["detail"]   # never "played" for one not heard


def test_a_malformed_tune_is_dropped_not_kept(conn):
    _session(conn, "ses_b2")
    bad = [{"at": True, "kind": "success", "outcome": "played"}, {"at": 5, "kind": "siren", "outcome": "played"},
           {"at": 5, "kind": "success", "outcome": "heard"}, "x", {"at": 7, "kind": "pulled", "outcome": "played"}]
    reports.ingest(conn, {"session_id": "ses_b2", "mac": MAC, "state": "writing", "targets": [], "beeps": bad})
    assert _kept(conn) == [{"at": 7, "kind": "pulled", "outcome": "played"}]
