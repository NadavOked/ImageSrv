"""‏#2657: מה קורה היום כשמשמיעים מחדש בקשה ישנה של הסוכן — בזרימה, לא לפי שמות.

הטסטים כאן אינם מתקנים דבר. הם מקבעים את ההתנהגות הנוכחית של **השרת**
מול השמעה חוזרת של בקשה זהה בייט-בבייט (אותו גוף, אותו אסימון), כפי
שמסמך מודל האמון (`docs/architecture/security-model.md`, סעיף "השמעה
חוזרת") מתאר אותה:

* **משימה או סבב שנסגרו** — ההשמעה נדחית בשמה (`not_open`) ולא משנה דבר.
  זה מצב המשימה בשרת (#535, ‏#557), לא מנגנון ייעודי נגד השמעה.
* **חבר סבב שסיים** — מצב טרמינלי אינו זז (#771), וה-hello הבא אינו מקבל
  את הסבב שוב.
* **משימה פתוחה** — האסימון הוא סוד נושא (bearer): השמעה של הדיווח של
  המכונה עצמה **מתקבלת**. זה הפער שמסמך מודל האמון מפנה בו ל-#1076
  (ערוץ מוצפן) ול-#2657. כשהוא ייסגר, הטסט האחרון כאן אמור להתהפך.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.core.tasks import TOKEN_HEADER
from test_capture import do_capture, make_task, manifest_for, setup_build_machine, task_token
from test_report_mac_forms import hello, open_session


def _send(server, raw: bytes, headers: dict):
    return server["anon"].post("/api/v1/agent/progress", content=raw,
                               headers={"Content-Type": "application/json", **headers})


def _task_state(server, task_id):
    row = server["ctx"].conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return row["state"]


def _events(server, name):
    return server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = ?", (name,)).fetchone()["n"]


def test_a_replayed_capture_report_after_the_task_closed_changes_nothing(server):
    """אותו גוף ואותו אסימון שעברו בזמן שהמשימה רצה, אחרי שנסגרה ב-`done`."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    head = {TOKEN_HEADER: task_token(server, task_id)}
    raw = json.dumps({"task_id": task_id, "mac": mac, "state": "failed",
                      "targets": [{"dev": "sda", "error": "replayed"}]}).encode()

    assert do_capture(server, task_id).status_code == 202
    assert _task_state(server, task_id) == "done"

    r = _send(server, raw, head)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "not_open", r.json()
    assert _task_state(server, task_id) == "done"
    assert _events(server, "capture_failed") == 0


def test_a_replayed_upload_after_the_task_closed_does_not_publish_again(server, images_root):
    """ההעלאה של המניפסט, מושמעת שוב עם האסימון המקורי: אין אימג' שני."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    token = task_token(server, task_id)
    assert do_capture(server, task_id).status_code == 202
    folders = sorted(p.name for p in images_root.iterdir() if p.is_dir())
    assert folders, "the first, genuine upload published nothing -- the setup is broken"

    r = server["anon"].put(f"/api/v1/capture/{task_id}/manifest",
                           content=json.dumps(manifest_for()).encode(),
                           headers={TOKEN_HEADER: token})
    assert r.status_code == 404, r.text   # "no such open task" (capture.task_for)
    assert sorted(p.name for p in images_root.iterdir() if p.is_dir()) == folders


def test_a_replayed_round_report_on_a_closed_round_is_refused(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    hello(server, ids["mac1"])
    raw = json.dumps({"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
                      "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2,
                                   "state": "writing"}]}).encode()
    assert _send(server, raw, {}).json()["ok"] is True
    assert server["deploy"].post(f"/api/console/sessions/{ids['session']}/close",
                                 json={"confirm_name": "Office 2024 Standard"}).status_code == 200

    r = _send(server, raw, {})
    assert r.json()["code"] == "not_open", r.json()


def test_a_replayed_progress_report_does_not_undo_done_and_the_round_is_not_offered_again(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    hello(server, ids["mac1"])
    writing = json.dumps({"session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
                          "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 100,
                                       "state": "writing"}]}).encode()
    done = json.dumps({"session_id": ids["session"], "mac": ids["mac1"], "state": "done",
                       "targets": [{"dev": "sda", "bytes_written": 100, "bytes_total": 100,
                                    "state": "done"}]}).encode()
    assert _send(server, writing, {}).json()["ok"] is True
    assert _send(server, done, {}).json()["ok"] is True

    assert _send(server, writing, {}).json() == {"ok": True}   # idempotent, לא נסיגה
    row = server["ctx"].conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (ids["session"], ids["mac1"])).fetchone()
    assert (row["state"], row["done"]) == ("done", 1)
    again = hello(server, ids["mac1"])
    assert again["session"] is None and again["task"] is None


def test_today_a_replayed_report_inside_an_open_task_is_accepted(server):
    """**הפער, כפי שהוא היום.** האסימון אינו קשור לבקשה אחת ואינו מתיישן
    בזמן שהמשימה פתוחה, והערוץ אינו מוצפן — ולכן השמעה של דיווח זהה מתקבלת.
    זה מתועד במסמך מודל האמון ונסגר ב-#1076/#2657; אז הטסט הזה יתהפך."""
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    raw = json.dumps({"task_id": task_id, "mac": mac, "state": "writing",
                      "targets": [{"dev": "sda", "bytes_written": 5, "bytes_total": 10}]}).encode()
    head = {TOKEN_HEADER: task_token(server, task_id)}

    assert _send(server, raw, head).json() == {"ok": True}
    assert _send(server, raw, head).json() == {"ok": True}
    assert _task_state(server, task_id) == "running"
