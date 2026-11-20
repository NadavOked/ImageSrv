"""#3268 (Nadav 18/11) -- the server's side of the sustained part of the waiting-screen write check.

The copy is a region of some hundred MiB (the originals) and the one pattern the check writes over it again and
again. What is proved here: it is stored only whole and only with the sha256 it came with; it is **streamed to a
file** (never held whole in memory: the cap and the leftovers are checked on disk); one open copy per serial,
shared with the short copy; it comes back byte for byte; and the listing says what kind it is, so the agent puts it
back the right way.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.agent import registry
from server.app import create_agent_app, create_runtime
from server.disks import write_backup

MAC = "aa:bb:cc:dd:ee:ff"
MIB = 1048576
REGION = 8 * MIB          # the region the check wrote (small here: the route does not care)
PATTERN = 2 * MIB
DISK = 64 * MIB
OFF = 16 * MIB
Q = {"mac": MAC, "serial": "SER1", "size": DISK, "regions": f"{OFF}:{REGION}", "pattern": PATTERN}
URL = "/api/v1/agent/write-backup/long"


@pytest.fixture()
def env(tmp_path: Path, images_root: Path):
    from test_sender import Recorder   # noqa: PLC0415

    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        sender_runner=Recorder(block=True), wol_send=lambda _p: None)
    registry.add_machine(rt.conn, MAC, "cloner-9", "grp_CLONERS", "test")
    yield rt, TestClient(create_agent_app(rt), raise_server_exceptions=False), tmp_path / "data"
    rt.ctx.sender.stop()


def _post(client, blob: bytes, sha: str | None = None, **over):
    q = {**Q, "sha256": sha or hashlib.sha256(blob).hexdigest(), **over}
    return client.post(URL, params=q, content=blob, headers={"Content-Type": "application/octet-stream"})


def _list(client, serial: str = "SER1"):
    return client.get("/api/v1/agent/write-backup", params={"mac": MAC, "serial": serial})


def _leftovers(data: Path) -> list[Path]:
    return [p for p in (data / "write-backups").rglob("*") if p.is_file() and p.name.endswith(".tmp")]


def test_a_sustained_copy_is_stored_whole_listed_with_its_kind_and_comes_back_byte_for_byte(env):
    _rt, client, data = env
    blob = os.urandom(REGION + PATTERN)
    r = _post(client, blob)
    assert r.status_code == 200 and r.json()["ok"] and r.json()["sha256"] == hashlib.sha256(blob).hexdigest(), r.text
    backups = _list(client).json()["backups"]
    assert len(backups) == 1
    b = backups[0]
    assert b["kind"] == "long" and b["pattern"] == PATTERN and b["regions"] == f"{OFF}:{REGION}" and b["size"] == DISK, b
    got = client.get(f"/api/v1/agent/write-backup/{b['id']}/blob", params={"mac": MAC, "serial": "SER1"})
    assert got.status_code == 200 and got.content == blob
    assert not _leftovers(data), "the temporary file of the stream is left behind"
    assert client.delete(f"/api/v1/agent/write-backup/{b['id']}", params={"mac": MAC, "serial": "SER1"}).json()["ok"]
    assert _list(client).json()["backups"] == []


def test_a_body_that_is_not_what_it_says_is_not_stored_and_leaves_nothing_on_disk(env):
    _rt, client, data = env
    blob = os.urandom(REGION + PATTERN)
    assert _post(client, blob, sha="0" * 64).status_code == 400            # the sha256 is not the body's
    assert _post(client, blob[:-1]).status_code == 400                      # a read that came up short
    assert _post(client, blob + b"x").status_code == 400                    # longer
    assert _post(client, blob[:REGION]).status_code == 400                  # the originals without the pattern
    assert _list(client).json()["backups"] == []
    assert not _leftovers(data)
    assert not list((data / "write-backups").rglob("*.bin")), "a refused body was stored"


def test_the_route_validates_what_it_is_asked_to_keep(env):
    _rt, client, _data = env
    blob = os.urandom(REGION + PATTERN)
    assert _post(client, blob, regions=f"{OFF}:{REGION},0:4096").status_code == 400   # one region only
    assert _post(client, blob, regions=f"{OFF + 1}:{REGION}").status_code == 400      # not 4KiB aligned
    assert _post(client, blob, regions=f"{DISK - 4096}:{REGION}").status_code == 400  # outside the disk
    assert _post(client, blob, pattern=0).status_code == 400
    assert _post(client, blob, pattern=REGION + 4096).status_code == 400              # a pattern longer than the region
    assert _post(client, blob, serial="").status_code == 400


def test_one_open_copy_per_serial_across_both_kinds_and_a_stranger_is_refused(env):
    _rt, client, _data = env
    blob = os.urandom(REGION + PATTERN)
    assert _post(client, blob).status_code == 200
    assert _post(client, blob).status_code == 409                             # a second never stacks
    short = os.urandom(2 * 4096)
    r = client.post("/api/v1/agent/write-backup", content=short, headers={"Content-Type": "application/octet-stream"},
                    params={"mac": MAC, "serial": "SER1", "size": DISK, "regions": "0:4096",
                            "sha256": hashlib.sha256(short).hexdigest()})
    assert r.status_code == 409, "a short copy must not stack on an open sustained one"
    other = client.post(URL, params={**Q, "mac": "aa:bb:cc:dd:ee:99", "sha256": hashlib.sha256(blob).hexdigest()}, content=blob)
    assert other.status_code >= 400, other.text


def test_the_ceilings_are_enforced_while_streaming_and_leave_no_file(env, monkeypatch):
    _rt, client, data = env
    blob = os.urandom(REGION + PATTERN)
    monkeypatch.setattr(write_backup, "MAX_LONG_BYTES", REGION)               # the region and the pattern are more than the cap
    assert _post(client, blob).status_code == 400
    monkeypatch.setattr(write_backup, "MAX_LONG_BYTES", 10 * MIB)
    monkeypatch.setattr(write_backup, "MAX_TOTAL_BYTES", 5 * MIB)             # the folder is full: the agent does not write
    assert _post(client, blob).status_code == 507
    assert not _leftovers(data) and not list((data / "write-backups").rglob("*.bin"))
    assert _list(client).json()["backups"] == []


def test_a_stale_mark_and_a_missing_blob_work_on_a_sustained_copy_like_on_a_short_one(env):
    _rt, client, _data = env
    blob = os.urandom(REGION + PATTERN)
    _post(client, blob)
    bid = _list(client).json()["backups"][0]["id"]
    q = {"mac": MAC, "serial": "SER1", "blocks": 3}
    assert client.post(f"/api/v1/agent/write-backup/{bid}/stale", params=q).json()["ok"]
    assert "stale" in _list(client).json()["backups"][0]
    assert client.get(f"/api/v1/agent/write-backup/{'0' * 32}/blob", params={"mac": MAC, "serial": "SER1"}).status_code == 404
