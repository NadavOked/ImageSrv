"""#2460 (Nadav 06/11, option a, CHAP included) -- no backup carries a storage location's secret.

The SMB password (``secret``) and the iSCSI CHAP secret (``chap_secret``) sat in
``storage_locations.params_json`` in plain text, and from there in every copy of the database:
the hourly job, the console download, and ``tools/backup-server.sh`` (all three go through
``consistent_db_copy``). Same exposure as ``console_secret`` (#2424), same fix: the copy drops
them -- in the function that drops ``console_secret`` -- and ``VACUUM`` rebuilds the file.

A server restored from such a copy has a user and no secret. That is said by name, not left to
mount.cifs: ``secret_missing`` in the row, a 409 that says the password is not in the backup, and
``POST /{id}/connect`` with ``{"secret": ...}`` to type it again. A credentials file that survived
on the same machine is used as it is -- "connect" without a new password does not overwrite it.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from datetime import datetime, timezone

import pytest

from server import db
from server.storage import locations as sl
from server.storage.locations import FAILED, OK, UNCHECKED, HookResult
from server.system import backup, backup_job

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
SMB_PASS = "Smb-Pass-2460-marker"
CHAP_SECRET = "chap-fake-value"
NO_IMAGES = type("NoImages", (), {"scan": lambda self: {}})()
SECRET_OF_ROW = {"loc_smb00001": ("secret", SMB_PASS), "loc_san00001": ("chap_secret", CHAP_SECRET)}


def _insert(conn, loc_id: str, kind: str, params) -> None:
    raw = params if isinstance(params, str) else json.dumps(params)
    with db._write_lock, db.writing(conn):
        conn.execute(
            "INSERT INTO storage_locations (id, name, type, params_json, mount_point, state,"
            " state_since, state_detail, created_by, created_at, last_images_json)"
            " VALUES (?, ?, ?, ?, ?, 'connected', 't', '', 'test', 't', '[]')",
            (loc_id, loc_id, kind, raw, f"/srv/imagesrv/storage/{loc_id}"))


@pytest.fixture()
def live(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = db.connect(data_dir / "imagesrv.db")
    _insert(conn, "loc_smb00001", "smb", {"server": "fs01", "share": "images", "username": "imagesrv",
                                          "secret": SMB_PASS, "domain": "COLLEGE"})
    _insert(conn, "loc_san00001", "iscsi", {"portal": "10.44.3.75:3260", "iqn": "iqn.2005-10.org.x:images",
                                            "chap_user": "imagesrv", "chap_secret": CHAP_SECRET,
                                            "auto": True, "fs_type": "ext4"})
    _insert(conn, "loc_nfs00001", "nfs", {"server": "10.44.10.20", "export": "/mnt/pool/images"})
    yield {"dir": data_dir, "conn": conn, "dest": tmp_path / "nas"}
    conn.close()


def _hourly(live) -> bytes:
    live["dest"].mkdir(exist_ok=True)
    status = backup_job.run(live["dir"], str(live["dest"]), NOW)
    assert status["ok"] is True, status
    return (live["dest"] / "db" / status["file"]).read_bytes()


def _download(live) -> bytes:
    payload, _name = backup.build_archive(live["conn"], live["dir"], "v0.83.0", NOW)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    for body in members.values():
        assert SMB_PASS.encode() not in body and CHAP_SECRET.encode() not in body, "secret in the archive"
    return members["data/imagesrv.db"]


def _script_copy(live) -> bytes:
    """What tools/backup-server.sh runs: a raw sqlite3 connection, consistent_db_copy, write_bytes."""
    conn = sqlite3.connect(f"file:{(live['dir'] / 'imagesrv.db').as_posix()}?mode=ro", uri=True)
    try:
        return backup.consistent_db_copy(conn)
    finally:
        conn.close()


WRITERS = {"hourly": _hourly, "download": _download, "backup-server.sh": _script_copy}


def _params(conn, loc_id: str) -> dict:
    return json.loads(conn.execute("SELECT params_json FROM storage_locations WHERE id = ?",
                                   (loc_id,)).fetchone()[0])


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_no_backup_carries_a_storage_secret_and_the_live_db_keeps_them(live, writer):
    raw = WRITERS[writer](live)
    assert SMB_PASS.encode() not in raw, "the SMB password is still in the file (row or free page)"
    assert CHAP_SECRET.encode() not in raw, "the CHAP secret is still in the file (row or free page)"
    copy = sqlite3.connect(":memory:")
    copy.deserialize(raw)
    smb, san, nfs = (_params(copy, i) for i in ("loc_smb00001", "loc_san00001", "loc_nfs00001"))
    assert smb == {"server": "fs01", "share": "images", "username": "imagesrv", "domain": "COLLEGE"}
    assert san == {"portal": "10.44.3.75:3260", "iqn": "iqn.2005-10.org.x:images",
                   "chap_user": "imagesrv", "auto": True, "fs_type": "ext4"}
    assert nfs == {"server": "10.44.10.20", "export": "/mnt/pool/images"}, "a row without secrets as it was"
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    copy.close()
    for loc_id, (key, value) in SECRET_OF_ROW.items():   # the copy was cut, not the server
        assert _params(live["conn"], loc_id)[key] == value


def test_every_secret_key_of_storage_locations_is_dropped(live):
    """The backup reads the list from ``storage.locations`` -- a key added there is dropped too."""
    params = {key: f"value-of-{key}-2460" for key in sl.SECRET_KEYS}
    _insert(live["conn"], "loc_all00001", "smb", {"server": "fs01", "share": "x", **params})
    raw = _script_copy(live)
    for value in params.values():
        assert value.encode() not in raw


def test_params_that_are_not_json_are_not_kept_in_the_copy(live):
    """Unreadable params cannot be checked for a secret, so the copy does not carry them."""
    _insert(live["conn"], "loc_bad00001", "smb", '{"secret": "broken-fake", ')
    raw = _script_copy(live)
    assert b"Broken-2460-pass" not in raw
    copy = sqlite3.connect(":memory:")
    copy.deserialize(raw)
    assert _params(copy, "loc_bad00001") == {}
    copy.close()


@pytest.mark.parametrize("writer", sorted(WRITERS))
def test_a_restored_server_says_the_secret_is_missing(live, writer, tmp_path):
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    (restored_dir / "imagesrv.db").write_bytes(WRITERS[writer](live))
    restored = db.connect(restored_dir / "imagesrv.db")
    try:
        rows = {row["id"]: sl.public_row(row, NO_IMAGES) for row in sl.list_rows(restored)
                if row["id"] != sl.LOCAL_ID}
    finally:
        restored.close()
    assert rows["loc_smb00001"]["secret_missing"] is True
    assert rows["loc_san00001"]["secret_missing"] is True
    assert rows["loc_nfs00001"]["secret_missing"] is False
    live_rows = {row["id"]: sl.public_row(row, NO_IMAGES) for row in sl.list_rows(live["conn"])}
    assert live_rows["loc_smb00001"]["secret_missing"] is False, "the live server is not asked"
    assert live_rows["loc_san00001"]["secret_missing"] is False


# --- the real hook: is there a password in the credentials file? ----------------------------


def test_creds_password_present_reads_the_file(tmp_path):
    good = tmp_path / "loc_a"
    good.write_text("username=imagesrv\npassword=s3cret\n", encoding="utf-8")
    no_password = tmp_path / "loc_b"
    no_password.write_text("username=imagesrv\npassword=\n", encoding="utf-8")
    assert sl.creds_password_present(str(good)).status == OK
    assert sl.creds_password_present(str(no_password)).status == FAILED
    assert sl.creds_password_present(str(tmp_path / "absent")).status == FAILED
    # a file that exists and cannot be read is "not checked", not "no password"
    assert sl.creds_password_present(str(tmp_path)).status == UNCHECKED


# --- /connect after a restore ----------------------------------------------------------------

pytest.importorskip("fastapi")

from test_storage_locations import FakeHooks, env  # noqa: E402,F401 -- the shared fixture

MISSING_SMB = "הסיסמה של המיקום אינה בגיבוי — הקלד אותה מחדש"
MISSING_CHAP = "סוד ה-CHAP של המיקום אינו בגיבוי — הקלד אותו מחדש"


class CredsFile:
    """The fake ``creds_password_present``: what is in /etc/imagesrv/creds/<id>."""

    def __init__(self, fake: FakeHooks):
        self.status = FAILED
        self.reason = "אין קובץ credentials"
        self.asked: list[str] = []
        fake_dict = fake.as_dict

        def as_dict():
            return {**fake_dict(), "creds_password_present": self.check}
        fake.as_dict = as_dict

    def check(self, path):
        self.asked.append(path)
        return HookResult(self.status, "" if self.status == OK else self.reason)


@pytest.fixture()
def restored(tmp_path, images_root, clock, monkeypatch):
    """The ``env`` app with a creds_password_present fake, built the same way."""
    from server.rounds import sender as sender_module
    from server.console import users
    from server.app import create_app
    from fastapi.testclient import TestClient
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    fake = FakeHooks()
    creds = CredsFile(fake)
    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
    health_hooks = {   # as in test_storage_locations.env: nothing reads the machine
        "ss": lambda: "", "unit_active": lambda name: "", "http_get": lambda url: 200,
        "http_size": lambda url: (200, 9_000_000), "interfaces": lambda: list(fake.interfaces),
        "tftp_root": lambda: tftp, "udp_sender_pids": lambda: [],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
                     sender_runner=Recorder(block=True), storage_hooks=fake.as_dict(),
                     storage_check_interval=10**9, health_hooks=health_hooks)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True,
                 check_policy=False)
    admin = TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "fake": fake, "creds": creds}
    ctx.sender.stop()


def _restored_location(env, kind: str) -> str:
    """Create a location through the API, then cut its secret out of the DB -- what the server
    reads after a restore from a backup -- and disconnect it, as a restored server has no mount."""
    if kind == "smb":
        params = {"server": "fs01", "share": "images", "username": "imagesrv", "secret": "old-pass",
                  "domain": "COLLEGE"}
    else:
        params = {"portal": "10.44.3.75:3260", "iqn": "iqn.2005-10.org.freenas.ctl:images",
                  "chap_user": "imagesrv", "chap_secret": "old-chap-secret", "auto": True}
    created = env["admin"].post("/api/console/storage-locations", json={
        "name": f"restored-{kind}", "type": kind, "params": params, "tested": True})
    assert created.status_code == 200, created.text
    loc_id = created.json()["id"]
    conn = env["ctx"].conn
    cut = {k: v for k, v in sl.params_of(sl.get(conn, loc_id)).items() if k not in sl.SECRET_KEYS}
    sl.save_params(conn, loc_id, cut)
    assert env["admin"].post(f"/api/console/storage-locations/{loc_id}/disconnect").status_code == 200
    return loc_id


def _row(env, loc_id):
    rows = env["admin"].get("/api/console/storage-locations").json()["locations"]
    return next(r for r in rows if r["id"] == loc_id)


def test_smb_without_a_password_and_without_a_creds_file_fails_by_name(restored):
    loc_id = _restored_location(restored, "smb")
    row = _row(restored, loc_id)
    assert row["secret_missing"] is True and "secret" not in row["params"]
    writes, mounts = len(restored["fake"].creds_writes), len(restored["fake"].mounts)
    r = restored["admin"].post(f"/api/console/storage-locations/{loc_id}/connect", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == MISSING_SMB
    assert restored["creds"].asked == [sl.creds_path(loc_id)]
    assert len(restored["fake"].creds_writes) == writes, "no credentials file without a password"
    assert len(restored["fake"].mounts) == mounts, "mount.cifs never ran without a password"
    assert sl.get(restored["ctx"].conn, loc_id)["state"] != "connected"


def test_smb_creds_file_that_survived_on_the_same_machine_is_used_not_overwritten(restored):
    loc_id = _restored_location(restored, "smb")
    restored["creds"].status = OK
    writes = len(restored["fake"].creds_writes)
    r = restored["admin"].post(f"/api/console/storage-locations/{loc_id}/connect", json={})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "connected"
    assert len(restored["fake"].creds_writes) == writes, "the file with the password was overwritten"
    assert f"credentials={sl.creds_path(loc_id)}" in restored["fake"].mounts[-1][3]


def test_smb_creds_file_that_cannot_be_read_is_not_a_missing_password(restored):
    loc_id = _restored_location(restored, "smb")
    restored["creds"].status, restored["creds"].reason = UNCHECKED, "Permission denied"
    writes = len(restored["fake"].creds_writes)
    r = restored["admin"].post(f"/api/console/storage-locations/{loc_id}/connect", json={})
    assert r.status_code == 409, r.text
    assert MISSING_SMB in r.json()["detail"] and "Permission denied" in r.json()["detail"]
    assert len(restored["fake"].creds_writes) == writes


def test_typed_smb_password_is_saved_only_after_the_mount_worked(restored):
    loc_id = _restored_location(restored, "smb")
    base = f"/api/console/storage-locations/{loc_id}"
    restored["fake"].mount_status = FAILED
    r = restored["admin"].post(base + "/connect", json={"secret": "wrong-pass"})
    assert r.status_code == 409, r.text
    assert "secret" not in sl.params_of(sl.get(restored["ctx"].conn, loc_id)), "a failed password was saved"
    restored["fake"].mount_status = OK
    r = restored["admin"].post(base + "/connect", json={"secret": "new-pass"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "connected" and r.json()["secret_missing"] is False
    assert "secret" not in r.json()["params"], "the answer does not echo the password"
    assert sl.params_of(sl.get(restored["ctx"].conn, loc_id))["secret"] == "new-pass"
    path, creds = restored["fake"].creds_writes[-1]
    assert path == sl.creds_path(loc_id)
    assert creds == {"username": "imagesrv", "password": "new-pass", "domain": "COLLEGE"}
    assert restored["creds"].asked == [], "a typed password does not look at the old file"


@pytest.mark.parametrize("body, status", [
    ({"secret": ""}, 400), ({"secret": 7}, 400), ({"secret": "a\nb"}, 400),
])
def test_a_typed_secret_is_validated(restored, body, status):
    loc_id = _restored_location(restored, "smb")
    mounts = len(restored["fake"].mounts)
    r = restored["admin"].post(f"/api/console/storage-locations/{loc_id}/connect", json=body)
    assert r.status_code == status, r.text
    assert len(restored["fake"].mounts) == mounts


def test_a_secret_for_a_location_without_a_user_is_refused(env):
    from test_storage_locations import _create_nfs
    loc_id = _create_nfs(env).json()["id"]
    base = f"/api/console/storage-locations/{loc_id}"
    assert env["admin"].post(base + "/disconnect").status_code == 200
    r = env["admin"].post(base + "/connect", json={"secret": "x"})
    assert r.status_code == 400, r.text
    assert env["admin"].post(base + "/connect", json={}).status_code == 200, "the console posts {}"


def test_iscsi_without_a_chap_secret_names_the_reason_and_a_typed_one_logs_in(restored):
    loc_id = _restored_location(restored, "iscsi")
    assert _row(restored, loc_id)["secret_missing"] is True
    base = f"/api/console/storage-locations/{loc_id}"
    restored["fake"].login_status, restored["fake"].login_reason = FAILED, "authorization failure"
    r = restored["admin"].post(base + "/connect", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == f"{MISSING_CHAP} · authorization failure"
    restored["fake"].login_status = OK
    r = restored["admin"].post(base + "/connect", json={"secret": "new-chap-secret"})
    assert r.status_code == 200, r.text
    assert restored["fake"].logins[-1][2] == {"user": "imagesrv", "secret": "new-chap-secret"}
    assert r.json()["secret_missing"] is False and "chap_secret" not in r.json()["params"]
    assert sl.params_of(sl.get(restored["ctx"].conn, loc_id))["chap_secret"] == "new-chap-secret"
