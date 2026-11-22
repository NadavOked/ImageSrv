"""#1255: פאנל "משימות אחרונות" — התחלה/משך/יוזם היו "-" קבוע בקוד כי השרת לא החזיר יוזם.

חוזה: `GET /api/console/tasks` מחזיר `created_by` (המשתמש שיצר) ו-`created_by_role` (admin/deploy);
ה-session ב-`/overview` מחזיר `opened_by`, `opened_by_role`, `created_at`, `started_at`, `closed_at`.
`None` = השרת לא יודע (משתמש שנמחק) — לא מחרוזת ריקה ולא "-".
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_capture import make_task, setup_build_machine  # noqa: E402


def _task_rows(server):
    return {t["id"]: t for t in server["admin"].get("/api/console/tasks").json()}


def test_tasks_return_who_created_them_and_their_role(server):
    mac = setup_build_machine(server)
    made = make_task(server, mac)
    assert made.status_code == 200, made.text
    row = _task_rows(server)[made.json()["id"]]
    assert row["created_by"] == "noc"            # הפיקסטורה של האדמין ב-conftest
    assert row["created_by_role"] == "admin"
    assert row["created_at"], "התחלה — ממשיך להגיע מ-created_at"


def test_a_deleted_creator_is_unknown_not_blank(server):
    mac = setup_build_machine(server)
    task_id = make_task(server, mac).json()["id"]
    ctx = server["ctx"]
    ctx.conn.execute("UPDATE tasks SET created_by = 'ghost' WHERE id = ?", (task_id,))
    ctx.conn.commit()
    row = _task_rows(server)[task_id]
    assert row["created_by"] == "ghost"
    assert row["created_by_role"] is None, "אין רשומת משתמש → 'לא ידוע' בקונסולה, לא תפקיד מומצא"


def _insert_running_session(ctx):
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
        " opened_by, created_at, last_join_at, started_at, kind)"
        " VALUES ('s1', 'grp_BUILD', 'img_x', 'P', 1, 60, 'running', 'noc', '2026-10-10T13:00:00', 0,"
        " '2026-10-10T13:01:00', 'multicast')")
    ctx.conn.commit()


def test_overview_session_carries_start_end_and_opener(server):
    _insert_running_session(server["ctx"])
    sess = server["admin"].get("/api/console/overview").json()["session"]
    assert sess["opened_by"] == "noc" and sess["opened_by_role"] == "admin"
    assert sess["created_at"] == "2026-10-10T13:00:00" and sess["started_at"] == "2026-10-10T13:01:00"
    assert sess["closed_at"] is None


def test_overview_pulls_carry_the_opener_too(server):
    ctx = server["ctx"]
    ctx.conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
        " opened_by, created_at, last_join_at, kind)"
        " VALUES ('p1', 'grp_BUILD', 'img_x', 'P', 1, 60, 'open', 'labtech', '2026-10-10T13:00:00', 0, 'unicast')")
    ctx.conn.commit()
    pulls = server["admin"].get("/api/console/overview").json()["pulls"]
    assert pulls and pulls[0]["opened_by"] == "labtech" and pulls[0]["opened_by_role"] == "deploy"


def test_the_unauthenticated_agent_route_does_not_leak_who_opened_the_round(server):
    """`/api/v1/agent/sessions/active` מוגש לסוכן ב-7712 בלי הרשאת קונסולה (וחולק את `build()` עם הקונסולה):
    שם המשתמש שפתח את הסבב ותפקידו לא יוצאים לשם. המידע הזה רק בתשובת הקונסולה המאומתת."""
    _insert_running_session(server["ctx"])
    anon = server["anon"].get("/api/v1/agent/sessions/active")
    assert anon.status_code == 200
    view = anon.json()["session"]
    assert view is not None and view["id"] == "s1", "הבדיקה אמיתית: הסבב נראה לסוכן"
    for leaked in ("opened_by", "opened_by_role", "created_at", "started_at", "closed_at"):
        assert leaked not in view, leaked
    assert "noc" not in anon.text
    assert server["admin"].get("/api/console/overview").json()["session"]["opened_by"] == "noc"
