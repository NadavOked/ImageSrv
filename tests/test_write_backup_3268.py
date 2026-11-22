"""#3268 -- the server's side of the waiting-screen write check: the copy of what the check overwrites.

No confirmation (ok + the same sha256) -> the agent does not write (test_idlecheck_3268.py). So what is
proved here: a copy is stored only whole (the originals, then what the check writes there) and only with
the sha256 it came with; one open copy per serial; the same bytes come back; it is gone only when the
agent says the restore was verified; a stale copy is kept, marked (#3271 H3); and a record that cannot be
read is never "no copy", and an ``ok`` is on disk before it is said (#3271 M4).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.agent import registry
from server.app import create_agent_app, create_runtime

MAC = "aa:bb:cc:dd:ee:ff"
Q = {"mac": MAC, "serial": "SER1", "size": 67108864}
REGIONS = "0:8192,16384:4096"
WHOLE = 2 * (8192 + 4096)   # the originals, then the patterns: the same lengths


@pytest.fixture()
def env(tmp_path: Path, images_root: Path):
    from fakesend import Recorder   # noqa: PLC0415

    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:7712",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None)
    registry.add_machine(rt.conn, MAC, "cloner-9", "grp_CLONERS", "test")   # #3283: only a registered cloner uses it
    yield rt, TestClient(create_agent_app(rt), raise_server_exceptions=False)
    rt.ctx.chunk.stop()


def _post(client, blob: bytes, regions: str = REGIONS, sha: str | None = None, **over):
    q = {**Q, "regions": regions, "sha256": sha or hashlib.sha256(blob).hexdigest(), **over}
    return client.post("/api/v1/agent/write-backup", params=q, content=blob,
                       headers={"Content-Type": "application/octet-stream"})


def _list(client, serial: str = "SER1"):
    return client.get("/api/v1/agent/write-backup", params={"mac": MAC, "serial": serial})


def _events(rt) -> list[str]:
    return [row[0] for row in rt.conn.execute("SELECT event FROM journal WHERE event LIKE 'write_backup_%'")]


def test_a_stored_copy_comes_back_byte_for_byte_and_goes_only_on_release(env):
    rt, client = env
    blob = os.urandom(WHOLE)
    r = _post(client, blob)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["sha256"] == hashlib.sha256(blob).hexdigest(), r.text
    listing = _list(client).json()
    assert [b["regions"] for b in listing["backups"]] == [REGIONS]
    bid = listing["backups"][0]["id"]
    got = client.get(f"/api/v1/agent/write-backup/{bid}/blob", params={"mac": MAC, "serial": "SER1"})
    assert got.status_code == 200 and got.content == blob
    # another serial knows nothing of it
    assert _list(client, "OTHER").json()["backups"] == []
    assert client.delete(f"/api/v1/agent/write-backup/{bid}", params={"mac": MAC, "serial": "SER1"}).json()["ok"]
    assert _list(client).json()["backups"] == []
    assert "write_backup_saved" in _events(rt) and "write_backup_released" in _events(rt)


def test_a_body_that_is_not_what_it_says_is_not_stored(env):
    _rt, client = env
    blob = os.urandom(WHOLE)
    assert _post(client, blob, sha="0" * 64).status_code == 400            # the sha256 is not the body's
    assert _post(client, blob[:-1]).status_code == 400                      # shorter than the regions
    assert _post(client, blob + b"x").status_code == 400                    # longer
    assert _post(client, blob[:WHOLE // 2]).status_code == 400             # #3271 H3: the originals without the patterns
    assert _post(client, blob, regions="nonsense").status_code == 400
    assert _list(client).json()["backups"] == []


def test_one_open_copy_per_serial_a_second_is_refused_not_stacked(env):
    _rt, client = env
    first = os.urandom(WHOLE)
    assert _post(client, first).status_code == 200
    second = _post(client, os.urandom(WHOLE))
    assert second.status_code == 409 and second.json()["code"] == "open_copy"
    bid = _list(client).json()["backups"][0]["id"]
    assert client.get(f"/api/v1/agent/write-backup/{bid}/blob", params={"mac": MAC, "serial": "SER1"}).content == first


def test_a_record_without_its_blob_is_not_an_open_copy(env):
    rt, client = env
    assert _post(client, os.urandom(WHOLE)).status_code == 200
    folder = next((Path(rt.data_dir) / "write-backups").iterdir())
    for blob in folder.glob("*.bin"):
        blob.unlink()
    assert _list(client).json()["backups"] == []


def test_the_hdd_support_switch_is_off_by_default_validated_and_in_the_setting_roundtrip(env):
    from server.console import users
    from server.app import create_console_app
    rt, _client = env
    users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(create_console_app(rt))
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert admin.get("/api/console/settings").json()["hdd_support"] == "false"
    assert admin.post("/api/console/settings", json={"hdd_support": "maybe"}).status_code == 400
    assert admin.post("/api/console/settings", json={"hdd_support": True}).status_code == 200
    assert admin.get("/api/console/settings").json()["hdd_support"] == "true"


def _seed_copy(rt, blob: bytes, record: str | None = None) -> str:
    """A copy as the server keeps it, laid on its disk directly: the tests below hold the routes that read
    and mark a copy, and a negative control on the server before them fails on those, not on the upload."""
    bid = "0123456789abcdef0123456789abcdef"
    folder = Path(rt.data_dir) / "write-backups" / "SER1".encode().hex()
    folder.mkdir(parents=True)
    (folder / f"{bid}.bin").write_bytes(blob)
    (folder / f"{bid}.json").write_text(record if record is not None else json.dumps(
        {"id": bid, "serial": "SER1", "size": Q["size"], "regions": REGIONS,
         "sha256": hashlib.sha256(blob).hexdigest(), "mac": MAC, "created": 1}), encoding="utf-8")
    return bid


def test_h3_a_stale_copy_is_kept_and_marked_once_never_deleted_by_the_mark(env):
    rt, client = env
    blob = os.urandom(WHOLE)
    bid = _seed_copy(rt, blob)
    for _ in range(2):   # every boot finds it again: one journal line, not one a boot
        r = client.post(f"/api/v1/agent/write-backup/{bid}/stale", params={"mac": MAC, "serial": "SER1", "blocks": 7})
        assert r.status_code == 200 and r.json()["ok"], r.text
    copy = _list(client).json()["backups"]
    assert len(copy) == 1 and copy[0]["stale"]["blocks"] == 7, copy
    assert client.get(f"/api/v1/agent/write-backup/{bid}/blob", params={"mac": MAC, "serial": "SER1"}).content == blob
    assert _events(rt).count("write_backup_stale") == 1 and "write_backup_released" not in _events(rt)
    assert client.post(f"/api/v1/agent/write-backup/{bid}/stale",
                       params={"mac": MAC, "serial": "SER1", "blocks": 0}).status_code == 400


def test_m4_a_record_that_cannot_be_read_is_not_no_copy(env):
    rt, client = env
    _seed_copy(rt, os.urandom(WHOLE), record="{not json")
    r = _list(client)
    assert r.status_code == 500 and r.json()["code"] == "unreadable", r.text   # never {"ok": true, "backups": []}
    again = _post(client, os.urandom(WHOLE))
    assert again.status_code == 500 and again.json()["code"] == "unreadable", again.text


def test_m4_the_copy_is_fsynced_before_ok(env, monkeypatch):
    from server.disks import write_backup
    _rt, client = env
    synced: list[int] = []
    real = os.fsync
    monkeypatch.setattr(write_backup.os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    assert _post(client, os.urandom(WHOLE)).status_code == 200
    assert len(synced) >= 2, "the blob and the record are not fsynced before the agent is told ok"
