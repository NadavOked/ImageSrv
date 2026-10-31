"""‏#1252 (נמדד על ברזל 10/10, ‏HP-BUILD מול שרת TA ב-v0.59.2): כותרת הגואי
הנייטיב הציגה "imagesrv-srv · 10.10.10.1" ו-"v0.48.1" — מחרוזות מוקאפ
מקודדות. הצד של השרת: ‏`GET /api/v1/agent/state?mac=` (מה ש-guistate.sh
קורא כל 2 שניות) נושא עכשיו `server_name` ו-`server_version`.

* ‏`server_name` — ההגדרה `server_name` אם המנהל קבע אחת, אחרת שם המארח:
  אותו מקור כמו `/me.server_name` (‏#936).
* ‏`server_version` — תג ה-git (`update.current_version`), אותו מקור כמו
  ‏`/me.version` (‏#915). ‏`null` כשהתג לא נקרא — והמסך כותב "לא ידוע",
  לא מספר.

הבדיקה עוברת דרך **אפליקציית הסוכן והקיוסק המפוצלות** — לא רק המעטפת
המשולבת: זה הנתיב שהסוכן קורא בייצור, והמעטפת כבר הסתירה פעם 404 (#824).

**בקרה שלילית:** על הקוד הישן אין שני השדות בתשובה — כל הטסטים נופלים.
"""

from __future__ import annotations

import socket

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_agent_app, create_app, create_runtime
from server.db import set_setting

MAC = "aa:bb:cc:00:00:10"


def _runtime(tmp_path, images_root, describe):
    from test_sender import Recorder                       # noqa: PLC0415
    return create_runtime(
        tmp_path / "data", images_root, "http://10.44.0.1:8080",
        sender_runner=Recorder(block=True),
        update_hooks={"describe": describe}, repo_dir="/repo")


def _state(app) -> dict:
    r = TestClient(app).get(f"/api/v1/agent/state?mac={MAC}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("make", [create_agent_app])
def test_state_carries_the_server_name_and_version(tmp_path, images_root, make, monkeypatch):
    rt = _runtime(tmp_path, images_root, lambda _repo: "v0.59.2")
    # נדב 17/10: שם אחד — שם המארח. הגדרה ישנה `server_name` במסד אינה נקראת עוד, והשם יציב.
    monkeypatch.setattr(socket, "gethostname", lambda: "TA")
    set_setting(rt.ctx.conn, "server_name", "stale-display-name")
    body = _state(make(rt))
    assert body["server_name"] == "TA"
    assert body["server_version"] == "v0.59.2"


def test_an_unread_version_is_null_not_a_number(tmp_path, images_root):
    def broken(_repo):
        raise RuntimeError("git describe failed")
    body = _state(create_agent_app(_runtime(tmp_path, images_root, broken)))
    assert "server_version" in body
    assert body["server_version"] is None


def test_without_a_setting_the_name_is_the_host_name(tmp_path, images_root):
    app = create_app(tmp_path / "data", images_root, "http://10.44.0.1:8080",
                     update_hooks={"describe": lambda _repo: "v0.59.2"}, repo_dir="/repo")
    assert _state(app)["server_name"] == socket.gethostname()
