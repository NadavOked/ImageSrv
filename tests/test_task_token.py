"""‏#530/#532/#535 — ‏`/api/v1/agent/*` מקבל זהות.

עד כאן ל-API של הסוכן **לא הייתה שכבת זהות בכלל**. ה-docstring של
``create_agent_capture_router`` הצהיר *"הרשאה: ה-MAC חייב להיות בעל
המשימה"*, ‏``task_for(task_id, request)`` קיבל את ``request``
**ולא נגע בו**, ולא היה ``Depends`` על הראוטר.

וה-``task_id`` הוא ``token_hex(2)`` — **‏65,536 ערכים**. כלומר הוא
לא רק חסר-אימות, הוא גם ניתן לניחוש.

**שלושה פגמים על אותו משטח, ואסימון אחד סוגר את כולם:**

* ‏#530 — פונה זר העלה מחיצות ומניפסט, והשרת פרסם אותם כאימג'.
  ואימות ה-sha אינו תופס: המניפסט מגיע **מאותו מעלה**.
* ‏#532 — העלאה שהסתיימה אחרי ביטול החזירה את המשימה ל-``running``.
* ‏#535 — דיווח ``failed`` הרג משימה חיה, בלי אימות ובלי בדיקת מצב.

**האסימון נמסר רק ב-hello**, ורק למכונה שה-MAC שלה תואם. הוא
**אינו** מוחזר ביצירת המשימה — שם רואה אותו הקונסולה, וזה בדיוק
מה שלא צריך לקרות.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from server.core.tasks import TOKEN_HEADER
from test_capture import (
    PART_A, do_capture, make_task, manifest_for, setup_build_machine, task_token,
)


# --- #530: מי מורשה לכתוב על המשימה -----------------------------------------


def test_the_token_reaches_the_machine_only_through_hello(server):
    """הסוכן מקבל אותו בתשובה; הקונסולה לא רואה אותו ביצירה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    assert "token" not in created, created

    answer = server["anon"].post("/api/v1/agent/hello",
                                 json=hello_body(mac)).json()
    assert answer["task"]["token"] == task_token(server, created["id"])
    assert len(answer["task"]["token"]) == 48, "‏192 סיביות, לא פחות"


def test_an_upload_without_a_token_is_refused(server, images_root):
    """זה בדיוק המצב שהיה עד #530 — וכל פונה יכול היה להיות בו."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].put(
        f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst", content=PART_A)
    assert r.status_code == 401, r.text
    assert not list(images_root.glob(".capture-*")), "נכתב קובץ בלי אסימון"


def test_an_upload_with_the_wrong_token_is_refused_and_journalled(server, images_root):
    """‏403 ולא 404: המשימה קיימת, והפונה אינו בעליה.

    ההבחנה חשובה — ‏404 אחיד היה נכון מבחינת דליפת מידע, אבל כאן
    ‏`task_id` ממילא ניתן לניחוש, ומה שחסר הוא **עקבות**: היומן הוא
    מה שיאפשר לראות שמישהו ניסה.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].put(
        f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
        content=PART_A, headers={TOKEN_HEADER: "0" * 48})
    assert r.status_code == 403, r.text
    assert not list(images_root.glob(".capture-*"))

    events = [e["event"] for e in server["admin"].get("/api/console/journal").json()]
    assert "task_token_refused" in events, events


def test_a_manifest_with_the_wrong_token_does_not_publish_an_image(server, images_root):
    """התרחיש של #530 מקצה לקצה: אימג' מוחלף אינו נכנס לספרייה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = do_capture(server, created["id"], token="f" * 48)
    assert r.status_code == 403, r.text

    listed = {m["id"] for m in server["admin"].get("/api/console/images").json()}
    assert created["image_id"] not in listed


def test_a_token_from_another_task_does_not_open_this_one(server):
    """אסימון תקף לחלוטין — של משימה אחרת. עדיין 403."""
    other_mac = setup_build_machine(server, "aa:bb:cc:00:00:11")
    mine_mac = setup_build_machine(server, "aa:bb:cc:00:00:12")
    other = make_task(server, other_mac).json()
    mine = make_task(server, mine_mac).json()

    r = server["anon"].put(
        f"/api/v1/capture/{mine['id']}/files/p1.esp.pcl.zst", content=PART_A,
        headers={TOKEN_HEADER: task_token(server, other["id"])})
    assert r.status_code == 403, r.text


# --- #532: ביטול שנשאר מבוטל ------------------------------------------------


def test_a_cancellation_before_the_upload_is_refused_at_the_gate(server):
    """המסלול הקל: ‏`task_for` עוצר משימה שאינה פתוחה.

    ⚠️ **הטסט הזה אינו הבקרה ל-#532.** הוא עובר גם בלי תנאי המצב
    ב-``UPDATE``, כי הבקשה נעצרת עוד קודם. הבא אחריו הוא הבקרה.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    token = task_token(server, created["id"])
    assert server["admin"].post(
        f"/api/console/tasks/{created['id']}/cancel").status_code == 200

    r = server["anon"].put(
        f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
        content=PART_A, headers={TOKEN_HEADER: token})
    assert r.status_code == 404, r.text


