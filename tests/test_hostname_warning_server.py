"""‏#856, צד השרת: יעד `done` עם `error` (שם המחשב לא נכתב) נשמר ומוצג.

הסוכן מדווח `done` עם `error` על היעד (סעיף 4) — אותו שדה שבו "done
(grow deferred)" מגיע מאז #648. השרת אינו מסווג: הוא שומר את הטקסט
ב-`session_members.error`, מדליק `done` (השחזור הושלם), ומחזיר אותו
לקונסולה ב-`overview.session.members[].error`. הבדיקה מוכיחה שהמסלול
הזה אכן קיים לדיווח `done` — ולא רק ל-`failed` — כי זה מה ש-#856 נשען עליו.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_closed_session_report import _session
from server.agent import reports

WARNING = "שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה"


def _done_with_warning(sid: str, mac: str) -> dict:
    return {"session_id": sid, "mac": mac, "state": "done",
            "targets": [{"dev": "sda", "bytes_written": 4096, "bytes_total": 4096,
                         "state": "done", "error": WARNING}]}


def test_a_done_target_with_a_hostname_warning_is_stored_and_counted_done(server):
    mac = _session(server, "ses_hn")
    conn = server["app"].state.ctx.conn
    assert reports.ingest(conn, _done_with_warning("ses_hn", mac))["ok"] is True
    row = conn.execute(
        "SELECT state, done, error FROM session_members WHERE session_id = 'ses_hn'"
    ).fetchone()
    assert row["state"] == "done" and row["done"] == 1      # השחזור הושלם
    assert row["error"] == f"sda: {WARNING}"                  # והאזהרה לא נבלעה
    # ביומן — עם המעבר הטרמינלי, פעם אחת, עם הסיבה.
    journal = conn.execute(
        "SELECT detail FROM journal WHERE event = 'client_done'").fetchall()
    assert any(WARNING in r["detail"] for r in journal), [dict(r) for r in journal]


def test_the_warning_reaches_the_console_next_to_the_member(server):
    mac = _session(server, "ses_hn2", state="running")
    conn = server["app"].state.ctx.conn
    reports.ingest(conn, _done_with_warning("ses_hn2", mac))
    overview = server["admin"].get("/api/console/overview").json()
    (member,) = [m for m in overview["session"]["members"] if m["mac"] == mac]
    assert member["state"] == "done"
    assert WARNING in member["error"]


def test_a_clean_done_carries_no_error(server):
    """רדיוס הפגיעה: `done` בלי `error` נשאר בלי `error` — לא מחרוזת ריקה."""
    mac = _session(server, "ses_hn3")
    conn = server["app"].state.ctx.conn
    payload = _done_with_warning("ses_hn3", mac)
    del payload["targets"][0]["error"]
    reports.ingest(conn, payload)
    row = conn.execute(
        "SELECT done, error FROM session_members WHERE session_id = 'ses_hn3'").fetchone()
    assert row["done"] == 1 and row["error"] is None
