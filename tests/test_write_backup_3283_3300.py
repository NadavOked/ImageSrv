"""#3283 (L1) and #3300 (N3), the server side of the write-check copy.

* a machine that is not a registered cloner (a build machine, an unknown MAC) gets 403 `not_a_cloner` on every
  verb: save, list, read, mark, drop;
* a ceiling for the whole `write-backups/` folder: over it the new copy is 507 `quota` and nothing is stored
  (the agent then writes nothing: no `ok`, no write);
* two POSTs for one serial at the same moment: one 200, one 409 -- never two open copies.

Negative control: `server/disks/write_backup.py` back at the base of this PR -- the role tests, the quota and the
race fail; the control (a registered cloner still saves, reads and drops) passes.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.agent import registry
from server.app import create_agent_app
from server.disks import write_backup
from test_write_backup_3268 import MAC, Q, REGIONS, WHOLE, _list, _post, env  # noqa: F401

BUILD = "aa:bb:cc:00:00:31"


def _register(rt, mac: str, name: str, group: str) -> None:
    registry.add_machine(rt.conn, mac, name, group, "test")


def test_control_a_registered_cloner_saves_lists_reads_and_drops(env):
    _rt, client = env
    blob = os.urandom(WHOLE)
    assert _post(client, blob).status_code == 200
    bid = _list(client).json()["backups"][0]["id"]
    assert client.get(f"/api/v1/agent/write-backup/{bid}/blob", params={"mac": MAC, "serial": "SER1"}).content == blob
    assert client.delete(f"/api/v1/agent/write-backup/{bid}", params={"mac": MAC, "serial": "SER1"}).json()["ok"]


@pytest.mark.parametrize("who", ["unregistered", "build"])
def test_a_machine_that_is_not_a_cloner_gets_403_on_every_verb(env, who):
    rt, client = env
    blob = os.urandom(WHOLE)
    assert _post(client, blob).status_code == 200
    bid = _list(client).json()["backups"][0]["id"]
    mac = "aa:bb:cc:00:00:99" if who == "unregistered" else BUILD
    if who == "build":
        _register(rt, BUILD, "bld-1", "grp_BUILD")
    params = {"mac": mac, "serial": "SER1"}
    sha = hashlib.sha256(blob).hexdigest()
    answers = [
        client.post("/api/v1/agent/write-backup", params={**Q, "mac": mac, "serial": "SER9", "regions": REGIONS, "sha256": sha},
                    content=blob, headers={"Content-Type": "application/octet-stream"}),
        client.get("/api/v1/agent/write-backup", params=params),
        client.get(f"/api/v1/agent/write-backup/{bid}/blob", params=params),
        client.post(f"/api/v1/agent/write-backup/{bid}/stale", params={**params, "blocks": 1}),
        client.delete(f"/api/v1/agent/write-backup/{bid}", params=params),
    ]
    assert [a.status_code for a in answers] == [403] * 5, [(a.status_code, a.text) for a in answers]
    assert all(a.json()["code"] == "not_a_cloner" for a in answers)
    backups = _list(client).json()["backups"]
    assert [b["id"] for b in backups] == [bid] and "stale" not in backups[0], "a non-cloner read, marked or dropped the copy"
    assert _list(client, "SER9").json()["backups"] == [], "a non-cloner stored a copy"


def test_the_folder_has_a_ceiling_and_a_copy_over_it_is_507_and_stored_nowhere(env, monkeypatch):
    rt, client = env
    monkeypatch.setattr(write_backup, "MAX_TOTAL_BYTES", WHOLE + 10)
    assert _post(client, os.urandom(WHOLE)).status_code == 200
    refused = _post(client, os.urandom(WHOLE), serial="SER2")
    assert refused.status_code == 507 and refused.json()["code"] == "quota", refused.text
    assert _list(client, "SER2").json()["backups"] == []
    assert not (rt.data_dir / "write-backups" / "SER2".encode().hex()).exists()
    # dropping the first frees the room
    bid = _list(client).json()["backups"][0]["id"]
    assert client.delete(f"/api/v1/agent/write-backup/{bid}", params={"mac": MAC, "serial": "SER1"}).json()["ok"]
    assert _post(client, os.urandom(WHOLE), serial="SER2").status_code == 200


def test_two_posts_for_one_serial_at_once_leave_one_open_copy(env, monkeypatch):
    rt, client = env
    real = write_backup._durable

    def slow(path, data):          # widens the window between "no open copy" and "the record is written"
        time.sleep(0.4)
        real(path, data)
    monkeypatch.setattr(write_backup, "_durable", slow)
    results: list[int] = []

    def post() -> None:
        c = TestClient(create_agent_app(rt), raise_server_exceptions=False)
        results.append(_post(c, os.urandom(WHOLE)).status_code)
    first = threading.Thread(target=post)
    second = threading.Thread(target=post)
    first.start()
    time.sleep(0.1)
    second.start()
    first.join(20)
    second.join(20)
    assert sorted(results) == [200, 409], results
    assert len(_list(client).json()["backups"]) == 1
