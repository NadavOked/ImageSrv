"""#3161 -- a direct send (build machine -> cloners) through the chunk engine, on the server's side.

Until here a direct send ran on udpcast only: the chunk engine needs a file and an index on the
server, and a direct send keeps no image there. Now (and since #3173 there is no other engine, and no
"stream engine" switch):

- the build machine uploads the chunk index (and the head frame) of every partition BEFORE its
  manifest, and the manifest is refused unless each index is the one it signs (`index_xxh64`);
- the receivers download that index, and only the head's bytes of the partition "file";
- the server's own ChunkEngine orchestrates the wave, and hands the SOURCE one order a pass
  (`task.direct.chunk.order`) instead of running `chunkcast send` itself;
- what one machine lacks is sent again by the source -- never a `fetch`, there is no file;
- a source that fails a pass, or goes silent in one, fails the wave by name.

Everything goes through the API, as the two agents do it. The receivers' and the sender's real
binaries are in test_chunkcast_stdin_3161.py; the build machine's shell in test_directchunk_agent_3161.py.

**Negative control** (the PR's body has the numbers): with every production file of the change back
at the base commit, each test here fails on its own assertion -- no `engine` in the hello, 404/405
on the upload, a udpcast wave -- except the library's `fetch`, which passes in both states on purpose.

The index, its BLAKE3 list, the manifest that signs them and the upload live in test_direct_server.py
(#3173): every direct send is a wave of this engine now, and its helpers are the direct send's own.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import re
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from test_direct_server import (HEAD, _b3_list, _build_hello, _index, _live_manifest, _manifest, _open_direct,
                                _put_manifest, _setup, _token, _upload)
from test_server_room import CLONER1, CLONER2, room, room_server  # noqa: F401 -- room_server is a fixture

from server.core.tasks import TOKEN_HEADER
from server.rounds import directchunk, repair

FILE = "p1.esp.pcl.zst"


def _cloner(room_server, mac: str = CLONER1, serials=("S1", "S2")) -> dict:
    """A cloner's hello, from an agent image that knows the chunk engine."""
    body = hello_body(mac)
    body["engines"] = ["udpcast", "chunk"]
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": serial, "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1}
                     for i, serial in enumerate(serials)]
    answer = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _direct(room_server) -> dict:
    return _build_hello(room_server["anon"])["task"]["direct"]


def _until(check, seconds: float = 8.0):
    end = time.monotonic() + seconds
    while True:
        got = check()
        if got:
            return got
        assert time.monotonic() < end, "the condition did not come true"
        time.sleep(0.05)


def _running_wave(room_server, index: bytes | None = None, kind: str = "raw"):
    """A direct round, its indexes uploaded and its manifest accepted, until the wave runs.
    Returns (what the open answered, the cloner's hello of the running wave)."""
    opened = _setup(room_server)
    index = _index(kind=kind) if index is None else index
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    if kind == "partclone":
        assert _upload(room_server, opened["task_id"], "head", 1, HEAD).status_code == 200
    put = _put_manifest(room_server, opened["task_id"], manifest=_manifest(index))
    assert put.status_code == 200, put.text
    _cloner(room_server)                                    # joins
    _cloner(room_server)                                    # the pulse that lets the wave out
    hello = _cloner(room_server)
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    return opened, hello


def _pass(room_server, wave: str, state: str, pass_: int, ranges: dict, mac: str = CLONER1):
    return room_server["anon"].post("/api/v1/agent/chunk-pass", json={
        "mac": mac, "session_id": wave, "part": 1, "pass": pass_, "state": state,
        "targets": [{"serial": serial, "write_mbps": 150, "durable": [{"part": 1, "ranges": got}]}
                    for serial, got in ranges.items()]})


def _ended(room_server, wave: str, pass_: int, ranges: dict) -> dict:
    """Reports `ended` again and again, as the agent does, until the answer is not `wait`."""
    return _until(lambda: (a := _pass(room_server, wave, "ended", pass_, ranges).json())["action"] != "wait" and a)


def _order(room_server, seq: int) -> dict:
    return _until(lambda: (o := _direct(room_server).get("chunk", {}).get("order")) and o["seq"] == seq and o)


def _sent(room_server, task_id: str, **body):
    return room_server["anon"].post(f"/api/v1/direct/{task_id}/chunk-sent", content=json.dumps(body).encode(),
                                    headers={TOKEN_HEADER: _token(room_server, task_id)})


