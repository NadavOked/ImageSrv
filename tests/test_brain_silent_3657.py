"""#3657 (אפיק #3653): משיכה או משימה שקטה (S30, K-SILENT) -- המוח מחליט (`server/brain/silence.py`), `pulls.sweep` ו-`silent_tasks.sweep` מכשילים.

- **ההתנהגות זהה לזו של לפני #3657.** `OLD_FAILURE`, `OLD_TASK` ו-`OLD_PULL_LIMIT` הם העותקים המילוליים של ההחלטות כפי שישבו ב-`pulls._failure`,
  ב-`silent_tasks.sweep` וב-`pulls.silent_seconds` בבסיס (`lab/vm-1114f` r33, `02d2092`), בתור אורקל בטסט בלבד. הטסטים הקיימים
  (`test_silent_task_2905`, `test_pull_lost_wait_3038`, `test_server_streams`, `test_clock_jump_1947`) ממשיכים לבדוק את הביצוע.
- **ההכשלה נרשמת** ב-`brain_decisions` עם הראיות, לפניה, והתוצאה אחריה. `KEEP` אינו נרשם (נשאל בכל מעבר).
- **אין החלטה = לא מכשילים** (עיקרון 5): כלל שנפל אינו סוגר משיכה או משימה, ו"לא ידוע" נרשם פעם אחת.
- שני עותקי ה-600 (`pulls.SILENT_SECONDS`, `silent_tasks.SILENT_SECONDS`) הם עכשיו אחד.

**בקרה שלילית:** בבסיס אין `server/brain/silence.py` (`silence = None`): הטסטים שעוברים דרך `pulls.sweep` ו-`silent_tasks.sweep` נופלים ב-assert על
`brain_decisions` ריק, והטסטים של המודול נופלים ב-assert שהמודול חסר.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

pytestmark = pytest.mark.usefixtures("classrooms_on")      # העזרים של המשיכות פותחים כיתה, כמו test_server_streams

from server import db                                      # noqa: E402
from server.agent import reports                           # noqa: E402
from server.brain import memory, veto                      # noqa: E402
from server.core.tasks import staging_dir                  # noqa: E402
from server.system import silent_tasks                     # noqa: E402

try:                                    # בקרה שלילית: בלי המודול ההתנהגות נבדקת, לא ה-import
    from server.brain import silence
except ImportError:                     # pragma: no cover
    silence = None

from test_server_streams import (                          # noqa: E402,F401
    SGDISK, overview, pull, report, report_failed, second_class, session_row, silence as silence_pull,
)
from test_silent_task_2905 import (                        # noqa: E402,F401
    _running_capture, _sweep, _task, _went_quiet,
)

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
OLD_SILENT, OLD_MARGIN = 600, 180


@pytest.fixture(autouse=True)
def _fresh_rule_registers():
    if silence is not None:
        silence.forget()
    yield
    if silence is not None:
        silence.forget()


def one(found):
    """החלטה אחת בדיוק, ואם לא: כשל התנהגותי שאומר מה נמצא (לא ValueError של פירוק רשימה)."""
    assert len(found) == 1, f"expected exactly one brain decision, got {len(found)}: {[(r['rule'], r['verdict']) for r in found]}"
    return found[0]


# --- ההתנהגות זהה: העותקים המילוליים של ההחלטות שהוסרו --------------------------------------------------------------------


def _old_parse(stamp):
    try:
        parsed = datetime.fromisoformat(stamp) if stamp else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None


def OLD_FAILURE(created_at, members, now, silent):
    """אורקל: `pulls._failure` בבסיס, מילה במילה."""
    if members and all(m["state"] in reports.FINAL for m in members):
        errors = "; ".join(m["error"] for m in members if m["error"])
        return errors or "המכונה דיווחה " + ", ".join(m["state"] for m in members)
    stamps = [_old_parse(m["updated_at"]) for m in members] or [_old_parse(created_at)]
    if None in stamps:
        return None
    last = max(stamps)
    if (now - last).total_seconds() <= silent:
        return None
    return f"אין דיווח מהמכונה מאז {last.astimezone().strftime('%H:%M')}"


def OLD_TASK(last, now):
    """אורקל: ההשוואה והסיבה של `silent_tasks.sweep` בבסיס, מילה במילה. מחזיר את הסיבה, או None."""
    if last is None or (now - last).total_seconds() <= OLD_SILENT:
        return None
    return (f"המכונה הפסיקה לדווח — אין דיווח ואין בייטים חדשים מאז"
            f" {last.astimezone().strftime('%H:%M')} (מעל {OLD_SILENT // 60} דקות)")


def OLD_PULL_LIMIT(lost):
    """אורקל: `pulls.silent_seconds` בבסיס."""
    return max(OLD_SILENT, lost + OLD_MARGIN)


def _ago(seconds):
    return (NOW - timedelta(seconds=seconds)).isoformat()


def test_the_brain_fails_a_pull_exactly_when_the_server_code_did():
    assert silence is not None, "server/brain/silence.py חסר"
    stamps = (None, _ago(0), _ago(599), _ago(600), _ago(601), _ago(1100), _ago(7200), "2026-11-18T11:50:00",
              "not-a-stamp", _ago(-30))
    states = ("waiting", "writing", "done", "failed", "partial", "lost")
    checked = 0
    for silent in (600, 1100):
        for created in stamps:
            for state in states:
                for stamp in stamps:
                    for error in (None, "boom"):
                        members = [{"mac": "aa", "state": state, "error": error, "updated_at": stamp}]
                        call = silence.judge_pull(members=members, created_at=created, now=NOW,
                                                  final=reports.FINAL, silent_s=silent)
                        got = call.reason if call.verdict in (silence.FAIL_REPORTED, silence.FAIL_SILENT) else None
                        assert got == OLD_FAILURE(created, members, NOW, silent), (silent, created, state, stamp, error)
                        checked += 1
            call = silence.judge_pull(members=[], created_at=created, now=NOW, final=reports.FINAL, silent_s=silent)
            got = call.reason if call.verdict == silence.FAIL_SILENT else None
            assert got == OLD_FAILURE(created, [], NOW, silent), (silent, created)
            checked += 1
    assert checked == 2 * 10 * (6 * 10 * 2 + 1)


def test_two_members_one_final_one_not_is_decided_like_before():
    assert silence is not None, "server/brain/silence.py חסר"
    for second in ("done", "writing"):
        members = [{"mac": "aa", "state": "failed", "error": "x", "updated_at": _ago(900)},
                   {"mac": "bb", "state": second, "error": None, "updated_at": _ago(10)}]
        call = silence.judge_pull(members=members, created_at=_ago(5000), now=NOW, final=reports.FINAL, silent_s=600)
        got = call.reason if call.verdict in (silence.FAIL_REPORTED, silence.FAIL_SILENT) else None
        assert got == OLD_FAILURE(_ago(5000), members, NOW, 600)


def test_the_brain_fails_a_task_exactly_when_the_server_code_did():
    assert silence is not None, "server/brain/silence.py חסר"
    for seconds in (None, -30, 0, 1, 599, 600, 601, 7200):
        last = None if seconds is None else NOW - timedelta(seconds=seconds)
        call = silence.judge_task(task_id="t", mac="aa", task_type="capture", last=last, now=NOW)
        got = call.reason if call.verdict == silence.FAIL_SILENT else None
        assert got == OLD_TASK(last, NOW), seconds
    for up_for in (-1, 0, 5, 599, 600, 601, 3600):
        assert silence.silent_task_grace(up_for) is (up_for <= OLD_SILENT), up_for


def test_the_pull_ceiling_is_still_600_or_the_lost_setting_plus_the_margin():
    assert silence is not None, "server/brain/silence.py חסר"
    assert (silence.SILENT_S, silence.LOST_MARGIN_S) == (OLD_SILENT, OLD_MARGIN)
    for lost in (180, 300, 420, 421, 900, 1800):
        assert silence.pull_silent_s(lost) == OLD_PULL_LIMIT(lost), lost


def test_the_old_constants_and_the_old_copy_of_the_logic_are_gone_from_the_server():
    from server.rounds import pulls                          # noqa: PLC0415
    for module, names in ((pulls, ("SILENT_SECONDS", "LOST_MARGIN_SECONDS", "silent_seconds", "_failure", "_parse")),
                          (silent_tasks, ("SILENT_SECONDS",))):
        assert [n for n in names if hasattr(module, n)] == [], module.__name__


# --- S30 דרך pulls.sweep -----------------------------------------------------------------------------------------------


def _rows(server, point_action: str | None = None) -> list[dict]:
    rows = list(reversed(memory.decisions(server["ctx"].conn, rule="K-SILENT", limit=64)))
    return [r for r in rows if point_action is None or r["action"] == point_action]


def _silent_pull(server, seconds):
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report(server, pull_id, lab2["mac1"], "writing").json()["ok"] is True
    silence_pull(server, pull_id, seconds)
    overview(server)                                        # המבט של הקונסולה סוחף את המשיכות
    return pull_id, lab2["mac1"]


def test_a_silent_pull_fails_and_the_decision_is_recorded_with_its_evidence_and_outcome(server):
    pull_id, mac = _silent_pull(server, 1100)
    row = session_row(server, pull_id)
    assert row["state"] == "failed" and row["failed_reason"].startswith("אין דיווח מהמכונה מאז")
    d = one(_rows(server, "SILENT_PULL"))
    assert (d["verdict"], d["entity_kind"], d["entity_key"], d["round_id"], d["point"], d["mode"]) == \
        ("FAIL_SILENT", "machine", mac, pull_id, "S30", "shadow")
    assert d["reason"] == row["failed_reason"]
    assert d["outcome"] == "השרת הכשיל את המשיכה" and d["outcome_state"] == "closed"
    assert "limit=600s" in d["evidence"] and any(e.startswith("age=11") for e in d["evidence"]), d["evidence"]


def test_a_pull_the_machine_failed_is_a_recorded_decision_that_carries_the_machines_own_reason(server):
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report_failed(server, pull_id, lab2["mac1"]).json()["ok"] is True
    overview(server)
    assert SGDISK in session_row(server, pull_id)["failed_reason"]
    d = one(_rows(server, "SILENT_PULL"))
    assert d["verdict"] == "FAIL_REPORTED" and SGDISK in d["reason"]


def test_a_pull_within_the_ceiling_is_kept_and_nothing_is_recorded(server):
    pull_id, _ = _silent_pull(server, 590)
    assert session_row(server, pull_id)["state"] == "running"
    assert _rows(server) == []


@pytest.mark.parametrize("mode, recorded", [("shadow", True), ("enforce", True), ("off", False)])
def test_every_mode_fails_the_same_and_off_only_stops_the_record(server, mode, recorded):
    db.set_setting(server["ctx"].conn, veto.SETTING, mode)
    pull_id, _ = _silent_pull(server, 1100)
    assert session_row(server, pull_id)["state"] == "failed"
    assert [r["mode"] for r in _rows(server)] == ([mode] if recorded else [])


def test_a_brain_that_fails_closes_no_pull_and_says_so_once(server, monkeypatch):
    """עיקרון 5: "לא הצלחנו להחליט" אינו "שתק". המשיכה נשארת `running`, ושורה אחת, לא אחת לכל סחיפה."""
    assert silence is not None, "server/brain/silence.py חסר"
    monkeypatch.setattr(silence, "judge_pull", lambda **k: 1 / 0)
    pull_id, _ = _silent_pull(server, 1100)
    overview(server)
    overview(server)
    assert session_row(server, pull_id)["state"] == "running"
    d = one(_rows(server))
    assert d["verdict"] == "UNDECIDED" and "ZeroDivisionError" in d["reason"] and d["entity_key"] == pull_id


# --- S30 דרך silent_tasks.sweep ----------------------------------------------------------------------------------------


def test_a_silent_capture_fails_by_name_and_the_decision_is_recorded(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    _sweep(ctx)
    row = _task(ctx, task_id)
    assert row["state"] == "failed" and "הפסיקה לדווח" in (row["error"] or "")
    assert not staging_dir(ctx.library.root, task_id).exists()          # התוצאה ללא שינוי (#1915)
    d = one(_rows(server, "SILENT_TASK"))
    assert (d["verdict"], d["entity_kind"], d["entity_key"], d["round_id"], d["point"], d["mode"]) == \
        ("FAIL_SILENT", "machine", "aa:bb:cc:00:00:10", task_id, "S30", "shadow")
    assert d["reason"] == row["error"] and d["outcome"] == "השרת הכשיל את המשימה"
    assert "type=capture" in d["evidence"] and "limit=600s" in d["evidence"]


def test_a_live_task_and_a_server_that_just_came_up_record_nothing(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _sweep(ctx)                                                          # טרייה
    _went_quiet(ctx, task_id, seconds=3600)
    _sweep(ctx, up_for=5)                                                # חסד העלייה
    assert _task(ctx, task_id)["state"] == "running" and _rows(server) == []
    _sweep(ctx)
    assert _task(ctx, task_id)["state"] == "failed" and len(_rows(server)) == 1


def test_a_report_between_the_decision_and_the_close_wins_and_the_outcome_says_so(server, monkeypatch):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    monkeypatch.setattr(silent_tasks, "update_one", lambda *a, **k: 0)   # הדיווח שינה את `updated_at`
    _sweep(ctx)
    assert _task(ctx, task_id)["state"] == "running"
    d = one(_rows(server, "SILENT_TASK"))
    assert d["verdict"] == "FAIL_SILENT" and "לא נסגרה" in d["outcome"], d


def test_a_brain_that_fails_closes_no_task_and_says_so_once(server, monkeypatch):
    assert silence is not None, "server/brain/silence.py חסר"
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    monkeypatch.setattr(silence, "judge_task", lambda **k: 1 / 0)
    for _ in range(3):
        _sweep(ctx)
    assert _task(ctx, task_id)["state"] == "running" and staging_dir(ctx.library.root, task_id).is_dir()
    d = one(_rows(server))
    assert d["verdict"] == "UNDECIDED" and d["entity_key"] == task_id and "ZeroDivisionError" in d["reason"]


def test_the_console_line_says_the_server_executed_it(server):
    ctx = server["ctx"]
    task_id = _running_capture(server)
    _went_quiet(ctx, task_id)
    _sweep(ctx)
    d = one(memory.decisions(ctx.conn, rule="K-SILENT", limit=1))
    assert "משימה שקטה" in d["line_he"] and "רק נרשם" not in d["line_he"] and "השרת ביצע" in d["line_he"], d["line_he"]