def test_a_cancellation_during_the_upload_is_not_undone(server, images_root):
    """**התרחיש האמיתי של #532**, וזו הבקרה השלילית שלו.

    ‏`task_for` בודק מצב **לפני** ההזרמה, והזרמה של מחיצה נמשכת
    דקות. הביטול קורה **בזמן** שהבייטים זורמים — ואז השורה
    שאחרי ההזרמה, ``SET state='running' WHERE id=?`` בלי תנאי,
    החזירה את המשימה לחיים.

    הביטול מוזרק מתוך גוף הבקשה: הגנרטור מוסר נתח, מבטל, ואז
    מוסר את השאר. זה מייצר את החלון בלי תהליכונים ובלי תזמון —
    ‏`test_hold_and_capture` כבר לימד ש**טסט תלוי-שעון נכשל על
    מכונה עמוסה ולא על הקוד**.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    token = task_token(server, created["id"])

    def body_that_cancels_midway():
        yield PART_A[:8]
        server["admin"].post(f"/api/console/tasks/{created['id']}/cancel")
        yield PART_A[8:]

    r = server["anon"].put(
        f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
        content=body_that_cancels_midway(), headers={TOKEN_HEADER: token})
    assert r.status_code == 409, r.text

    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    assert tasks[created["id"]]["state"] == "cancelled", "הביטול בוטל"
    # והקובץ החלקי אינו נשאר: ‏staging של משימה מבוטלת כבר נמחק,
    # וקובץ יתום שם היה נכנס לספרייה בקליטה הבאה עם אותו מזהה.
    leftovers = list(images_root.glob(".capture-*/p1.esp.pcl.zst"))
    assert not leftovers, leftovers


def test_a_cancel_that_removed_the_staging_folder_is_409_not_500(server, images_root, monkeypatch, tmp_path):
    """#1470 + #532, on every platform: on Linux the cancel above removes the
    staging folder while the stream still writes into the open handle, so the
    sha record #1470 writes beside the file has no folder left
    (FileNotFoundError). That was a 500 before the task-state check could say
    409 -- the Testrunner caught it, Windows cannot (an open file pins its
    folder). Here the record's folder is simply missing."""
    import server.library.capture as capture
    gone = tmp_path / "removed-staging"
    monkeypatch.setattr(capture, "upload_sha_path", lambda path: gone / (path.name + ".sha256"))
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    token = task_token(server, created["id"])

    def body_that_cancels_midway():
        yield PART_A[:8]
        server["admin"].post(f"/api/console/tasks/{created['id']}/cancel")
        yield PART_A[8:]

    r = server["anon"].put(
        f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
        content=body_that_cancels_midway(), headers={TOKEN_HEADER: token})
    assert r.status_code == 409, r.text
    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    assert tasks[created["id"]]["state"] == "cancelled"


# --- #535: דיווח שאינו הורג משימה סגורה --------------------------------------


def _report(server, task_id, mac, state, token=None):
    """דיווח התקדמות על משימה, עם האסימון שלה — כמו הסוכן האמיתי (#855).

    ``token=""`` שולח בלי כותרת בכלל; ``token="..."`` שולח את מה שניתן.
    """
    head = {TOKEN_HEADER: task_token(server, task_id) if token is None else token}
    if token == "":
        head = {}
    return server["anon"].post("/api/v1/agent/progress", json={
        "task_id": task_id, "mac": mac, "state": state, "targets": []},
        headers=head)


def test_a_failure_report_cannot_kill_a_finished_task(server):
    """התרחיש של #535: ``failed`` על משימה שכבר ``done``.

    ‏`task_id` הוא ``token_hex(2)`` — ניחוש של 65,536 — וה-MAC מתפרסם
    ב-ARP. עד כאן זה הספיק כדי להרוג קליטה.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    assert do_capture(server, created["id"]).status_code == 202

    r = _report(server, created["id"], mac, "failed")
    assert r.json()["code"] == "not_open", r.json()

    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    assert tasks[created["id"]]["state"] == "done", "משימה שהסתיימה שונתה"


def test_a_failure_report_cannot_revive_a_cancelled_task(server):
    """אותו שער, מהצד השני: ``cancelled`` נשאר ``cancelled``."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    server["admin"].post(f"/api/console/tasks/{created['id']}/cancel")

    assert _report(server, created["id"], mac, "failed").json()["code"] == "not_open"
    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    assert tasks[created["id"]]["state"] == "cancelled"