def _events(room_server) -> list[tuple[str, str]]:
    return [(r["event"], r["detail"]) for r in room_server["ctx"].conn.execute("SELECT event, detail FROM journal")]


# --- before the manifest -------------------------------------------------------------------------

def test_before_the_manifest_the_source_is_told_to_upload_its_indexes(room_server):
    _setup(room_server)
    direct = _direct(room_server)
    assert direct.get("engine") == "chunk"                  # before the manifest: "upload your indexes"
    assert "chunk" not in direct                            # no wave of the engine yet, and no order


# --- the indexes, the head and the manifest that signs them -----------------------------------------

def test_the_manifest_is_accepted_when_it_signs_the_index_that_was_uploaded(room_server):
    opened = _setup(room_server)
    anon, image = room_server["anon"], opened["image_id"]
    index = _index()
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    assert anon.get(f"/api/v1/images/{image}/index/1").status_code == 404      # not signed by a manifest yet
    assert _put_manifest(room_server, opened["task_id"], manifest=_manifest(index)).status_code == 200
    served = anon.get(f"/api/v1/images/{image}/index/1")
    assert served.status_code == 200 and served.content == index               # byte for byte: its XXH64 is checked
    manifest = anon.get(f"/api/v1/images/{image}/manifest").json()
    assert manifest["partitions"][0]["readback_chunks"] == 10                  # what the engine and the ledger count by
    assert not [k for k in manifest if k.startswith("_")]                      # the server's mark is not served
    late = _upload(room_server, opened["task_id"], "index", 1, index)          # the manifest is the seal
    assert late.status_code == 409
    listed = anon.get(f"/api/v1/images/{image}/b3/1")                         # #3332: the list, as uploaded
    assert listed.status_code == 200 and listed.content == _b3_list(index)


def test_a_manifest_whose_partition_has_no_blake3_list_uploaded_is_refused_by_name(room_server):
    """#3332: the receivers write nothing without the list -- so a live manifest without it is not a wave."""
    opened = _setup(room_server)
    index = _index()
    assert _upload(room_server, opened["task_id"], "index", 1, index, b3=False).status_code == 200
    refused = _put_manifest(room_server, opened["task_id"], manifest=_manifest(index))
    assert refused.status_code == 400 and "its BLAKE3 list was not uploaded" in refused.json()["detail"], refused.text


def test_a_blake3_list_that_is_not_the_one_the_manifest_names_is_refused_by_name(room_server):
    opened = _setup(room_server)
    index = _index()
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    refused = _put_manifest(room_server, opened["task_id"], manifest=_manifest(index, chunk_b3="ab" * 32))
    assert refused.status_code == 400 and "not the one the manifest names (chunk_b3)" in refused.json()["detail"]


def test_a_blake3_list_of_another_length_than_the_index_is_refused(room_server):
    opened = _setup(room_server)
    index = _index()
    assert _upload(room_server, opened["task_id"], "index", 1, index, b3=False).status_code == 200
    short = _upload(room_server, opened["task_id"], "b3", 1, _b3_list(index)[:-32])
    assert short.status_code == 400 and "are not the BLAKE3 list of 10 chunks" in short.json()["detail"], short.text


