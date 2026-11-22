"""#86 -- the server's side of qualifying a machine during a restore.

The switch is an operator policy ("הכשרת המחשב לאימג' בשחזור", on by default, Nadav 04/11 and
12/11, with a way to turn it off); it travels in the hello answer as ``secure_boot_db_update``.
The signed update is served beside the image only when the manifest declares it by name
(``db_update.file``). What the agent did -- applied, refused, failed, with db before and after --
reaches the journal once per state (``server/agent/db_update_log.py``).

Negative control: ``server/agent/db_update_log.py`` removed and ``server/agent/reports.py``,
``server/agent/hello.py``, ``server/console/api.py``, ``server/console/journal_he.py``,
``server/db.py`` and ``server/library/images.py`` back to the base -> see the PR body.
"""

from __future__ import annotations

import json

import pytest

from server import db
from server.console import journal_he
from server.library.images import ImageLibrary
from test_room_precheck_432_65 import disk, drawers_hello
from test_server_room import CLONER1, room_server  # noqa: F401 -- room_server is a fixture

KEY = "secure_boot_db_update"
URL = "/api/console/settings"
MAC = "aa:bb:cc:00:86:01"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    c.close()


# --- the switch -----------------------------------------------------------------------------------


def test_the_default_is_on_and_the_setting_is_an_admin_boolean(server):
    admin, deploy = server["admin"], server["deploy"]
    assert admin.get(URL).json()[KEY] == "true"
    assert admin.post(URL, json={KEY: "false"}).status_code == 200
    assert admin.get(URL).json()[KEY] == "false"
    for bad in ("yes", 1, "on"):
        assert admin.post(URL, json={KEY: bad}).status_code == 400
    assert deploy.post(URL, json={KEY: "true"}).status_code == 403
    assert admin.get(URL).json()[KEY] == "false"
    assert journal_he.SETTINGS_HE[KEY]


def test_the_hello_carries_the_switch(room_server):
    admin = room_server["admin"]
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])[KEY] is True
    assert admin.post(URL, json={KEY: False}).status_code == 200
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])[KEY] is False


# --- the signed update beside the image -----------------------------------------------------------


def _declare(images_root, image_id: str, db_update: object) -> None:
    path = images_root / image_id / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["db_update"] = db_update
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_the_update_is_served_only_when_the_manifest_declares_it(images_root):
    folder = images_root / "img_7f3a91"
    (folder / "db-update.bin").write_bytes(b"signed")
    (folder / "other.bin").write_bytes(b"x")
    lib = ImageLibrary(images_root)
    assert lib.file_path("img_7f3a91", "db-update.bin") is None, "not declared yet"
    _declare(images_root, "img_7f3a91", {"file": "db-update.bin", "sha256": "0" * 64})
    lib = ImageLibrary(images_root)
    assert lib.file_path("img_7f3a91", "db-update.bin") == folder / "db-update.bin"
    assert lib.file_path("img_7f3a91", "other.bin") is None
    _declare(images_root, "img_7f3a91", {"file": "../manifest.json", "sha256": "0" * 64})
    assert ImageLibrary(images_root).file_path("img_7f3a91", "../manifest.json") is None
    _declare(images_root, "img_7f3a91", "db-update.bin")   # not an object
    assert ImageLibrary(images_root).file_path("img_7f3a91", "db-update.bin") is None


def test_the_agent_gets_the_file_and_the_manifest_field_over_http(server, images_root):
    anon = server["anon"]
    (images_root / "img_7f3a91" / "db-update.bin").write_bytes(b"signed-by-microsoft")
    _declare(images_root, "img_7f3a91", {"file": "db-update.bin", "sha256": "1" * 64})
    manifest = anon.get("/api/v1/images/img_7f3a91/manifest").json()
    assert manifest["db_update"] == {"file": "db-update.bin", "sha256": "1" * 64}
    got = anon.get("/api/v1/images/img_7f3a91/files/db-update.bin")
    assert got.status_code == 200 and got.content == b"signed-by-microsoft"


# --- the journal ----------------------------------------------------------------------------------


def _target(state: str, **extra) -> dict:
    return {"dev": "sda", "state": "writing", "db_update": {
        "state": state, "reason": f"why {state}", "before": ["Microsoft Windows Production PCA 2011"],
        "after": ["Microsoft Windows Production PCA 2011", "Windows UEFI CA 2023"], **extra}}


def _rows(conn, event: str) -> list[str]:
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = ?", (event,))]


@pytest.mark.parametrize("state, event", [("applied", "db_update_applied"), ("refused", "db_update_refused"),
                                          ("failed", "db_update_failed")])
def test_each_outcome_is_journaled_once_with_db_before_and_after(conn, state, event):
    from server.agent import db_update_log

    db_update_log.journal_new(conn, "ses_1", MAC, None, [_target(state)])
    rows = _rows(conn, event)
    assert len(rows) == 1, rows
    assert rows[0].startswith(f"{MAC} sda in ses_1 — why {state}")
    assert "db before: Microsoft Windows Production PCA 2011 |" in rows[0]
    assert "db after: Microsoft Windows Production PCA 2011; Windows UEFI CA 2023" in rows[0]
    # the same report again (every 2 seconds) does not write a second row
    db_update_log.journal_new(conn, "ses_1", MAC, json.dumps([_target(state)]), [_target(state)])
    assert len(_rows(conn, event)) == 1
    assert journal_he.EVENTS_HE[event]


def test_severities_and_malformed_reports(conn):
    from server.agent import db_update_log

    assert journal_he.severity("db_update_applied") == "ok"
    assert journal_he.severity("db_update_refused") == "warn"
    assert journal_he.severity("db_update_failed") == "err"
    bad = [{"dev": "sda", "db_update": {"state": "maybe"}}, {"dev": 5, "db_update": {"state": "applied"}},
           {"dev": "sdb", "db_update": "applied"}, "not a target"]
    db_update_log.journal_new(conn, "ses_1", MAC, "{not json", bad)
    for event in db_update_log.EVENTS.values():
        assert _rows(conn, event) == []
    db_update_log.journal_new(conn, "ses_1", MAC, "{not json", [_target("refused", before="x")])
    assert "db before: ? |" in _rows(conn, "db_update_refused")[0]   # not a list = not read, not "none"


def test_a_round_report_reaches_the_journal_once(conn):
    """Through `reports.ingest`, the path every progress report of a round takes (as #2956's test)."""
    from server.agent import reports

    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds,"
        " state, opened_by, created_at, last_join_at, started_at)"
        " VALUES ('ses_86', 'grp_CLONERS', 'img_x', 'p', 1, 300, 'running', 't', ?, 0, ?)",
        (db.now_iso(), db.now_iso()))
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at)"
                 " VALUES ('ses_86', ?, 'writing', ?)", (MAC, db.now_iso()))
    conn.commit()
    for _ in range(2):   # the agent reports every 2 seconds
        got = reports.ingest(conn, {"session_id": "ses_86", "mac": MAC, "state": "writing",
                                    "targets": [_target("applied")]})
        assert got.get("ok") is True, got
    assert len(_rows(conn, "db_update_applied")) == 1
