"""‏#1128 (R41): גיבוי ההגדרות מהקונסולה — ‏`GET /api/console/update/backup`.

מה שנבדק הוא **ראיה חיובית** (עיקרון 5): הארכיון נפתח, ה-DB שבתוכו נפתח
ב-sqlite ומכיל את המשתמש שנוצר, כל קובץ ב-`SHA256SUMS` מתאים לתוכנו,
והאימג'ים **אינם** בפנים (הדיסק הוא מקור האמת להם — הם עוברים ב-rsync).
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.system import backup as backup_mod

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


def _members(payload: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for info in tar.getmembers():
            f = tar.extractfile(info)
            out[info.name] = f.read() if f else b""
    return out


def test_consistent_db_copy_is_a_readable_database_even_under_wal(tmp_path: Path):
    src = sqlite3.connect(tmp_path / "live.db")
    src.execute("PRAGMA journal_mode=WAL")
    src.execute("CREATE TABLE t (k TEXT)")
    src.execute("INSERT INTO t VALUES ('alive')")
    src.commit()                                      # יושב ב-WAL, לא בקובץ הראשי
    raw = backup_mod.consistent_db_copy(src)
    copy = sqlite3.connect(":memory:")
    copy.deserialize(raw)
    assert copy.execute("SELECT k FROM t").fetchone()[0] == "alive"
    # ‏cp של הקובץ הראשי בלבד לא היה רואה את השורה — זו הסיבה ל-backup API.
    assert b"alive" not in (tmp_path / "live.db").read_bytes()
    assert b"alive" in raw


def test_archive_has_db_manifest_sums_and_no_images(tmp_path: Path):
    data = tmp_path / "data"
    (data / "storage-test").mkdir(parents=True)
    (data / "storage-test" / "probe").write_bytes(b"x" * 10)
    (data / "netcfg").mkdir()
    (data / "netcfg" / "servers.conf").write_text("iface=ens33\n")
    (data / "console.pem").write_bytes(b"-----KEY-----")
    # ‏#1302: לוגו מוסד שהועלה לפני שהיכולת הוסרה — שארית, לא הגדרה.
    (data / "branding").mkdir()
    (data / "branding" / "logo.png").write_bytes(b"PNG")
    (data / "imagesrv.db-wal").write_bytes(b"wal")
    conn = sqlite3.connect(data / "imagesrv.db")
    conn.execute("CREATE TABLE settings (k TEXT, v TEXT)")
    conn.commit()
    payload, name = backup_mod.build_archive(conn, data, "v0.53.3")
    assert name.startswith("imagesrv-settings-") and name.endswith(".tar.gz")
    members = _members(payload)
    assert set(members) == {"data/imagesrv.db", "data/netcfg/servers.conf",
                            "data/console.pem", "MANIFEST.txt", "SHA256SUMS"}
    assert b"version=v0.53.3" in members["MANIFEST.txt"]
    assert backup_mod.BACKUP_WARNING.encode() in members["MANIFEST.txt"]
    for line in members["SHA256SUMS"].decode().splitlines():
        digest, _, arc = line.partition("  ")
        assert hashlib.sha256(members[arc]).hexdigest() == digest, arc


def test_integrity_failure_is_an_error_not_a_smaller_backup(monkeypatch):
    """‏עיקרון 5: עותק שלא עבר integrity_check אינו "גיבוי קטן יותר" — הוא כשל."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (k)")
    monkeypatch.setattr(backup_mod, "_integrity", lambda c: "*** page 3: broken")
    with pytest.raises(RuntimeError, match="integrity_check"):
        backup_mod.consistent_db_copy(conn)


@pytest.fixture()
def server(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app
    hooks = {"describe": lambda repo_dir: "v0.53.3",
             "ls_remote_tags": lambda url: (True, "", ""),
             "run_upgrade": lambda repo_dir, tag: (True, "")}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, update_hooks=hooks, repo_dir="/repo")
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(conn, "dep", "deploy-pass-123", "deploy", by="test", check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "app": app, "conn": conn, "data": tmp_path / "data"}


def test_download_restores_to_a_database_with_the_users(server):
    admin, conn = server["admin"], server["conn"]
    r = admin.get("/api/console/update/backup")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/gzip"
    assert 'filename="imagesrv-settings-' in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    members = _members(r.content)
    restored = sqlite3.connect(":memory:")
    restored.deserialize(members["data/imagesrv.db"])
    names = {row[0] for row in restored.execute("SELECT username FROM users")}
    assert {"noc", "dep"} <= names
    secret = restored.execute("SELECT value FROM settings WHERE key='console_secret'").fetchone()
    assert secret and len(secret[0]) >= 32
    assert b"version=v0.53.3" in members["MANIFEST.txt"]
    ev = conn.execute("SELECT event, detail, user FROM journal WHERE event='settings_backup_downloaded'").fetchone()
    assert ev is not None and ev["user"] == "noc" and "bytes" in ev["detail"]


def test_backup_is_admin_only(server):
    app = server["app"]
    anon = TestClient(app)
    assert anon.get("/api/console/update/backup").status_code == 401
    deploy = TestClient(app)
    deploy.post("/api/console/login", json={"username": "dep", "password": "deploy-pass-123"})
    assert deploy.get("/api/console/update/backup").status_code == 403
