"""#2684 — כל forecast שהקונסולה מקבלת נושא `clock`, כמו `session.forecast` ב-hello.

`forecastText` מצייר "צפוי להסתיים ב-HH:MM" מ-`ends_at`, שהוא שעון הקיר של השרת. כשהשרת
לא מסונכרן (#2144) זו שעה שגויה, וגרועה מהיעדר שעה. ה-hello כבר אומר למשכפל (`clock`)
אם מותר לצייר שעה; כאן אותה ראיה (`ctx.clock_trust`, chrony, `synchronized is True`)
נוסעת גם ל-`/room.round.forecast`, ל-`/overview.pulls[].forecast` ול-`/tasks[].forecast`.
העיצוב עצמו (שעה רק כש-`clock === true`) נבדק ב-`learn_eta_1843.test.cjs`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from server.learn import eta as learn_eta

from test_hello_eta_1843 import _history, _open, _running, clocked  # noqa: F401
from test_server_room import CLONER1

NOT_PROVEN = [
    {"synchronized": False, "offset_ms": 4000.0},      # chrony ענה: לא מסונכרן
    {"synchronized": None, "offset_ms": None},         # chrony לא נקרא -- "לא בדקנו" אינו "מסונכרן"
    RuntimeError("chronyc is not installed"),          # והמדידה עצמה נכשלה
]


def _round_forecast(env) -> dict:
    return env["admin"].get("/api/console/room").json()["round"]["forecast"]


def _expire(env) -> None:
    """הקריאה האחרונה של chrony תקפה 60ש'; הטסט מפקיע אותה כדי שהקריאה הבאה תשאל שוב."""
    env["ctx"].clock_trust._at = None


def _running_pull(env) -> None:
    ctx = env["ctx"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at, roster_json, kind)"
        " VALUES ('pull_live', 'grp_CLONERS', 'img_7f3a91', 'p', 1, 0, 'running', 't', ?, 0, ?, ?,"
        " 'unicast')", (now, now, json.dumps([CLONER1])))
    ctx.conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                     " VALUES ('pull_live', ?, 'writing', ?)", (CLONER1, now))
    ctx.conn.commit()


def _capture_task(env, task_id: str, state: str) -> None:
    env["ctx"].conn.execute(
        "INSERT INTO tasks (id, mac, type, disk, image_id, name, state, created_by,"
        " created_at, updated_at) VALUES (?, ?, 'capture', 'sda', 'img_new', 'n', ?, 't',"
        " '2026-10-21T10:00:00+00:00', '2026-10-21T10:00:00+00:00')", (task_id, CLONER1, state))
    env["ctx"].conn.commit()


# --- חדר ----------------------------------------------------------------------


def test_the_room_forecast_carries_a_true_clock_when_chrony_says_synchronized(clocked):
    _history(clocked)
    _running(clocked)
    fc = _round_forecast(clocked)
    assert fc["why"] is None and fc["seconds"] == 1259 and fc["ends_at"] is not None, fc
    assert fc["clock"] is True, fc


@pytest.mark.parametrize("state", NOT_PROVEN)
def test_the_room_forecast_clock_is_false_unless_proven(clocked, state):
    _history(clocked)
    clocked["status"]["now"] = state
    _running(clocked)
    fc = _round_forecast(clocked)
    # ההערכה עצמה (שניות) נשארת -- הקונסולה מציגה "כ-N דק'"; רק האישור לצייר שעה חסר
    assert fc["seconds"] == 1259 and fc["clock"] is False, fc


def test_an_open_wave_carries_the_clock_too(clocked):
    _history(clocked)
    _open(clocked, 4)                                     # גל פתוח: "לפי היסטוריה: כ-X"
    fc = _round_forecast(clocked)
    assert fc["seconds"] == 1259 and fc["ends_at"] is None and fc["clock"] is True, fc
    clocked["status"]["now"] = {"synchronized": False}
    _expire(clocked)
    assert _round_forecast(clocked)["clock"] is False


def test_a_wave_without_history_carries_the_clock_too(clocked):
    clocked["status"]["now"] = {"synchronized": False}
    _running(clocked)
    fc = _round_forecast(clocked)
    assert fc["seconds"] is None and fc["why"] == "no_history" and fc["clock"] is False, fc


def test_the_console_and_the_hello_share_one_clock_reading(clocked):
    """אותה ראיה: `session.forecast.clock` (hello) ו-`round.forecast.clock` (קונסולה) באים מאותו
    `ClockTrust` -- שני קוראים אינם שתי קריאות chronyc, וגם אינם יכולים להחליט אחרת."""
    _history(clocked)
    hello = _running(clocked)["session"]["forecast"]
    seen = clocked["status"]["calls"]
    for _ in range(3):
        assert _round_forecast(clocked)["clock"] is hello["clock"] is True
    assert clocked["status"]["calls"] == seen >= 1


# --- משיכה וקליטה -------------------------------------------------------------


def test_a_running_pull_carries_the_clock(clocked):
    _running_pull(clocked)
    (p,) = clocked["admin"].get("/api/console/overview").json()["pulls"]
    assert p["forecast"]["why"] == "no_history" and p["forecast"]["clock"] is True, p["forecast"]
    clocked["status"]["now"] = {"synchronized": False}
    _expire(clocked)
    (p,) = clocked["admin"].get("/api/console/overview").json()["pulls"]
    assert p["forecast"]["clock"] is False, p["forecast"]


def test_a_running_capture_carries_the_clock_and_a_done_one_stays_none(clocked):
    _capture_task(clocked, "t_run", "running")
    _capture_task(clocked, "t_done", "done")
    got = {t["id"]: t for t in clocked["admin"].get("/api/console/tasks").json()}
    assert got["t_run"]["forecast"]["why"] == "size_unknown"
    assert got["t_run"]["forecast"]["clock"] is True
    assert got["t_done"]["forecast"] is None             # אין הערכה -- אין מה לסמן
    clocked["status"]["now"] = RuntimeError("chronyc is not installed")
    _expire(clocked)
    got = {t["id"]: t for t in clocked["admin"].get("/api/console/tasks").json()}
    assert got["t_run"]["forecast"]["clock"] is False


# --- with_clock: בלי ראיה, אין שעה --------------------------------------------


class _Trust:
    def __init__(self, answer):
        self._answer = answer

    def ok(self):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


@pytest.mark.parametrize("trust, expected", [
    (_Trust(True), True),
    (_Trust(False), False),
    (_Trust(1), False),                                   # truthy אינו "True" -- ראיה חיובית בלבד
    (_Trust(None), False),
    (_Trust(RuntimeError("boom")), False),                # מדידה שנכשלה היא "לא הוכח", לא 500
    (None, False),                                        # ctx בלי ClockTrust (בדיקות, שרת בלי הוק)
])
def test_with_clock_is_true_only_on_positive_evidence(trust, expected):
    src = {"seconds": 600, "ends_at": "2026-10-21T10:10:00+00:00", "why": None}
    got = learn_eta.with_clock(src, trust)
    assert got == {**src, "clock": expected}, got
    assert "clock" not in src                              # לא משנה את הקלט


def test_with_clock_leaves_no_forecast_as_none():
    assert learn_eta.with_clock(None, _Trust(True)) is None