def test_an_index_that_is_not_the_manifests_fails_the_task_and_the_round_by_name(room_server):
    opened = _setup(room_server)
    assert _upload(room_server, opened["task_id"], "index", 1, _index(10)).status_code == 200
    refused = _put_manifest(room_server, opened["task_id"], manifest=_manifest(_index(11)))
    assert refused.status_code == 400 and "index_xxh64" in refused.json()["detail"]
    conn = room_server["ctx"].conn
    assert conn.execute("SELECT state FROM tasks WHERE id = ?", (opened["task_id"],)).fetchone()["state"] == "failed"
    assert conn.execute("SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()["state"] == "failed"


def test_an_upload_that_is_not_a_format_2_index_is_refused_and_fails_the_task(room_server):
    opened = _setup(room_server)
    bad = _index()[:-1]                                     # no final newline: not the file chunkcut writes
    refused = _upload(room_server, opened["task_id"], "index", 1, bad)
    assert refused.status_code == 400 and "format-2" in refused.json()["detail"]
    assert room_server["ctx"].conn.execute(
        "SELECT state FROM tasks WHERE id = ?", (opened["task_id"],)).fetchone()["state"] == "failed"


def test_an_upload_past_its_ceiling_is_refused_and_not_read_to_its_end(room_server):
    """These two routes are outside the agent port's 1MiB cap (`body.STREAM_ROUTES`): an index is 36
    bytes a chunk, and a big disk's is larger. They carry a ceiling of their own instead."""
    live = getattr(room_server["ctx"], "direct_chunk", None)
    if live is not None:
        live.index_max = 64                                 # the test's ceiling; 64MiB on a server
    opened = _setup(room_server)
    index = _index(10)
    assert len(index) > 64
    refused = _upload(room_server, opened["task_id"], "index", 1, index)
    assert refused.status_code == 413 and refused.json()["code"] == "too_large"
    # nothing of it was kept: a manifest that follows is the manifest of a source that uploaded nothing
    assert _put_manifest(room_server, opened["task_id"], manifest=_live_manifest()).status_code == 200
    assert room_server["anon"].get(f"/api/v1/images/{opened['image_id']}/index/1").status_code == 404


def test_a_partition_without_its_index_refuses_the_manifest_a_partial_upload_is_not_none(room_server):
    opened = _setup(room_server)
    index = _index()
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    manifest = _manifest(index)
    manifest["partitions"].append({**manifest["partitions"][0], "index": 2, "file": "p2.windows.pcl.zst",
                                   "start_sector": 300000})
    refused = _put_manifest(room_server, opened["task_id"], manifest=manifest)
    assert refused.status_code == 400 and "partition 2" in refused.json()["detail"]


def test_readback_chunks_that_disagree_with_the_index_refuse_the_manifest(room_server):
    opened = _setup(room_server)
    index = _index(10)
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    refused = _put_manifest(room_server, opened["task_id"], manifest=_manifest(index, readback_chunks=9))
    assert refused.status_code == 400 and "readback_chunks" in refused.json()["detail"]


def test_a_head_that_comes_before_its_index_is_refused(room_server):
    opened = _setup(room_server)
    early = _upload(room_server, opened["task_id"], "head", 1, HEAD)
    assert early.status_code == 400 and "before its chunk index" in early.json()["detail"]


def test_a_head_must_be_exactly_the_frame_its_index_describes(room_server):
    opened = _setup(room_server)
    assert _upload(room_server, opened["task_id"], "index", 1, _index(kind="partclone")).status_code == 200
    short = _upload(room_server, opened["task_id"], "head", 1, HEAD[:-1])
    assert short.status_code == 400 and "head frame" in short.json()["detail"]
    assert room_server["ctx"].conn.execute(
        "SELECT state FROM tasks WHERE id = ?", (opened["task_id"],)).fetchone()["state"] == "failed"


def test_the_head_of_a_live_image_is_served_by_an_exact_range_and_nothing_else_is(room_server):
    opened, _hello = _running_wave(room_server, kind="partclone")
    anon, url = room_server["anon"], f"/api/v1/images/{opened['image_id']}/files/{FILE}"
    whole = anon.get(url, headers={"Range": f"bytes=0-{len(HEAD) - 1}"})
    assert whole.status_code == 206 and whole.content == HEAD
    assert anon.get(url, headers={"Range": "bytes=4-9"}).content == HEAD[4:10]
    for wanted in ({"Range": f"bytes=0-{len(HEAD)}"}, {"Range": f"bytes={len(HEAD) + 1}-{len(HEAD)}"}, {}):
        beyond = anon.get(url, headers=wanted)              # the head and a byte, a backward range, the whole file
        assert beyond.status_code == 404 and beyond.json()["code"] == "no_file", wanted
    # whole chunks are the build machine's to read (#3209), and this one has no address the server can reach
    chunks = anon.get(url, headers={"Range": f"bytes={len(HEAD)}-{len(HEAD) + 3}"})
    assert chunks.status_code == 502 and chunks.json()["code"] == "source_unreachable"
    room_server["ctx"].chunk.stop(opened["wave_session_id"])


def test_a_manifest_without_the_head_of_a_partclone_partition_is_refused(room_server):
    opened = _setup(room_server)
    index = _index(kind="partclone")
    assert _upload(room_server, opened["task_id"], "index", 1, index).status_code == 200
    refused = _put_manifest(room_server, opened["task_id"], manifest=_manifest(index))
    assert refused.status_code == 400 and "head frame" in refused.json()["detail"]


# --- an upload is a slow body: what happens while it arrives (review of #3216) ---------------------

async def _put_slowly(app, path: str, token: str, receive) -> tuple[int, dict]:
    """One PUT straight into the ASGI app, with the test's own `receive` -- a body that takes its time, as a
    large index over a slow link does. TestClient reads a request whole before the app sees it, so it cannot
    put anything BETWEEN the task's check and the end of the body; this can."""
    sent = []
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "PUT", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": [(b"host", b"testserver"), (TOKEN_HEADER.lower().encode(), token.encode()),
                         (b"content-type", b"text/plain")],
             "client": ("testclient", 50000), "server": ("testserver", 80)}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body)


