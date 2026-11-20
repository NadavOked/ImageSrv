"""#3125 (L12, U5, U6): מגירה שחזרה, ההחלטה להשלים אותה, ודיסק זר בחריץ -- מגיעים ליומן של השרת, לא רק ליומן הסוכן.

עד כאן שלושתם היו שורה ביומן של הסוכן (`drawerback.sh`). השרת, הקונסולה והמוח ידעו עליהם רק מזנב היומן בסוף הסבב. עכשיו
הסוכן שם כל משפט בדיווח ההתקדמות (`round_notes`, `agent/lib/restore/roundnote.sh`), והשרת (`server/agent/round_notes.py`)
כותב כל אחד ליומן פעם אחת, עם ה-MAC והגל.

- הסוכן: משפט אחד לכל (סוג, מפתח); כל דיווח נושא את כולם (דיווח שאבד אינו מאבד משפט); ה-JSON נבנה פעם אחת בכתיבה; תקרה
  למספר המשפטים; עבודה חדשה מתחילה בלי המשפטים של הקודמת; דיווח של משימה (לכידה) ושל מצב בלי גל אינו נושא אותם.
- השרת: סוג שאינו מוכר, מפתח ריק או טיפוס שגוי -- נזנחים כל אחד לבדו; משפט ארוך נחתך ולא נזנח; כפילות נחסמת גם אחרי
  שהשרת עלה מחדש.

בקרה שלילית: ראו את גוף ה-PR (קובצי הייצור החוזרים לבסיס; `roundnote.sh` ו-`round_notes.py` הם קבצים חדשים ונשארים,
ובלי החיווט שלהם -- `drawerback.sh`, `datareport.sh`, `progress.sh`, `reports.py` -- אף משפט אינו נכתב).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from agentlib import agent_sources
from native import requires_native
from server import db
from server.agent import reports, round_notes
from test_agent import AGENT, BASH, posix
from test_chunk_drawer_returns_2533 import PULLED_BACK, RECEIVE2
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

REPO = AGENT.parent
MAC = "78:ac:c0:9b:18:85"
needs_bash = requires_native(("bash", BASH), why="the agent's shell")

UNIT = r'''
export RUN_DIR="$1" LOG_FILE="$1/agent.log"; mkdir -p "$RUN_DIR"
. agent/lib/core/common.sh; . agent/lib/core/jsonq.sh; . agent/lib/core/datareport.sh
''' + agent_sources("sysinfo", "progress", "failmark") + r'''   # job_reset, as the agent loads it (#2272); progress calls failmark and sysinfo (NEEDS)
# as the agent sources it. Not there = the tree before #3125 (the negative control): nothing is noted
[ ! -f agent/lib/restore/roundnote.sh ] || . agent/lib/restore/roundnote.sh
'''


def _unit(tmp_path, script: str, env: str = "") -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.sh").write_text(UNIT + script, encoding="utf-8", newline="\n")
    proc = subprocess.run([BASH, "-c", f"{env} . {posix(tmp_path / 't.sh')!r} {posix(tmp_path)!r}"], capture_output=True,
                          text=True, encoding="utf-8", cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _notes(tmp_path) -> list[dict]:
    f = tmp_path / "round_notes"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


# --- the agent -----------------------------------------------------------------------------------------------------


@needs_bash
def test_a_note_is_one_json_line_once_per_kind_and_key(tmp_path):
    _unit(tmp_path, r'''
round_note drawer_back sdc@1 'מגירה 3 חזרה (serial A"B, sdc)'
round_note drawer_back sdc@1 'the same instance again'
round_note drawer_back sdc@2 'a second time it comes back'
round_note drawer_stranger 'sdc|X' 'a stranger'
''')
    assert _notes(tmp_path) == [
        {"kind": "drawer_back", "key": "sdc@1", "text": 'מגירה 3 חזרה (serial A"B, sdc)'},
        {"kind": "drawer_back", "key": "sdc@2", "text": "a second time it comes back"},
        {"kind": "drawer_stranger", "key": "sdc|X", "text": "a stranger"}]


@needs_bash
def test_the_report_carries_every_note_of_the_job_as_one_valid_json_fragment(tmp_path):
    out = _unit(tmp_path, r'''
round_note drawer_back sdc@1 'a "quoted" \ sentence'
round_note drawer_stranger k 'שניים'
echo "sess [$(round_notes_json ses_1)]"
echo "nosess [$(round_notes_json '')]"
echo "extra [$(machine_extra_json ses_1 '' | grep -o ',"round_notes":.*')]"
echo "task [$(machine_extra_json ses_1 task_9 | grep -c round_notes)]"
''')
    got = dict(line.split(" ", 1) for line in out.splitlines())
    fragment = json.loads("{" + got["sess"][1:-1].lstrip(",") + "}")
    assert [n["text"] for n in fragment["round_notes"]] == ['a "quoted" \\ sentence', "שניים"]
    assert got["nosess"] == "[]", "a report that is no session's carried notes"
    assert got["extra"] == got["sess"], "the progress report did not carry the notes"
    assert got["task"] == "[0]", "a task report (a capture) carried a round's notes"


@needs_bash
def test_a_job_past_the_cap_drops_the_rest_and_says_so(tmp_path):
    _unit(tmp_path, r'''
for n in 1 2 3 4 5; do round_note drawer_back "k$n" "n$n"; done
''', env="ROUND_NOTES_MAX=3")
    assert [n["key"] for n in _notes(tmp_path)] == ["k1", "k2", "k3"]
    log = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert "round note drawer_back (k4) dropped: 3 notes already" in log and "(k5) dropped" in log, log


@needs_bash
def test_a_new_job_starts_without_the_notes_of_the_last(tmp_path):
    _unit(tmp_path, r'''
round_note drawer_back k1 'last job'
job_reset
round_note drawer_back k2 'this job'
''')
    assert [n["key"] for n in _notes(tmp_path)] == ["k2"]


# --- the agent's own three places ----------------------------------------------------------------------------------


@needs_bash
def test_a_drawer_pushed_back_in_and_its_completion_are_told(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=PULLED_BACK)
    assert got["trace"][-1] == "rc=0", got
    by_kind = {n["kind"]: n for n in _notes(tmp_path)}
    assert set(by_kind) == {"drawer_back", "drawer_back_part"}, by_kind
    assert "מגירה 3 חזרה (serial SER-sdb, sdc)" in by_kind["drawer_back"]["text"]
    assert by_kind["drawer_back_part"]["key"] == "sdc/p3"
    assert "מחיצה 3: השלמה ב-HTTP על sdc (טווחים: 1, חתיכות במחיצה: 5)" == by_kind["drawer_back_part"]["text"], by_kind
    assert "מגירה 3 חזרה (serial SER-sdb, sdc)" in got["log"]            # the agent's own log is as it was


@needs_bash
def test_another_disk_in_the_slot_is_told_once_however_many_passes(tmp_path):
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"RECV_BACK_SERIAL=SER-other {PULLED_BACK}")
    assert got["trace"][-1] == "rc=0", got
    strangers = [n for n in _notes(tmp_path) if n["kind"] == "drawer_stranger"]
    assert len(strangers) == 1, strangers
    assert strangers[0]["key"] == "sdc|SER-other"
    assert strangers[0]["text"] == "דיסק אחר הוכנס באמצע הסבב (sdc, serial SER-other) -- הוא אינו מצטרף לסבב הזה"


@needs_bash
def test_a_drawer_that_never_left_tells_nothing(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE])
    assert got["trace"][-1] == "rc=0" and _notes(tmp_path) == []


def test_the_agent_sources_the_notes_and_the_hooks_are_in_place():
    agent = (REPO / "agent" / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/roundnote.sh"' in agent
    assert "round_notes_json" in (REPO / "agent/lib/core/datareport.sh").read_text(encoding="utf-8")
    assert "round_notes_reset" in (REPO / "agent/lib/core/progress.sh").read_text(encoding="utf-8")


# --- the server ----------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    round_notes._seen.clear()
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at, started_at) VALUES ('ses_n', 'grp_CLONERS', 'img', 'p', 1, 300,"
              " 'running', 't', ?, 0, ?)", (db.now_iso(), db.now_iso()))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES ('ses_n', ?, 'writing', ?)",
              (MAC, db.now_iso()))
    c.commit()
    return c


NOTES = [{"kind": "drawer_back", "key": "sdc@1", "text": "מגירה 3 חזרה (serial A, sdc)"},
         {"kind": "drawer_back_part", "key": "sdc/p3", "text": "מחיצה 3: השלמה ב-HTTP על sdc (טווחים: 1, חתיכות במחיצה: 5)"},
         {"kind": "drawer_stranger", "key": "sdc|X", "text": "דיסק אחר הוכנס באמצע הסבב"}]


def _journal(conn):
    return [dict(r) for r in conn.execute(
        "SELECT event, detail, machine, round_id FROM journal WHERE event IN ('drawer_back', 'drawer_back_part',"
        " 'drawer_stranger') ORDER BY id")]


def _report(conn, notes):
    body = {"session_id": "ses_n", "mac": MAC, "state": "writing", "targets": []}
    if notes is not ...:
        body["round_notes"] = notes
    assert reports.ingest(conn, body)["ok"]


def test_each_note_reaches_the_journal_once_with_the_machine_and_the_wave(conn):
    _report(conn, NOTES)
    assert [(r["event"], r["detail"], r["machine"], r["round_id"]) for r in _journal(conn)] == [
        (n["kind"], n["text"], MAC, "ses_n") for n in NOTES]
    for _ in range(3):
        _report(conn, NOTES)                     # the agent sends ALL of them with every report
    assert len(_journal(conn)) == 3
    _report(conn, NOTES + [{"kind": "drawer_back", "key": "sdc@2", "text": "it came back again"}])
    assert [r["detail"] for r in _journal(conn)][-1] == "it came back again" and len(_journal(conn)) == 4


def test_a_server_that_started_again_does_not_write_what_is_already_there(conn):
    _report(conn, NOTES)
    round_notes._seen.clear()                    # the process is new; the journal is not
    _report(conn, NOTES)
    assert len(_journal(conn)) == 3


def test_a_report_without_notes_or_with_a_bad_list_writes_nothing_and_does_not_fail(conn):
    for notes in (..., None, "x", {"kind": "drawer_back"}, [], [None, 3, "x"]):
        _report(conn, notes)
    assert _journal(conn) == []


def test_a_bad_note_is_dropped_alone(conn):
    _report(conn, [{"kind": "unknown_kind", "key": "k", "text": "t"},        # no label in the journal: not a journal event
                   {"kind": "drawer_back", "key": "", "text": "t"},
                   {"kind": "drawer_back", "key": "k", "text": "   "},
                   {"kind": "drawer_back", "key": "k" * 500, "text": "t"},
                   {"kind": "drawer_back", "key": 7, "text": "t"},
                   {"kind": "drawer_back", "key": "k", "text": 7},
                   {"kind": "drawer_stranger", "key": "good", "text": "the one that is fine"}])
    assert [(r["event"], r["detail"]) for r in _journal(conn)] == [("drawer_stranger", "the one that is fine")]


def test_a_long_sentence_is_cut_not_dropped_and_loses_its_control_characters(conn):
    _report(conn, [{"kind": "drawer_back", "key": "k", "text": "a\x00b\nc" + "x" * 1000}])
    [row] = _journal(conn)
    assert row["detail"].startswith("abc") and len(row["detail"]) == round_notes.MAX_TEXT


def test_the_journal_has_a_hebrew_label_and_a_stranger_is_a_warning(conn):
    from server.console import journal_he
    for event in ("drawer_back", "drawer_back_part", "drawer_stranger"):
        assert event in journal_he.EVENTS_HE, event
    assert journal_he.severity("drawer_stranger") == "warn" and journal_he.severity("drawer_back") == "info"
