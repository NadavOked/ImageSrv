"""#3672 (אפיק #3653, נדב 18/11) A15: תקרות הזמן של בדיקת הכתיבה במסך ההמתנה -- המוח קובע (`server/brain/idleceil.py`, כלל K-IDLE), הסוכן מחיל.

מה נבדק:

- **הערכים לא השתנו, ואי אפשר שיסטו.** ההחלטה במוח (300 / 60 / 10) שווה לערכים שהסוכן טוען בעצמו כשאין החלטה (`idlecheck.sh`, `idlegate.sh`): הטסט טוען את שני
  הקבצים ומשווה. אחד מהם שזז בלי השני נופל כאן, בשמו.
- **הסוכן מחיל את מה שהמוח אמר** (`idle_ceilings_refresh`, מהתשובה האחרונה, `$RUN_DIR/response.json`): מספר שלם בין 1 ל-3600 בלבד. שדה חסר, תשובה שלא נקראה,
  ערך לא תקין או סוג לא נכון = הערך שהסוכן טען (אין החלטה = הערכים של היום, גם כשהסביבה קבעה אחרים, ובחזרה אליהם כשההחלטה נעלמת).
- **ההחלטה בפועל משנה את ההמתנה**: `idle_wc_ready` על דיסק שבדיקה רצה עליו מוותר בשמו אחרי התקרה של המוח, לא אחרי זו שבסביבה.
- **ה-hello של משכפל נושא `idle_ceilings`**, של אחר לא; נרשם ב-`brain_decisions` פעם אחת לכל שינוי; `off` ו-hello בקריאה בלבד אינם רושמים; כלל שנפל = אין שדה.
- לא נגעתי בדיסק עסוק ולא קטעתי סבב בבדיקה: התקרות הן כמה מחכים לפני שמוותרים בשם.

**בקרה שלילית:** בבסיס אין `idleceil.py` (`idleceil = None`: נבדקת ההתנהגות), אין `idle_ceilings` בתשובה, ו-`idle_ceilings_refresh` אינו קיים: הסוכן ממתין את הערך
שבסביבה גם כשהמוח אמר אחרת.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from server import db
from server.brain import memory, veto
from test_agent import AGENT, posix
from test_beeps_2067 import BUSYBOX, run_ash

try:                                    # בקרה שלילית: בלי המודול נבדקת ההתנהגות, לא ה-import
    from server.brain import idleceil
except ImportError:                     # pragma: no cover
    idleceil = None

MAC = "aa:bb:cc:00:00:21"
_ASH = requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")
TODAY = {"busy_wait_s": 300, "upload_s": 60, "kill_wait_s": 10}


@pytest.fixture(autouse=True)
def _forget():
    veto.forget()
    if idleceil is not None:
        idleceil.forget()
    yield
    veto.forget()
    if idleceil is not None:
        idleceil.forget()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "brain.db")


def _need() -> None:
    assert idleceil is not None, "server/brain/idleceil.py is missing: nothing decides the ceilings of the waiting-screen write check"


def rows(conn):
    return memory.decisions(conn, rule="K-IDLE", limit=64)


# --- the brain ------------------------------------------------------------------------------------------------------

def test_a_cloner_gets_the_ceilings_of_today_and_nobody_else_does(conn):
    _need()
    assert idleceil.judge("cloner") == TODAY
    for role in ("build", "deploy", "", None, 1, ["cloner"]):
        assert idleceil.judge(role) is None, role
        assert idleceil.decide(conn, MAC, role) is None, role
    assert idleceil.decide(conn, MAC, "cloner") == TODAY


@_ASH
def test_the_brain_and_the_agent_agree_on_the_values_of_today(tmp_path):
    """What the agent holds when nobody decides is exactly what the brain decides: one without the other cannot move."""
    _need()
    run = tmp_path / "run"; run.mkdir()
    out = run_ash(f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/restore/idlecheck.sh; . {posix(AGENT)}/lib/restore/idlegate.sh; '
                  'echo "$IDLE_WC_BUSY_WAIT_S $IDLE_WC_UPLOAD_S $IDLE_WC_KILL_S"', timeout=30)
    assert out.split() == [str(TODAY["busy_wait_s"]), str(TODAY["upload_s"]), str(TODAY["kill_wait_s"])], out


def test_the_decision_is_recorded_once_and_again_only_when_it_changes(conn, monkeypatch):
    _need()
    for _ in range(4):
        idleceil.decide(conn, MAC, "cloner")
    (row,) = rows(conn)
    assert (row["point"], row["action"], row["verdict"], row["mode"]) == ("A15", "IDLE_CEILINGS", "SET", "shadow")
    assert (row["entity_kind"], row["entity_key"]) == ("machine", MAC)
    assert row["evidence"] == ["busy_wait_s=300", "upload_s=60", "kill_wait_s=10"], row["evidence"]
    monkeypatch.setattr(idleceil, "BUSY_WAIT_S", 120)
    assert idleceil.decide(conn, MAC, "cloner")["busy_wait_s"] == 120
    assert [r["evidence"][0] for r in rows(conn)] == ["busy_wait_s=120", "busy_wait_s=300"]


def test_off_and_a_read_only_hello_decide_and_do_not_record(conn):
    _need()
    assert idleceil.decide(conn, MAC, "cloner", record=False) == TODAY
    assert rows(conn) == []
    from server.db import set_setting
    set_setting(conn, veto.SETTING, "off")
    assert idleceil.decide(conn, MAC, "cloner") == TODAY
    assert rows(conn) == []


def test_a_rule_that_raised_is_no_field_and_one_undecided_row(conn, monkeypatch):
    _need()
    def boom(_role):
        raise RuntimeError("the rule fell")
    monkeypatch.setattr(idleceil, "judge", boom)
    for _ in range(3):
        assert idleceil.decide(conn, MAC, "cloner") is None
    (row,) = rows(conn)
    assert row["verdict"] == "UNDECIDED" and "RuntimeError" in row["reason"], row


# --- the hello answer --------------------------------------------------------------------------------------------------

pytest.importorskip("fastapi")
from test_nic_live_3277 import _hello  # noqa: E402
from test_server_room import room_server  # noqa: E402,F401 -- fixture


def test_a_cloners_hello_answer_carries_the_ceilings(room_server):
    out = _hello(room_server["anon"], ["S1"])
    assert out["idle_ceilings"] == TODAY, out.get("idle_ceilings")
    again = _hello(room_server["anon"], ["S1"])
    assert again["idle_ceilings"] == TODAY
    assert [r["verdict"] for r in rows(room_server["ctx"].conn)] == ["SET"], "one row per change, not one per hello"


# --- the agent ---------------------------------------------------------------------------------------------------------

def _refresh(tmp_path: Path, answer: object, env: str = "", twice: bool = False) -> list[str]:
    """What the agent holds after `idle_ceilings_refresh` against this answer: busy upload kill. ``answer``: a dict (written as json), a str (written as is), or None (no file)."""
    run = tmp_path / "run"; run.mkdir(parents=True, exist_ok=True)
    if answer is not None:
        (run / "response.json").write_text(answer if isinstance(answer, str) else json.dumps(answer), newline="\n")
    script = (f'export RUN_DIR={posix(run)!r}; {env} . {posix(AGENT)}/lib/restore/idlecheck.sh; . {posix(AGENT)}/lib/restore/idlegate.sh; '
              'idle_ceilings_refresh; echo "$IDLE_WC_BUSY_WAIT_S $IDLE_WC_UPLOAD_S $IDLE_WC_KILL_S"')
    return run_ash(script, timeout=30).split()


@_ASH
def test_the_agent_applies_what_the_brain_said(tmp_path):
    assert _refresh(tmp_path, {"idle_ceilings": {"busy_wait_s": 120, "upload_s": 30, "kill_wait_s": 5}}) == ["120", "30", "5"]


@_ASH
def test_a_missing_answer_or_field_is_the_values_of_today(tmp_path):
    assert _refresh(tmp_path / "a", None) == ["300", "60", "10"]                                  # no response.json yet
    assert _refresh(tmp_path / "b", {"schema": 1}) == ["300", "60", "10"]                         # an old server
    assert _refresh(tmp_path / "c", "{not json") == ["300", "60", "10"]                           # an answer cut in the middle
    assert _refresh(tmp_path / "d", {"idle_ceilings": {"busy_wait_s": 50}}) == ["50", "60", "10"]   # one of three: the others stay


@_ASH
@pytest.mark.parametrize("bad", [0, -1, 3601, 1.5, "10", True, None, [], {}, 1e9])
def test_a_value_that_is_not_a_whole_number_of_seconds_in_range_is_no_decision(tmp_path, bad):
    got = _refresh(tmp_path, {"idle_ceilings": {"busy_wait_s": bad, "upload_s": bad, "kill_wait_s": bad}})
    assert got == ["300", "60", "10"], (bad, got)


@_ASH
@pytest.mark.parametrize("not_an_object", ["slow", 7, [300, 60, 10], None, True])
def test_ceilings_that_are_not_an_object_are_no_decision(tmp_path, not_an_object):
    assert _refresh(tmp_path, {"idle_ceilings": not_an_object}) == ["300", "60", "10"]


@_ASH
def test_the_environment_is_the_value_of_today_and_the_brain_wins_over_it(tmp_path):
    env = "IDLE_WC_BUSY_WAIT_S=2; IDLE_WC_UPLOAD_S=7; IDLE_WC_KILL_S=3;"
    assert _refresh(tmp_path / "a", None, env) == ["2", "7", "3"]
    assert _refresh(tmp_path / "b", {"idle_ceilings": {"busy_wait_s": 40, "upload_s": 41, "kill_wait_s": 42}}, env) == ["40", "41", "42"]


@_ASH
def test_a_decision_that_goes_away_returns_to_the_values_of_today(tmp_path):
    """In ONE process: the brain said 120, then the answer has nothing -- the 120 does not outlive the decision."""
    run = tmp_path / "run"; run.mkdir()
    answer = run / "response.json"
    answer.write_text(json.dumps({"idle_ceilings": {"busy_wait_s": 120, "upload_s": 30, "kill_wait_s": 5}}), newline="\n")
    out = run_ash(f'export RUN_DIR={posix(run)!r}; . {posix(AGENT)}/lib/restore/idlecheck.sh; . {posix(AGENT)}/lib/restore/idlegate.sh; '
                  'idle_ceilings_refresh; echo "$IDLE_WC_BUSY_WAIT_S $IDLE_WC_UPLOAD_S $IDLE_WC_KILL_S"; '
                  f'echo \'{{"schema":1}}\' > {posix(answer)!r}; '
                  'idle_ceilings_refresh; echo "$IDLE_WC_BUSY_WAIT_S $IDLE_WC_UPLOAD_S $IDLE_WC_KILL_S"', timeout=30)
    assert [l.split() for l in out.strip().splitlines()[-2:]] == [["120", "30", "5"], ["300", "60", "10"]], out


@_ASH
def test_a_round_waits_for_a_running_check_as_long_as_the_brain_said_not_as_long_as_the_environment_did(tmp_path):
    """The brain says 1 s, the environment 6 s: the disk is refused by name after ~1 s. Nothing is written to it either way."""
    run = tmp_path / "run"; (run / "idlecheck" / "busy.sda").mkdir(parents=True)          # a check is running on sda
    (run / "response.json").write_text(json.dumps({"idle_ceilings": {"busy_wait_s": 1}}), newline="\n")
    out = run_ash(f'export RUN_DIR={posix(run)!r} D_ROLE=cloner SERVER=http://x; IDLE_WC_BUSY_WAIT_S=6; '
                  f'. {posix(AGENT)}/lib/restore/idlecheck.sh; . {posix(AGENT)}/lib/restore/idlegate.sh; '
                  't0=$(date +%s); idle_wc_ready sda dst; echo "rc=$? waited=$(( $(date +%s) - t0 ))"', timeout=60)
    assert "לא נגענו בו" in out and "rc=1" in out, out
    waited = int(out.rsplit("waited=", 1)[1].split()[0])
    assert waited <= 3, f"waited {waited} s: the brain's ceiling (1 s) was not used, the environment's (6 s) was\n{out}"
    assert not (run / "idlecheck" / "res.sda").exists(), "something was written about the disk"


@_ASH
def test_the_answer_is_read_by_jq_only_when_it_changed(tmp_path):
    """`idle_wc_scan` calls `idle_ceilings_refresh` once a second per cloner: jq runs when response.json changed (mtime + size), not every call.

    A jq that counts its runs and then runs the real one. Three calls on the same answer = one run; a changed answer = one more, and its values.
    """
    run = tmp_path / "run"; run.mkdir()
    bindir = tmp_path / "bin"; bindir.mkdir()
    count = tmp_path / "jq.count"
    fake = bindir / "jq"
    fake.write_text(f'#!/bin/sh\necho x >> {posix(count)}\nexec "$REAL_JQ" "$@"\n', newline="\n")
    fake.chmod(0o755)
    answer = run / "response.json"
    answer.write_text(json.dumps({"idle_ceilings": {"busy_wait_s": 120, "upload_s": 30, "kill_wait_s": 5}}), newline="\n")
    show = 'echo "$IDLE_WC_BUSY_WAIT_S $IDLE_WC_UPLOAD_S $IDLE_WC_KILL_S"; wc -l < ' + posix(count)
    out = run_ash(f'export RUN_DIR={posix(run)!r}; REAL_JQ=$(command -v jq); export REAL_JQ; PATH={posix(bindir)!r}:$PATH; '
                  f'. {posix(AGENT)}/lib/restore/idlecheck.sh; . {posix(AGENT)}/lib/restore/idlegate.sh; '
                  f'idle_ceilings_refresh; idle_ceilings_refresh; idle_ceilings_refresh; {show}; '
                  f'echo \'{{"idle_ceilings":{{"busy_wait_s":200,"upload_s":40,"kill_wait_s":6}},"pad":1}}\' > {posix(answer)!r}; '
                  f'idle_ceilings_refresh; idle_ceilings_refresh; {show}', timeout=30)
    lines = out.strip().splitlines()[-4:]
    assert [lines[0].split(), lines[1].strip()] == [["120", "30", "5"], "1"], f"three calls on the same answer must run jq once\n{out}"
    assert [lines[2].split(), lines[3].strip()] == [["200", "40", "6"], "2"], f"a changed answer is read once more, with its values\n{out}"