def test_an_upload_that_ends_after_its_round_was_replaced_keeps_nothing_and_deletes_nothing(room_server):
    """The body of task A's index is still arriving when the operator stops A's round and opens B, whose
    source uploads its own index. When A's body ends it was accepted (the task had been open when the request
    began), and its clean-up -- "everything that is not this upload's image" -- deleted B's folder."""
    first = _setup(room_server)
    admin, anon, app = room_server["admin"], room_server["anon"], room_server["app"]
    index_a, index_b, later, calls = _index(10), _index(7), {}, []

    async def receive():
        calls.append(len(calls))
        if len(calls) == 1:
            return {"type": "http.request", "body": index_a[:20], "more_body": True}
        if len(calls) == 2:                     # between two pieces of the body: the round is replaced
            assert admin.post("/api/console/room/close", json={"confirm_name": "build-01:sda"}).status_code == 200
            opened = _open_direct(admin, [{"mac": CLONER1, "ports": [1, 2]}])
            assert opened.status_code == 200, opened.text
            later.update(opened.json())
            assert _upload(room_server, later["task_id"], "index", 1, index_b).status_code == 200
        return {"type": "http.request", "body": index_a[20:], "more_body": False}

    status, answer = asyncio.run(_put_slowly(app, f"/api/v1/direct/{first['task_id']}/index/1",
                                             _token(room_server, first["task_id"]), receive))
    assert len(calls) >= 2 and later, "the body was never read: the round was not replaced under it"
    assert status == 409 and answer["code"] == "task_closed", (status, answer)
    # B's index is where B's source put it: its manifest is sealed against it, and the receivers get it
    assert _put_manifest(room_server, later["task_id"], manifest=_manifest(index_b)).status_code == 200
    served = anon.get(f"/api/v1/images/{later['image_id']}/index/1")
    assert served.status_code == 200 and served.content == index_b
    assert anon.get(f"/api/v1/images/{first['image_id']}/index/1").status_code == 404     # and nothing of A's


def test_the_folder_of_a_round_that_ended_is_removed_and_a_live_one_is_not(room_server, tmp_path):
    first = _setup(room_server)
    admin, store = room_server["admin"], tmp_path / "data" / "direct"
    assert _upload(room_server, first["task_id"], "index", 1, _index(10)).status_code == 200
    assert (store / first["image_id"]).is_dir()
    assert admin.post("/api/console/room/close", json={"confirm_name": "build-01:sda"}).status_code == 200
    second = _open_direct(admin, [{"mac": CLONER1, "ports": [1, 2]}]).json()
    assert _upload(room_server, second["task_id"], "index", 1, _index(7)).status_code == 200
    assert (store / second["image_id"]).is_dir()            # the round that is active now
    assert not (store / first["image_id"]).exists()         # the one that was closed: by its state, not by who uploads


