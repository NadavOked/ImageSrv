"""‏#2419 (2) — ‏"החלת עדכון" אינה מורידה גרסה.

‏``POST /update/apply`` קיבל כל תג חתום, גם ישן מהמותקן. ירידת גרסה קיימת
במכוון, אבל במסלול אחד ומפורש: ‏``/revert`` לגרסה הקודמת, עם חלון אישור משלו.
"""

from __future__ import annotations

from server import update as update_mod
from test_update import _enable, update_server  # noqa: F401 -- fixture


def test_apply_refuses_a_tag_older_than_the_installed_version(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply", json={"tag": "v0.23.9"})
    assert r.status_code == 409, r.text
    assert "v0.24.0" in r.json()["detail"]
    assert update_server["calls"]["run"] == 0
    assert admin.get("/api/console/update/status").json().get("state") != "running"


def test_a_forced_apply_does_not_skip_the_downgrade_refusal(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply", json={"tag": "v0.23.9", "force": True})
    assert r.status_code == 409, r.text
    assert update_server["calls"]["run"] == 0


def test_apply_of_a_newer_or_the_same_version_still_starts(update_server):
    admin = update_server["admin"]
    _enable(admin)
    assert admin.post("/api/console/update/apply", json={"tag": "v0.24.0"}).status_code == 200
    # הנוכחית כבר v0.24.0 — הריצה הראשונה "אומתה", והבאה אינה חסומה ב"כבר רץ".
    assert admin.post("/api/console/update/apply", json={"tag": "v0.25.0"}).status_code == 200
    assert update_server["calls"]["run"] == 2


def test_revert_to_the_older_previous_version_is_still_allowed(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply", json={"tag": "v0.25.0"})
    update_server["state"]["current"] = "v0.25.0"
    r = admin.post("/api/console/update/revert", json={})
    assert r.status_code == 200, r.text
    assert update_server["calls"]["run_args"] == ("/repo", "v0.24.0")


def test_downgrade_refusal_unit():
    assert update_mod.downgrade_refusal("v0.82.0", "v0.82.1") is None
    assert update_mod.downgrade_refusal("v0.82.0", "v0.82.0") is None
    assert update_mod.downgrade_refusal("v0.82.0-3-gabc1234", "v0.83.0") is None
    assert update_mod.downgrade_refusal("v0.82.0", "v0.81.9")
    assert update_mod.downgrade_refusal("v0.82.0", "v0.9.99")       # מספרית, לא מחרוזת
    assert update_mod.downgrade_refusal(None, "v0.1.0") is None      # לא נקרא — כמו check_update
