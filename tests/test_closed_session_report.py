"""‏#557 — סבב שנסגר אינו מקבל דיווחים, והסוכן מפסיק לדווח עליו.

**נמדד על ברזל, 08/09/2026.** הסבב `ses_277091d3` נסגר ב-07:36:21.
ב-08:25 — **‏50 דקות אחר כך** — שתי מכונות עדיין שלחו
`POST /api/v1/agent/progress` עליו, כל שנייה, אלפי פעמים. השרת ענה
`200 OK` בכל אחת.

⚠️ **והמחיר לא היה רעש:** הן לא הצטרפו לגל הבא. הסבב החדש נשאר
פתוח וריק, ‏`udp-sender` לא רץ, **וההפצה נעצרה** עד אתחול ידני.

זהו בדיוק הפגם ש-#535 תיקן **למשימות**. מסלול הסבב לא קיבל את אותו
טיפול — ולכן הטסטים כאן בודקים את שני הצדדים: שהשרת מסרב, ושהסוכן
**מקשיב לסירוב**.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from server.agent import reports


def _payload(session_id: str, mac: str, state: str = "writing") -> dict:
    return {"session_id": session_id, "mac": mac, "state": state,
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2,
                         "state": state}]}


def _session(server, sid: str, state: str = "open") -> str:
    """סבב אמיתי עם חבר אמיתי, ומחזיר את ה-MAC.

    ‏**דרך ה-fixture ולא ב-INSERT ידני:** ‏`sessions` נשען על מפתחות
    זרים ל-`groups` ול-`images`, וטסט שמזריק מזהים מומצאים נופל על
    האילוץ ולא על הקוד שנבדק.
    """
    info = setup_classroom(server)
    conn = server["app"].state.ctx.conn
    # ⚠️ **אין טבלת `images`** — הדיסק הוא מקור האמת לאימג'ים (עיקרון 3),
    # וה-DB מחזיק רק מה שאין לו ייצוג טבעי כקבצים. המזהה נלקח מהספרייה.
    image_id = server["admin"].get("/api/console/images").json()[0]["id"]
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, ?, ?, 'ROOM', 2, 300, ?, 'nadav', ?, 0.0, 'multicast')",
        (sid, info["group"], image_id, state, "2026-09-08T00:00:00+00:00"))
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, done,"
        " bytes_written, bytes_total, updated_at)"
        " VALUES (?, ?, 'writing', 0, 0, 0, ?)",
        (sid, info["mac1"], "2026-09-08T00:00:00+00:00"))
    conn.commit()
    return info["mac1"]


# --- צד השרת -----------------------------------------------------------------


def test_a_report_to_an_open_session_is_accepted(server):
    """רדיוס הפגיעה. בלי זה התיקון היה חוסם גם את המסלול התקין."""
    mac = _session(server, "ses_open")
    conn = server["app"].state.ctx.conn
    assert reports.ingest(conn, _payload("ses_open", mac))["ok"] is True


def test_a_report_to_a_closed_session_is_refused(server):
    """**הפגם עצמו.** עד #557 זה החזיר `{"ok": True}`."""
    mac = _session(server, "ses_shut", state="closed")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, _payload("ses_shut", mac))
    assert answer["ok"] is False, answer
    assert answer["code"] == "not_open", answer


def test_a_report_to_a_session_that_never_existed_is_refused(server):
    """מזהה שהומצא אינו "לא חבר" — הסבב עצמו איננו."""
    mac = _session(server, "ses_real")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, _payload("ses_ghost", mac))
    assert answer["code"] == "not_open", answer


def test_the_refusal_is_journalled(server):
    """בלי רישום, אלפי הבקשות של 08/09 היו נשארות בלתי-נראות."""
    mac = _session(server, "ses_shut2", state="closed")
    conn = server["app"].state.ctx.conn
    reports.ingest(conn, _payload("ses_shut2", mac))
    events = [e["event"] for e in
              server["admin"].get("/api/console/journal").json()]
    assert "report_on_closed_session" in events, events


def test_a_closed_session_is_not_modified_by_the_refused_report(server):
    """הסירוב אינו רק תשובה — הוא גם אי-כתיבה.

    דיווח `done` על סבב סגור **אסור** שיסמן חבר כגמור: זה היה
    מזייף השלמה שלא קרתה.
    """
    mac = _session(server, "ses_shut3", state="closed")
    conn = server["app"].state.ctx.conn
    reports.ingest(conn, _payload("ses_shut3", mac, state="done"))
    row = conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ?",
        ("ses_shut3",)).fetchone()
    assert row["state"] == "writing", dict(row)
    assert row["done"] == 0, dict(row)


@pytest.mark.parametrize("exists", [True, False])
def test_not_open_body_survives_curl_fail_http_contract(server, exists):
    """curl -f suppresses 4xx bodies: ingest-only tests missed this regression."""
    mac = _session(server, "ses_http_closed", state="closed")
    sid = "ses_http_closed" if exists else "ses_http_missing"
    response = server["anon"].post(
        "/api/v1/agent/progress", json=_payload(sid, mac))
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "not_open"
    assert response.json()["ok"] is False