def test_only_two_uploads_are_read_at_once_and_a_third_is_refused_before_its_body(room_server, tmp_path):
    """Each upload is under its own 64MiB ceiling, and thirty-two of them together were 2GiB of memory. Now a
    body is read by the holder of a slot only, and into a file."""
    opened = _setup(room_server)
    app, token = room_server["app"], _token(room_server, opened["task_id"])
    path, index = f"/api/v1/direct/{opened['task_id']}/index/", _index(10)
    third_read = []

    async def scenario():
        release, reading = asyncio.Event(), [asyncio.Event(), asyncio.Event()]

        def held(n):
            pieces = iter([index[:20], index[20:]])

            async def receive():
                piece = next(pieces)
                if len(piece) == 20:
                    reading[n].set()            # the first piece is in: this upload holds its slot
                    return {"type": "http.request", "body": piece, "more_body": True}
                await release.wait()
                return {"type": "http.request", "body": piece, "more_body": False}
            return receive

        async def never():
            third_read.append(1)
            return {"type": "http.request", "body": index, "more_body": False}

        first = asyncio.ensure_future(_put_slowly(app, path + "1", token, held(0)))
        second = asyncio.ensure_future(_put_slowly(app, path + "2", token, held(1)))
        both = asyncio.ensure_future(asyncio.gather(reading[0].wait(), reading[1].wait()))
        await asyncio.wait({both, first, second}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert both.done(), ("an upload ended before its body was read", first.result() if first.done() else None)
        await asyncio.sleep(0.2)                # both are inside the read of their second piece
        spooled = sorted(f.name for f in (tmp_path / "data" / "direct").glob(".upload-*"))
        third = await asyncio.wait_for(_put_slowly(app, path + "3", token, never), 10)
        release.set()
        return third, await asyncio.wait_for(first, 10), await asyncio.wait_for(second, 10), spooled

    third, first, second, spooled = asyncio.run(scenario())
    assert third[0] == 429 and third[1]["code"] == "busy", third
    assert not third_read                                   # refused BEFORE a byte of its body was read
    assert first[0] == 200 and second[0] == 200, (first, second)
    assert len(spooled) == 2                                # the two bodies were on the disk, not in memory
    assert not list((tmp_path / "data" / "direct").glob(".upload-*"))      # and no temporary file is left
    # the slots are free again
    assert _upload(room_server, opened["task_id"], "index", 3, index).status_code == 200


# --- the wave: the server orchestrates, the source sends -----------------------------------------

def test_the_engine_orders_the_source_to_send_and_never_sends_itself(room_server):
    opened, hello = _running_wave(room_server)
    ctx, wave, task = room_server["ctx"], opened["wave_session_id"], opened["task_id"]
    assert hello["session"]["engine"] == "chunk" and hello["session"]["chunk"]["port"] == ctx.chunk.port
    direct = _direct(room_server)
    token = direct["chunk"].get("serve_token")
    assert re.fullmatch(r"[0-9a-f]{32}", token or "")       # the round's token for the frame servers (review of #3216)
    assert direct["engine"] == "chunk" and direct["chunk"] == {
        "serve_ports": {"1": ctx.chunk.port + directchunk.SERVE_OFFSET + 1}, "serve_token": token, "order": None, "done": False, "error": None}

    assert _pass(room_server, wave, "listening", 1, {"S1": [], "S2": []}).json() == {
        "ok": True, "action": "receive", "pass": 1}
    order = _order(room_server, 1)
    assert order == {"seq": 1, "part": 1, "pass": 1, "kbps": order["kbps"], "write_kbps": order["write_kbps"],
                     "group": hello["session"]["chunk"]["group"], "port": ctx.chunk.port,
                     "key": hello["session"]["chunk"]["key"], "chunks": False}
    assert order["kbps"] > 0 and order["write_kbps"] > 0
    assert room_server["recorder"].commands == []           # no chunkcast on the server
    assert ctx.chunk.status()["session_id"] == wave         # the server's own engine orchestrates the wave

    assert _sent(room_server, task, seq=1, state="sending").json() == {"ok": True}
    assert _sent(room_server, task, seq=1, state="done", rc=0).json() == {"ok": True}
    assert _sent(room_server, task, seq=1, state="done", rc=0).json() == {"ok": True, "stale": True}
    assert _ended(room_server, wave, 1, {"S1": [[0, 9]], "S2": [[0, 9]]})["action"] == "done"
    assert _until(lambda: _direct(room_server)["chunk"]["done"])
    # the engine's thread writes `send_done` as it ends, a moment after the task shows done
    assert _until(lambda: [e for e, _d in _events(room_server) if e == "send_done"])
    events = _events(room_server)
    assert [d for e, d in events if e == "send_delegated" and "engine=chunk" in d]
    assert [e for e, _d in events if e in ("send_start", "send_done")] == ["send_start", "send_done"]
    assert not [e for e, _d in events if e == "send_failed"]


def test_what_one_machine_lacks_is_sent_again_by_the_source_and_is_never_a_fetch(room_server):
    opened, _hello = _running_wave(room_server)
    wave, task, anon = opened["wave_session_id"], opened["task_id"], room_server["anon"]
    token = {TOKEN_HEADER: _token(room_server, task)}
    _pass(room_server, wave, "listening", 1, {"S1": [], "S2": []})
    _order(room_server, 1)
    assert anon.get(f"/api/v1/direct/{task}/chunks/1", headers=token).status_code == 404   # a whole pass has no list
    _sent(room_server, task, seq=1, state="done", rc=0)

    answer = _ended(room_server, wave, 1, {"S1": [[0, 4]], "S2": [[0, 9]]})     # one machine, one drawer lacks 5-9
    assert answer["action"] == "receive" and answer["pass"] == 2, answer       # a library wave would say `fetch`
    assert "ranges" not in answer
    _pass(room_server, wave, "listening", 2, {"S1": [[0, 4]], "S2": [[0, 9]]})
    order = _order(room_server, 2)
    assert order["pass"] == 2 and order["chunks"] is True
    listed = anon.get(f"/api/v1/direct/{task}/chunks/2", headers=token)
    assert listed.status_code == 200 and listed.text == "5-9\n"
    assert anon.get(f"/api/v1/direct/{task}/chunks/2").status_code == 401       # the task's token, as the manifest
    _sent(room_server, task, seq=2, state="done", rc=0)
    assert _ended(room_server, wave, 2, {"S1": [[0, 9]], "S2": [[0, 9]]})["action"] == "done"
    assert _until(lambda: _direct(room_server)["chunk"]["done"])
    assert room_server["recorder"].commands == []


def test_the_library_wave_still_lets_one_machine_fetch_the_threshold_is_the_waves():
    """The guard of the other side: `threshold` is a parameter, and its default is what it was."""
    durable = {"a": {"S1": [(0, 4)], "S2": [(0, 9)]}}
    library = repair.plan(10, durable, repairs_done=0)
    assert library.fetch == {"a": [(5, 9)]} and not library.multicast and not library.receive


def test_a_plan_of_a_direct_wave_sends_everything_again_and_fetches_nothing():
    plan = repair.plan(10, {"a": {"S1": [(0, 4)], "S2": [(0, 9)]}, "b": {"S3": [(0, 9)]}},
                       repairs_done=0, threshold=1)
    assert plan.multicast == [(5, 9)] and plan.receive == {"a"} and plan.fetch == {} and not plan.done


def test_a_source_that_fails_a_pass_fails_the_wave_with_its_own_reason(room_server):
    opened, _hello = _running_wave(room_server)
    wave, task = opened["wave_session_id"], opened["task_id"]
    _pass(room_server, wave, "listening", 1, {"S1": [], "S2": []})
    _order(room_server, 1)
    assert _sent(room_server, task, seq=1, state="failed", rc=9, error="הדיסק השתנה בין הקריאות").status_code == 200
    failed = _until(lambda: [d for e, d in _events(room_server) if e == "send_failed"])
    assert "הדיסק השתנה בין הקריאות" in failed[0] and "קוד 9" in failed[0] and "מחיצה 1" in failed[0]
    # the journal line is written before the session itself is failed: the source's next hello, not this instant
    direct = _until(lambda: (d := _direct(room_server))["session_state"] == "failed" and d)
    assert "הדיסק השתנה" in direct["chunk"]["error"] and direct["chunk"]["done"] is False
    assert _pass(room_server, wave, "ended", 1, {"S1": [[0, 2]], "S2": []}).json()["action"] == "closed"
    assert room_server["ctx"].conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()["state"] == "failed"


def test_a_source_that_goes_silent_in_a_pass_fails_the_wave_by_name(room_server):
    opened, _hello = _running_wave(room_server)
    live = room_server["ctx"].direct_chunk
    _pass(room_server, opened["wave_session_id"], "listening", 1, {"S1": [], "S2": []})
    _order(room_server, 1)                                  # the source saw its order -- and is never heard again
    live.poll_s, live.silence = 0.05, lambda: 0.3
    failed = _until(lambda: [d for e, d in _events(room_server) if e == "send_failed"])
    assert "מחשב הבנייה הפסיק לדווח" in failed[0] and "מחיצה 1" in failed[0]


def test_a_bad_report_of_a_pass_is_refused_and_a_foreign_token_never_reports(room_server):
    opened, _hello = _running_wave(room_server)
    task, anon = opened["task_id"], room_server["anon"]
    assert _sent(room_server, task, seq="1", state="done").status_code == 400
    assert _sent(room_server, task, seq=1, state="finished").status_code == 400
    assert anon.post(f"/api/v1/direct/{task}/chunk-sent", content=b"{}", headers={TOKEN_HEADER: "x" * 48}).status_code == 403
    assert anon.post(f"/api/v1/direct/{task}/chunk-sent", content=b"{}").status_code == 401
    room_server["ctx"].chunk.stop(opened["wave_session_id"])


def test_a_source_that_uploaded_no_index_fails_the_wave_by_name(room_server):
    """An agent image from before #3161: the wave is refused -- it does not quietly become a udpcast
    wave (as a cloner with an old image is refused). Since #3173 there is no udpcast to become."""
    opened = _setup(room_server)
    assert _put_manifest(room_server, opened["task_id"], manifest=_live_manifest()).status_code == 200  # no upload
    _cloner(room_server), _cloner(room_server)
    hello = _cloner(room_server)
    failed = [d for e, d in _events(room_server) if e == "send_failed"]
    assert len(failed) == 1 and "אינדקס חתיכות" in failed[0] and "דבר לא שודר" in failed[0]
    assert not [e for e, _d in _events(room_server) if e == "send_delegated"]
    assert hello["session"] is None or "engine" not in hello["session"]
    assert room_server["recorder"].commands == []
    direct = _direct(room_server)
    assert direct["session_state"] == "failed" and "chunk" not in direct      # the source stops waiting on `failed`
    assert room_server["ctx"].conn.execute(
        "SELECT state FROM room_rounds WHERE id = ?", (opened["id"],)).fetchone()["state"] == "failed"


def test_a_machine_that_is_not_in_the_wave_gets_nothing_of_it(room_server):
    opened, _hello = _running_wave(room_server)
    stranger = _pass(room_server, opened["wave_session_id"], "listening", 1, {"S3": []}, mac=CLONER2)
    assert stranger.status_code == 403 and stranger.json()["code"] == "not_member"
    room_server["ctx"].chunk.stop(opened["wave_session_id"])


# --- #3209: what one machine lacks after the pass is over -- a late report, a drawer pushed back in ----

class _Source(http.server.BaseHTTPRequestHandler):
    """The build machine's frame server (`chunkcast serve`), as far as the image server sees it."""
    frames = b"0123456789"          # ten one-byte "frames", as _index() describes them
    asked: list = []
    answer = "frames"
    token = None                    # the round's token: a request without it is 403, as `chunkcast serve` answers

    def do_GET(self):               # noqa: N802 -- http.server's name
        wanted = self.headers.get("Range", "")
        if self.headers.get("X-Imagesrv-Serve-Token") != type(self).token or type(self).token is None:
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        type(self).asked.append(wanted)
        first, last = (int(x) for x in wanted.removeprefix("bytes=").split("-"))
        body = self.frames[first:last + 1] if self.answer == "frames" else b"x"
        self.send_response(206 if self.answer != "refuse" else 416)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):   # quiet
        pass