def test_an_open_task_still_accepts_its_reports(server):
    """רדיוס הפגיעה. בלי זה התיקון היה חוסם גם את המסלול התקין."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    assert _report(server, created["id"], mac, "writing").json()["ok"] is True

    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    assert tasks[created["id"]]["state"] == "running"


# --- #855 (פריט 1): גם הדיווח דורש את האסימון, לא רק ההעלאה ------------------
#
# ‏#535 סגר את "דיווח `failed` הורג משימה **סגורה**", ו-#530 סגר את
# ההעלאה. מה שנשאר פתוח: דיווח `failed` על משימה **פתוחה** — בלי
# אסימון, רק `task_id` (65,536 ניחושים) ו-MAC (מתפרסם ב-ARP). מכונה
# אחרת בוילן שולחת אותו באמצע הקליטה, המשימה הופכת `failed`, וההעלאה
# המאומתת של מחשב הבנייה נכשלת "המשימה אינה פתוחה" — שומר-אצל-האח.


def _task_state(server, task_id):
    tasks = {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}
    return tasks[task_id]["state"]


def test_a_capture_report_without_a_token_is_refused(server):
    """**לב #855 פריט 1.** בלי כותרת → 403, המשימה לא זזה, והיומן יודע."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    r = _report(server, created["id"], mac, "failed", token="")
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "bad_token", r.json()
    assert _task_state(server, created["id"]) == "pending", "דיווח זר שינה משימה"

    events = [e["event"] for e in server["admin"].get("/api/console/journal").json()]
    assert "task_token_refused" in events, events
    assert "capture_failed" not in events, events


def test_a_capture_report_with_the_wrong_token_is_refused(server):
    """אסימון שאינו של המשימה — אותו 403, אותו רישום, כמו בהעלאה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    r = _report(server, created["id"], mac, "failed", token="0" * 48)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "bad_token"
    assert _task_state(server, created["id"]) == "pending"


def test_a_token_from_another_task_does_not_report_on_this_one(server):
    """אסימון תקף — של משימה אחרת. עדיין 403, כמו ב-#530."""
    other_mac = setup_build_machine(server, "aa:bb:cc:00:00:11")
    mine_mac = setup_build_machine(server, "aa:bb:cc:00:00:12")
    other = make_task(server, other_mac).json()
    mine = make_task(server, mine_mac).json()

    r = _report(server, mine["id"], mine_mac, "failed",
                token=task_token(server, other["id"]))
    assert r.status_code == 403, r.text
    assert _task_state(server, mine["id"]) == "pending"


def test_the_upload_still_succeeds_after_a_forged_failure_report(server):
    """התרחיש המלא: הדיווח הזר נדחה, וההעלאה האמיתית אחריו מתקבלת."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    assert _report(server, created["id"], mac, "failed", token="").status_code == 403
    assert do_capture(server, created["id"]).status_code == 202
    assert _task_state(server, created["id"]) == "done"


def test_a_closed_task_still_answers_not_open_to_its_own_reporter(server):
    """רדיוס הפגיעה של הסדר: המשימה נסגרת ב-`done` כשהמניפסט מתקבל,
    ואז `report_final` של הסוכן שולח דיווח **עם** האסימון. הוא חייב
    לקבל 200/`not_open` כמו היום — אחרת מחשב הבנייה נעצר עם
    "השרת לא אישר" אחרי קליטה שהצליחה (ממשק 4, ‏#127)."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    token = task_token(server, created["id"])
    assert do_capture(server, created["id"]).status_code == 202

    r = _report(server, created["id"], mac, "done", token=token)
    assert r.status_code == 200, r.text
    assert r.json()["code"] == "not_open"


def test_a_round_report_needs_no_token(server):
    """הסבב (session) אינו נוגע ב-#855 פריט 1 — אין לו אסימון (פריטים
    3–4 ב-Issue, הכרעת תכנון). דיווח סבב בלי כותרת ממשיך כרגיל."""
    from test_report_mac_forms import hello, open_session   # noqa: PLC0415
    ids = open_session(server)
    hello(server, ids["mac1"])
    r = server["anon"].post("/api/v1/agent/progress", json={
        "session_id": ids["session"], "mac": ids["mac1"], "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2,
                     "state": "writing"}]})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
