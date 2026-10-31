"""‏#649 שלב 1: ‏API ארגז הכלים — `GET /tools/catalog`, `PUT /tools/selection`.

admin בלבד (deploy → 403, אנונימי → 401); שמירה = ‏settings **וגם**
`<data_dir>/tools-selection.json` (מה שבניית ה-initrd תקרא בשלב 2) ורשומת
יומן אחת עם הספירות; id שאינו בקטלוג → 422 **בשמו** (לא נזרק בשקט).

v1 יוצאת בלי ארגז הכלים (נדב 19/09) — ה-API עונה 404 (נבדק ב-
`test_console_visibility.py`). כאן ה-fixture מדליק את הדגל, כלומר זה
המסלול של v1.1, והוא נשמר עובד.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


@pytest.fixture()
def tools_server(tmp_path: Path, images_root: Path, clock, monkeypatch):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.core import capabilities
    from server.console import users
    monkeypatch.setattr(capabilities, "TOOLS", True)    # v1.1: הדגל דלוק
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "anon": TestClient(app),
            "ctx": app.state.ctx, "data_dir": tmp_path / "data"}


def test_catalog_is_the_json_file_with_an_empty_selection_at_first(tools_server):
    from server.console.console_tools import load_catalog
    r = tools_server["admin"].get("/api/console/tools/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["tools"] == load_catalog()["tools"]
    assert body["groups"] == load_catalog()["groups"]
    assert body["selection"] == {"build": [], "student": []}
    assert body["summary"] == {"build": {"count": 0, "size_kb_known": 0, "size_unknown": 0},
                               "student": {"count": 0, "size_kb_known": 0, "size_unknown": 0}}


def test_deploy_gets_403_and_anonymous_401_on_both_endpoints(tools_server):
    assert tools_server["deploy"].get("/api/console/tools/catalog").status_code == 403
    assert tools_server["deploy"].put("/api/console/tools/selection",
                                      json={"build": [], "student": []}).status_code == 403
    assert tools_server["anon"].get("/api/console/tools/catalog").status_code == 401
    assert tools_server["anon"].put("/api/console/tools/selection",
                                    json={"build": [], "student": []}).status_code == 401


def test_saving_writes_settings_the_data_dir_file_and_one_journal_row(tools_server):
    from server.db import get_setting
    admin = tools_server["admin"]
    body = {"build": ["testdisk", "disk-blkdiscard", "photorec"], "student": ["win-users"]}
    r = admin.put("/api/console/tools/selection", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["selection"] == body
    summary = r.json()["summary"]
    assert summary["build"]["count"] == 3 and summary["student"]["count"] == 1
    # testdisk 1.5MB נמדד, blkdiscard "חלק מ-util-linux" = לא נמדד, photorec "כלול ב-testdisk" = לא נמדד
    assert summary["build"] == {"count": 3, "size_kb_known": 1536, "size_unknown": 2}
    assert summary["student"] == {"count": 1, "size_kb_known": 486, "size_unknown": 0}

    # קריאה חוזרת — מה שנשמר, לא מה שנשלח
    again = admin.get("/api/console/tools/catalog").json()
    assert again["selection"] == body
    assert again["summary"] == summary
    assert json.loads(get_setting(tools_server["ctx"].conn, "tools.selection")) == body

    on_disk = json.loads((tools_server["data_dir"] / "tools-selection.json").read_text(encoding="utf-8"))
    assert on_disk == {"schema": 1, **body}

    events = [e for e in admin.get("/api/console/journal").json() if e["event"] == "tools_selection"]
    assert len(events) == 1
    assert events[0]["label"] == "בחירת ארגז הכלים נשמרה"          # journal_he
    assert "build=3" in events[0]["text"] and "student=1" in events[0]["text"]


def test_a_packed_tool_adds_zero_and_is_not_counted_as_unmeasured(tools_server):
    r = tools_server["admin"].put("/api/console/tools/selection",
                                  json={"build": ["disk-hdparm-erase"], "student": []})
    assert r.json()["summary"]["build"] == {"count": 1, "size_kb_known": 0, "size_unknown": 0}


def test_an_unknown_id_is_refused_by_name_and_nothing_is_saved(tools_server):
    admin = tools_server["admin"]
    r = admin.put("/api/console/tools/selection", json={"build": ["testdisk", "no-such-tool"], "student": []})
    assert r.status_code == 422
    assert "no-such-tool" in r.json()["detail"]
    assert admin.get("/api/console/tools/catalog").json()["selection"] == {"build": [], "student": []}
    assert not (tools_server["data_dir"] / "tools-selection.json").exists()


@pytest.mark.parametrize("body", [
    {"build": ["testdisk"]},                       # student חסר
    {"build": "testdisk", "student": []},          # לא רשימה
    {"build": [1], "student": []},                 # לא מחרוזות
    [],                                            # לא אובייקט
])
def test_a_malformed_body_is_422(tools_server, body):
    assert tools_server["admin"].put("/api/console/tools/selection", json=body).status_code == 422


def test_duplicates_are_collapsed_and_a_later_save_replaces_the_earlier_one(tools_server):
    admin = tools_server["admin"]
    admin.put("/api/console/tools/selection", json={"build": ["testdisk", "testdisk"], "student": ["testdisk"]})
    assert admin.get("/api/console/tools/catalog").json()["selection"] == {"build": ["testdisk"], "student": ["testdisk"]}
    admin.put("/api/console/tools/selection", json={"build": [], "student": []})
    assert admin.get("/api/console/tools/catalog").json()["selection"] == {"build": [], "student": []}
    assert json.loads((tools_server["data_dir"] / "tools-selection.json").read_text(encoding="utf-8")) == {"schema": 1, "build": [], "student": []}