def _source(room_server, image_id):
    """A frame server on loopback at the place the image server looks for it: the address of the build
    machine's last hello, and the port after the engine's + the partition's index."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _Source.asked, _Source.answer, _Source.token = [], "frames", None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = room_server["ctx"].conn
    conn.execute("UPDATE net_devices SET ip = '127.0.0.1' WHERE mac = (SELECT source_mac FROM room_rounds"
                 " WHERE image_id = ?)", (image_id,))
    conn.commit()
    live = getattr(room_server["ctx"], "direct_chunk", None)
    if live is not None:
        live.serve_port = lambda: port - 1                  # partition 1 is at port
        _Source.token = live.serve_token(image_id)          # what the source got in its hello
    return server


def test_whole_chunks_of_a_live_image_are_read_from_the_build_machine_through_the_server(room_server):
    opened, _hello = _running_wave(room_server)
    anon, url = room_server["anon"], f"/api/v1/images/{opened['image_id']}/files/{FILE}"
    source = _source(room_server, opened["image_id"])
    try:
        got = anon.get(url, headers={"Range": "bytes=5-9"})
        assert got.status_code == 206 and got.content == b"56789"
        assert _Source.asked == ["bytes=5-9"]               # the receiver's own range, as it asked it
        _Source.answer = "short"                            # fewer bytes than the range: never passed on as chunks
        short = anon.get(url, headers={"Range": "bytes=2-4"})
        assert short.status_code == 502 and short.json()["code"] == "source_refused"
        _Source.answer = "refuse"
        assert anon.get(url, headers={"Range": "bytes=2-4"}).json()["code"] == "source_refused"
    finally:
        source.shutdown()
        source.server_close()
        room_server["ctx"].chunk.stop(opened["wave_session_id"])
    gone = anon.get(url, headers={"Range": "bytes=5-9"})    # nobody listens there any more
    assert gone.status_code == 502 and gone.json()["code"] == "source_unreachable"


def test_the_source_gets_a_token_of_the_round_that_is_random_stable_and_never_in_the_journal(room_server):
    """The review of #3216: the build machine's frame servers answer whoever carries this token, and nobody else. It is
    born on the server, goes to the source in the hello, and is the same in every hello of the round."""
    opened, _hello = _running_wave(room_server)
    first = _direct(room_server)["chunk"]["serve_token"]
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert _direct(room_server)["chunk"]["serve_token"] == first               # the frame servers started with this one
    assert room_server["ctx"].direct_chunk.serve_token(opened["image_id"]) == first
    assert all(first not in detail for _event, detail in _events(room_server))
    room_server["ctx"].chunk.stop(opened["wave_session_id"])
    assert room_server["ctx"].direct_chunk.serve_token("live_00000000") != first   # another round, another token


def test_the_relay_carries_the_rounds_token_and_a_source_that_refuses_it_is_named(room_server):
    opened, _hello = _running_wave(room_server)
    anon, url = room_server["anon"], f"/api/v1/images/{opened['image_id']}/files/{FILE}"
    source = _source(room_server, opened["image_id"])
    try:
        assert anon.get(url, headers={"Range": "bytes=5-9"}).status_code == 206     # it carried the token
        _Source.token = "0" * 32                                                    # a frame server of another round
        refused = anon.get(url, headers={"Range": "bytes=5-9"})
        assert refused.status_code == 502 and refused.json()["code"] == "source_refused" and "403" in refused.json()["error"]
    finally:
        source.shutdown()
        source.server_close()
        room_server["ctx"].chunk.stop(opened["wave_session_id"])


def test_a_machine_that_reports_after_its_partition_closed_is_told_to_fetch_and_does_not_fail(room_server, monkeypatch):
    """Until #3209 a late reporter of a direct wave failed by name: the server held nothing to fetch from.
    Now its fetch is relayed to the build machine, so it gets what a library wave gives it."""
    from server.rounds import chunksend

    monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0)
    opened = _setup(room_server, slots=[{"mac": CLONER1, "ports": [1, 2]}, {"mac": CLONER2, "ports": [1, 2]}])
    index, wave, task = _index(), opened["wave_session_id"], opened["task_id"]
    assert _upload(room_server, task, "index", 1, index).status_code == 200
    assert _put_manifest(room_server, task, manifest=_manifest(index)).status_code == 200
    room_server["ctx"].chunk.lost_wait = lambda: 3          # how long the engine waits for the machine that is late
    for _round in range(3):
        _cloner(room_server)
        _cloner(room_server, CLONER2, ("S3", "S4"))
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    _pass(room_server, wave, "listening", 1, {"S1": [], "S2": []})
    _pass(room_server, wave, "listening", 1, {"S3": [], "S4": []}, mac=CLONER2)
    _order(room_server, 1)
    _sent(room_server, task, seq=1, state="done", rc=0)
    assert _ended(room_server, wave, 1, {"S1": [[0, 9]], "S2": [[0, 9]]})["action"] == "done"   # closed without the other
    late = _pass(room_server, wave, "ended", 1, {"S3": [[0, 4]], "S4": [[0, 9]]}, mac=CLONER2).json()
    assert late["action"] == "fetch" and late["ranges"] == [[5, 9]] and "fail" not in late, late
    # and the fetch it then makes is answered: from the build machine, through the server
    source = _source(room_server, opened["image_id"])
    try:
        got = room_server["anon"].get(f"/api/v1/images/{opened['image_id']}/files/{FILE}", headers={"Range": "bytes=5-9"})
        assert got.status_code == 206 and got.content == b"56789"
    finally:
        source.shutdown()
        source.server_close()
    assert _pass(room_server, wave, "ended", 2, {"S3": [[0, 9]], "S4": [[0, 9]]}, mac=CLONER2).json()["action"] == "done"
