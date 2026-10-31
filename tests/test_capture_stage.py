"""‏#980 §4, §6 — מה שכרטיס מחשב הבנייה וכרטיס "קליטה בתהליך" צריכים מהשרת.

§4 **שלב הקליטה**: הסוכן כותב את השלב שהוא בו (‏`stage`) בדיווח ההתקדמות של
המשימה; השרת שומר אותו ואת הרגע שבו נכנס אליו (‏`stage_started_at`) ומחזיר
ב-`/api/console/tasks`. ערך שאינו ברשימה, או דיווח בלי השדה (סוכן ישן) —
אינו מוחק שלב שכבר דווח, ואינו ממציא שלב (עיקרון 5א: לא מנחשים מ-bytes).

§6 **מצב הדיסק לפני קליטה**: ‏"בשימוש" מהקליטה האחרונה שהסתיימה מהמחשב
(‏`last_capture` ב-`/api/console/machines`), ו-NTFS מהובר/dirty מבדיקת המכונה
(‏`probe.ntfs`, ‏#1049). "לא נקלט מעולם", "האימג' כבר לא בספרייה" ו"לא נמדד"
הם שלושה ערכים שונים.
"""

from __future__ import annotations

import json
import shutil

import pytest

from server.core.tasks import TOKEN_HEADER
from test_capture import do_capture, make_task, manifest_for, setup_build_machine, task_token

pytest.importorskip("fastapi")


def _report(server, task_id, mac, **extra):
    body = {"task_id": task_id, "mac": mac, "state": "capturing",
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 0, "state": "capturing"}],
            **extra}
    r = server["anon"].post("/api/v1/agent/progress", json=body,
                            headers={TOKEN_HEADER: task_token(server, task_id)})
    assert r.status_code == 200 and r.json()["ok"], r.text


def _task(server, task_id):
    return next(t for t in server["admin"].get("/api/console/tasks").json() if t["id"] == task_id)


def _machine(server, mac):
    return next(m for m in server["admin"].get("/api/console/machines").json() if m["mac"] == mac)


# --- §4: השלב --------------------------------------------------------------


def test_a_new_task_has_no_stage(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    task = _task(server, tid)
    assert task["stage"] is None and task["stage_started_at"] is None


def test_the_stage_the_agent_reports_reaches_the_console_with_its_start(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    _report(server, tid, mac, stage="shrink")
    task = _task(server, tid)
    assert task["stage"] == "shrink" and task["stage_started_at"]


def test_the_same_stage_again_keeps_its_start_and_a_new_one_moves_it(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    _report(server, tid, mac, stage="shrink")
    conn = server["ctx"].conn
    conn.execute("UPDATE tasks SET stage_started_at = '2026-10-11T08:00:00+00:00' WHERE id = ?", (tid,))
    conn.commit()
    _report(server, tid, mac, stage="shrink")
    assert _task(server, tid)["stage_started_at"] == "2026-10-11T08:00:00+00:00"
    _report(server, tid, mac, stage="stream")
    task = _task(server, tid)
    assert task["stage"] == "stream" and task["stage_started_at"] != "2026-10-11T08:00:00+00:00"


@pytest.mark.parametrize("extra", [{}, {"stage": "dance"}, {"stage": 3}, {"stage": None}])
def test_a_missing_or_unknown_stage_keeps_the_last_one(server, extra):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    _report(server, tid, mac, stage="verify")
    _report(server, tid, mac, **extra)
    assert _task(server, tid)["stage"] == "verify"


def test_an_old_agent_that_never_sends_a_stage_leaves_it_null(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    _report(server, tid, mac)
    task = _task(server, tid)
    assert task["state"] == "running" and task["stage"] is None


# --- §6: הקליטה האחרונה ------------------------------------------------------


def test_a_machine_never_captured_has_no_last_capture(server):
    mac = setup_build_machine(server)
    assert _machine(server, mac)["last_capture"] is None


def test_the_last_finished_capture_brings_its_used_bytes(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    assert do_capture(server, tid).status_code == 202
    # קליטה מאוחרת שנכשלה אינה "הקליטה האחרונה" — אין לה מניפסט.
    failed = make_task(server, mac, name="אחרת").json()["id"]
    _report(server, failed, mac, state="failed")
    last = _machine(server, mac)["last_capture"]
    row = server["ctx"].conn.execute("SELECT image_id, updated_at FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert last == {"task_id": tid, "image_id": row["image_id"], "name": "Windows 11 Base",
                    "at": row["updated_at"], "in_library": True,
                    "used_bytes": 31457280 + 84509376512}


def test_an_image_no_longer_in_the_library_says_so(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    assert do_capture(server, tid).status_code == 202
    image_id = server["ctx"].conn.execute("SELECT image_id FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
    shutil.rmtree(server["ctx"].library.root / image_id)
    last = _machine(server, mac)["last_capture"]
    assert last["in_library"] is False and last["used_bytes"] is None


def test_an_unmeasured_partition_is_unknown_not_a_partial_sum(server):
    mac = setup_build_machine(server)
    tid = make_task(server, mac).json()["id"]
    manifest = manifest_for()
    manifest["partitions"][1]["used_bytes"] = None
    assert do_capture(server, tid, manifest=manifest).status_code == 202
    last = _machine(server, mac)["last_capture"]
    assert last["in_library"] is True and last["used_bytes"] is None


# --- §6: NTFS מבדיקת המכונה ---------------------------------------------------


def test_the_ntfs_state_in_the_probe_is_kept(server):
    from conftest import hello_body

    mac = setup_build_machine(server)
    body = hello_body(mac)
    body["probe"] = {"ntfs": [{"node": "/dev/sda3", "state": "hibernated"},
                              {"node": "/dev/sda4", "error": "ntfs-3g.probe rc 21"}]}
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    probe = _machine(server, mac)["probe"]
    assert json.dumps(probe["ntfs"], sort_keys=True) == json.dumps(
        [{"node": "/dev/sda3", "state": "hibernated"},
         {"error": "ntfs-3g.probe rc 21", "node": "/dev/sda4"}], sort_keys=True)