def test_invalid_progress_still_returns_http_400(server):
    response = server["anon"].post("/api/v1/agent/progress", json={})
    assert response.status_code == 400
    assert response.json()["ok"] is False


# --- #771: דיווח טרמינלי הוא בלתי-משתנה -------------------------------------


def test_a_duplicate_done_does_not_erase_targets_json(server):
    """**לב #771.** חבר שכבר `done` עם יעד שהושלם, ואז חוזר על `done`
    עם `targets=[]` — הדיווח השני אסור שימחק את targets_json.

    בלי השומר: ה-UPDATE דורס targets_json ל-`"[]"`, ‏`_tally` סופר 0
    מגירות שהושלמו, ומתחיל גל שיכפול נוסף — re-image של מכונה שסיימה.
    """
    mac = _session(server, "ses_dupdone")
    conn = server["app"].state.ctx.conn

    first = reports.ingest(conn, {
        "session_id": "ses_dupdone", "mac": mac, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 40960,
                     "bytes_total": 40960, "state": "done"}]})
    assert first["ok"] is True
    before = conn.execute(
        "SELECT state, done, targets_json FROM session_members"
        " WHERE session_id = 'ses_dupdone'").fetchone()
    assert before["state"] == "done" and before["done"] == 1
    assert "sda" in before["targets_json"]

    dup = reports.ingest(conn, {
        "session_id": "ses_dupdone", "mac": mac, "state": "done", "targets": []})
    assert dup["ok"] is True, dup
    after = conn.execute(
        "SELECT state, done, targets_json FROM session_members"
        " WHERE session_id = 'ses_dupdone'").fetchone()
    assert after["targets_json"] == before["targets_json"], (
        "done כפול מחק את targets_json → _tally יספור 0 → גל שיכפול נוסף")
    assert after["state"] == "done" and after["done"] == 1


def test_a_late_writing_does_not_regress_a_done_member(server):
    """הזרוע השנייה של #771: דיווח `writing` מאוחר אחרי `done` אסור
    שיחזיר את החבר ל-לא-טרמינלי — אחרת `_spent` לא רואה אותו גמור
    והגל נתקע."""
    mac = _session(server, "ses_regress")
    conn = server["app"].state.ctx.conn
    assert reports.ingest(conn, {
        "session_id": "ses_regress", "mac": mac, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 40960,
                     "bytes_total": 40960, "state": "done"}]})["ok"] is True

    late = reports.ingest(conn, {
        "session_id": "ses_regress", "mac": mac, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 100,
                     "bytes_total": 40960, "state": "writing"}]})
    assert late["ok"] is True, late
    row = conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = 'ses_regress'"
    ).fetchone()
    assert row["state"] == "done", "writing מאוחר הרגיס done → הגל נתקע"
    assert row["done"] == 1


# --- #772: מצב לא-מוכר נדחה, לא נכתב בשקט ------------------------------------


def test_a_report_without_a_state_is_refused(server):
    """**#772.** דיווח עם mac/targets תקינים אך בלי `state` נדחה
    (`bad_state`), ואינו כותב `""` למצב החבר — אחרת החבר לעולם אינו
    טרמינלי וה-tick נתקע."""
    mac = _session(server, "ses_nostate")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, {
        "session_id": "ses_nostate", "mac": mac,
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2}]})
    assert answer["ok"] is False, answer
    assert answer["code"] == "bad_state", answer
    row = conn.execute(
        "SELECT state FROM session_members WHERE session_id = 'ses_nostate'"
    ).fetchone()
    assert row["state"] == "writing", dict(row)     # לא נכתב '' מה-fixture


def test_an_unknown_state_is_refused(server):
    """ערך שרירותי הוא אותו פגם כמו state ריק."""
    mac = _session(server, "ses_badstate")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, _payload("ses_badstate", mac, state="zombie"))
    assert answer["ok"] is False, answer
    assert answer["code"] == "bad_state", answer


def test_an_unknown_state_returns_http_400(server):
    """המסלול המלא: `bad_state` ממופה ל-400, כמו כל דחיית קלט."""
    mac = _session(server, "ses_badstate_http")
    response = server["anon"].post("/api/v1/agent/progress", json={
        "session_id": "ses_badstate_http", "mac": mac, "state": "",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2}]})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "bad_state"


def test_legitimate_progress_states_are_still_accepted(server):
    """רדיוס הפגיעה: המצבים החוקיים שאינם טרמינליים עדיין עוברים."""
    mac = _session(server, "ses_okstates")
    conn = server["app"].state.ctx.conn
    for good in ("writing", "verifying", "naming"):
        assert reports.ingest(
            conn, _payload("ses_okstates", mac, state=good))["ok"] is True, good
