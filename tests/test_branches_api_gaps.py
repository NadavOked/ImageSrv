"""‏#1017 — פערי ה-API של "סניפים": ‏`POST /storage-nodes/{id}/check` (בדיקת
חיבור בלבד, ‏`/ping` המאומת), ו-`last_seen_at`/`last_error`/`last_error_at`
על `storage_nodes` — נכתבים בכל קריאה בין-שרתית ומוחזרים ב-`GET
/storage-nodes` ובתשובות הפרוקסי, כדי ש"ענתה לאחרונה HH:MM" יהיה זמן
השרת ולא שעון הדפדפן.

אותם שני שרתים אמיתיים על loopback כמו ב-``test_storage_remote``.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db
from server.interserver import interserver_auth, storage_nodes
from test_storage_transfer import paired, secondary          # noqa: F401 — fixtures

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")


def _row(server, nid) -> dict:
    return next(n for n in server["admin"].get("/api/console/storage-nodes").json()
                if n["id"] == nid)


# --- יחידה: record_contact --------------------------------------------------

def test_record_contact_success_clears_the_error_and_failure_keeps_last_seen(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES ('n1', 'a', 'https://x:8443/api/interserver/v1', 'c', ?)",
        (db.now_iso(),))
    conn.commit()
    assert storage_nodes.contact_fields(conn, "n1") == {
        "last_seen_at": None, "last_error": None, "last_error_at": None}
    storage_nodes.record_contact(conn, "n1", error="timeout")
    f = storage_nodes.contact_fields(conn, "n1")
    assert f["last_seen_at"] is None and f["last_error"] == "timeout"
    assert ISO.match(f["last_error_at"])
    storage_nodes.record_contact(conn, "n1", error=None)
    f = storage_nodes.contact_fields(conn, "n1")
    assert ISO.match(f["last_seen_at"]) and f["last_error"] is None and f["last_error_at"] is None
    seen = f["last_seen_at"]
    storage_nodes.record_contact(conn, "n1", error="refused")
    f = storage_nodes.contact_fields(conn, "n1")
    assert f["last_seen_at"] == seen, "a failure does not erase when it last answered"
    assert f["last_error"] == "refused"
    assert storage_nodes.contact_fields(conn, "nope") == {
        "last_seen_at": None, "last_error": None, "last_error_at": None}


def test_migration_adds_the_contact_columns_to_an_older_table(tmp_path):
    """‏ADDED_COLUMNS: DB שנוצר לפני #1017 מקבל את שלוש העמודות, NULL בכולן."""
    import sqlite3
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE storage_nodes (id TEXT PRIMARY KEY, label TEXT NOT NULL,"
        " base_url TEXT NOT NULL UNIQUE, group_id TEXT, tls_fingerprint TEXT,"
        " node_id TEXT, protocol_version TEXT, pinned_spki TEXT, client_cert_ref TEXT,"
        " credential_ref TEXT NOT NULL, enrolled_at TEXT NOT NULL, disabled_at TEXT);"
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES ('n1', 'a', 'https://x', 'c', 't');")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(storage_nodes)")}
    assert {"last_seen_at", "last_error", "last_error_at"} <= cols
    assert {"server_version", "server_version_at"} <= cols, "#1300"
    assert storage_nodes.contact_fields(conn, "n1") == {
        "last_seen_at": None, "last_error": None, "last_error_at": None}


# --- דרך השרת ------------------------------------------------------------------

def test_check_pings_the_secondary_and_records_when_it_answered(server, secondary, paired):
    before = _row(server, paired)
    assert before["last_seen_at"] is None and before["last_error"] is None
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/check")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True and body["error"] is None
    assert body["node_id"] == secondary["ident"]["node_id"]
    assert body["protocol_version"] == interserver_auth.PROTOCOL_VERSION
    assert ISO.match(body["checked_at"]) and ISO.match(body["last_seen_at"])
    assert body["last_error"] is None and body["last_error_at"] is None
    # ומה שהטבלה תקרא — אותו זמן, מהשרת.
    after = _row(server, paired)
    assert after["last_seen_at"] == body["last_seen_at"]
    assert after["last_error"] is None and after["last_error_at"] is None


