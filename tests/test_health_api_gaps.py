"""‏#1000 — פערי ה-API של דף "בריאות ושירותים": תוצאת בדיקת-העדכון האחרונה
נשמרת בשרת (`settings.update_last_check`) ומוחזרת ב-`GET /update` כ-
`last_check`; ‏`GET /health` נושא את זמן המדידה בכותרת `X-Health-Checked-At`
— כותרת ולא מעטפת, כי הגוף הוא מערך שכל הקוראים הקיימים תלויים בו.
"""

from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")

from server.system import health
from server import update as update_mod
from server.db import get_setting, set_setting
from test_server_health import health_server              # noqa: F401 — fixture
from test_update import _enable, update_server            # noqa: F401 — fixture

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def test_last_check_is_null_before_any_check(update_server):
    info = update_server["admin"].get("/api/console/update").json()
    assert "last_check" in info and info["last_check"] is None


def test_check_is_recorded_with_the_server_clock_and_survives_a_reload(update_server):
    admin, state = update_server["admin"], update_server["state"]
    _enable(admin)
    state["remote"] = "x\trefs/tags/v0.25.0\n"
    result = admin.post("/api/console/update/check").json()
    assert ISO.match(result["at"]), result
    assert result["current"] == "v0.24.0" and result["latest"] == "v0.25.0"
    assert result["available"] is True and result["reason"] is None
    # אותו מסמך חוזר ב-GET /update — מהשרת, לא מהסשן של הדפדפן.
    info = admin.get("/api/console/update").json()
    assert info["last_check"] == result
    # ומה שנשמר הוא בדיוק המסמך הזה, ב-settings.
    stored = json.loads(get_setting(update_server["app"].state.ctx.conn,
                                    update_mod.LAST_CHECK_KEY))
    assert stored == result


def test_a_failed_check_is_recorded_as_failed_not_as_no_update(update_server):
    admin = update_server["admin"]
    _enable(admin)
    hooks_fail = {"ls_remote_tags": lambda url: (False, "", "no route to github")}
    # מזריקים כשל דרך ה-hooks של הראוטר: הדרך היחידה בלי רשת היא לשנות
    # את ההוק שהוזרק — ולכן בודקים את שכבת ה-service ישירות.
    doc = update_mod.record_last_check(
        update_server["app"].state.ctx.conn,
        update_mod.check_update({"describe": lambda d: "v0.24.0", **hooks_fail},
                                "/repo", "https://example/pub.git"))
    assert doc["available"] is False and doc["latest"] is None
    # ‏#1281: הכשל נרשם ככשל בשם (`failed` — "no route to github" אינו
    # סמן רשת), הפלט הגולמי נשמר כפי שהוא ב-`detail`, והעברית ב-`message`
    # נושאת אותו. כשל אינו מתקפל ל"אין עדכון" (`reason=None`).
    assert doc["reason"] == "failed"
    assert doc["detail"] == "no route to github"
    assert "no route to github" in doc["message"]
    assert admin.get("/api/console/update").json()["last_check"] == doc


def test_a_corrupt_stored_check_is_null_not_a_crash(update_server):
    conn = update_server["app"].state.ctx.conn
    set_setting(conn, update_mod.LAST_CHECK_KEY, "{not json")
    assert update_server["admin"].get("/api/console/update").json()["last_check"] is None
    set_setting(conn, update_mod.LAST_CHECK_KEY, "[1, 2]")      # JSON, לא מסמך
    assert update_server["admin"].get("/api/console/update").json()["last_check"] is None


def test_health_carries_the_measurement_time_in_a_header_and_keeps_the_array(health_server):
    resp = health_server["admin"].get("/api/console/health")
    assert resp.status_code == 200
    assert ISO.match(resp.headers[health.CHECKED_AT_HEADER]), resp.headers
    body = resp.json()
    assert isinstance(body, list) and body, "the body stays the bare array"
    assert all({"id", "label", "state", "detail"} <= set(c) for c in body)


def test_health_header_is_not_sent_to_a_forbidden_caller(health_server):
    resp = health_server["deploy"].get("/api/console/health")
    assert resp.status_code == 403
    assert health.CHECKED_AT_HEADER not in resp.headers