def test_check_of_an_unreachable_secondary_records_the_failure_not_a_5xx(server, secondary, paired):
    admin = server["admin"]
    assert admin.post(f"/api/console/storage-nodes/{paired}/check").json()["connected"] is True
    seen = _row(server, paired)["last_seen_at"]
    secondary["server"].stop()
    resp = admin.post(f"/api/console/storage-nodes/{paired}/check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False and body["error"]
    assert body["node_id"] is None and body["protocol_version"] is None
    assert body["last_error"] == body["error"] and ISO.match(body["last_error_at"])
    assert body["last_seen_at"] == seen, "when it last answered is kept alongside the failure"
    row = _row(server, paired)
    assert row["last_error"] == body["error"] and row["last_seen_at"] == seen


def test_machines_and_images_proxies_record_contact_too(server, secondary, paired):
    admin = server["admin"]
    m = admin.get(f"/api/console/storage-nodes/{paired}/machines").json()
    assert m["connected"] is True and ISO.match(m["last_seen_at"]) and m["last_error"] is None
    i = admin.get(f"/api/console/storage-nodes/{paired}/images").json()
    assert i["connected"] is True and ISO.match(i["last_seen_at"])
    assert _row(server, paired)["last_seen_at"] == i["last_seen_at"]
    secondary["server"].stop()
    m = admin.get(f"/api/console/storage-nodes/{paired}/machines").json()
    assert m["connected"] is False and m["last_error"] == m["error"]
    assert ISO.match(m["last_error_at"]) and m["last_seen_at"] == i["last_seen_at"]


def test_check_guards(server, paired):
    admin, deploy = server["admin"], server["deploy"]
    assert deploy.post(f"/api/console/storage-nodes/{paired}/check").status_code == 403
    assert admin.post("/api/console/storage-nodes/nope/check").status_code == 404
    storage_nodes.set_node_disabled(server["ctx"].conn, paired, True, ("noc", "admin"))
    body = admin.post(f"/api/console/storage-nodes/{paired}/check").json()
    assert body["connected"] is False and "מושבת" in body["error"]
    assert body["last_error"] is None, "a disabled secondary is not asked, so it is not 'unanswered'"
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    assert admin.post(f"/api/console/storage-nodes/{paired}/check").status_code == 409


# --- ‏#1300: גרסת המשני לכרטיס "סניפים" בסקירה ---------------------------------

def test_record_version_only_when_the_field_was_in_the_answer(tmp_path):
    conn = db.connect(tmp_path / "v.db")
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref, enrolled_at)"
        " VALUES ('n1', 'a', 'https://x:8443/api/interserver/v1', 'c', ?)",
        (db.now_iso(),))
    conn.commit()
    row = lambda: dict(conn.execute(  # noqa: E731
        "SELECT server_version, server_version_at FROM storage_nodes WHERE id='n1'").fetchone())
    storage_nodes.record_version(conn, "n1", {"ok": True})       # משני ישן — בלי השדה
    assert row() == {"server_version": None, "server_version_at": None}, "not read = not checked"
    storage_nodes.record_version(conn, "n1", {"server_version": "v0.72.0"})
    r = row()
    assert r["server_version"] == "v0.72.0" and ISO.match(r["server_version_at"])
    storage_nodes.record_version(conn, "n1", {"ok": True})
    assert row()["server_version"] == "v0.72.0", "an answer without the field does not erase it"
    storage_nodes.record_version(conn, "n1", {"server_version": None})
    r = row()
    assert r["server_version"] is None and ISO.match(r["server_version_at"]), "read, unknown"
    storage_nodes.record_version(conn, "n1", {"server_version": 72})
    assert row()["server_version"] is None, "a non-string is unknown, not a guess"


def test_check_and_machines_record_the_secondary_version_and_the_list_returns_it(
        server, secondary, paired, monkeypatch):
    from server.core import agent_version
    admin = server["admin"]
    assert _row(server, paired)["server_version_at"] is None, "never read = not checked"
    monkeypatch.setattr(agent_version, "server_version", lambda: "v9.8.7")
    assert admin.post(f"/api/console/storage-nodes/{paired}/check").json()["connected"] is True
    row = _row(server, paired)
    assert row["server_version"] == "v9.8.7" and ISO.match(row["server_version_at"])
    monkeypatch.setattr(agent_version, "server_version", lambda: "v9.8.8")
    assert admin.get(f"/api/console/storage-nodes/{paired}/machines").json()["connected"] is True
    assert _row(server, paired)["server_version"] == "v9.8.8", "/machines (the tree, every load) records it too"
    secondary["server"].stop()
    admin.get(f"/api/console/storage-nodes/{paired}/machines")
    assert _row(server, paired)["server_version"] == "v9.8.8", "a failed contact keeps the last version read"
