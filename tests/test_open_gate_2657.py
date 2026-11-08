"""#2657 -- nothing touches a disk before the server confirms the job is open
for this machine, and the server serves an image only inside such a job.

Two halves, both principle 5 ("we could not check" is not "it is fine"):

* **The agent** (`agent/lib/core/opengate.sh`). A hello answer that says
  "restore" names a round; it is not evidence that the round is still open.
  Before the write check, TRIM and the partition table (and, in the cloning
  room, the SMART/RAID gates), the agent sends the job's first progress report
  and goes on only on `ok:true` that carries the same `session_id` back. Any
  other reply -- `not_open`, `not_member`, a member that already ended, an
  error, no answer, a body that is not JSON, another id -- ends at the local
  disk with nothing written (principle 1). Here the write is a stand-in that
  calls `blkdiscard` and `sgdisk --zap-all` from PATH, and the stand-ins only
  record: the evidence is that they were never called.
* **The server.** `reports.ingest` returns the `session_id` only for a report
  it recorded for an active member; `api.source_gate` serves manifest, files
  and the read-back list only to a machine that is a member of an open round
  or pull of *that* image (the GUI restore's console session opens the
  manifest alone, before its pull).
"""

from __future__ import annotations

import json
import shlex
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from server.agent import reports
from server.console.auth import COOKIE_NAME
from test_agent import AGENT, posix
from test_closed_session_report import _payload, _session
from test_final_report import IMAGE, SESSION, native_tools, run_path
from test_machine_identity import (IMAGE as LIB_IMAGE, MAC1, STATION_IP,  # noqa: F401
                                   identity_server, journal_details)


# --- the agent: the gate before the disk -------------------------------------

#: A disk tool that only writes down that it was called.
DISK_TOOL = '#!/bin/sh\nprintf "%s %s\\n" "${0##*/}" "$*" >> "$DISK_CALLS"\nexit 0\n'

#: The first destructive steps of the real write (restore.sh / drawers.sh:
#: TRIM, then the table), and the gates in front of it -- recorded, so a run
#: that got past the open-round gate leaves a trace.
WRITE = (
    'smart_gate() { echo SMART >> "$RUN_DIR/trace"; shift; echo "$@"; }; '
    'drawer_gates() { echo DRAWER_GATES >> "$RUN_DIR/trace"; echo "$@"; }; '
    'run_restore() { blkdiscard "/dev/$2"; sgdisk --zap-all "/dev/$2"; '
    'echo verifying > "$RUN_DIR/state"; target_set "$2" done; return 0; }; '
    'run_restore_drawers() { shift 4; for _t in "$@"; do blkdiscard "/dev/$_t"; '
    'sgdisk --zap-all "/dev/$_t"; target_set "$_t" done; done; '
    'echo verifying > "$RUN_DIR/state"; return 0; }; '
)

PATHS = {
    "station": "do_restore",
    "cloner": f"do_restore_drawers {IMAGE} {SESSION}",
}


class _Server(HTTPServer):
    status = 200
    body = b""
    posts: list[tuple[str, dict | str]] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:                       # noqa: N802 -- BaseHTTP's name
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            parsed: dict | str = json.loads(raw)
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        self.server.posts.append((self.path, parsed))
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, *_args) -> None:
        """No noise on the run's stderr."""


@pytest.fixture
def progress_server():
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.posts = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _url(httpd: HTTPServer) -> str:
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}"


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _run(tmp_path: Path, server: str, call: str) -> dict:
    tools = tmp_path / "disk-tools"
    tools.mkdir()
    for name in ("sgdisk", "blkdiscard"):
        (tools / name).write_text(DISK_TOOL, encoding="utf-8", newline="\n")
        (tools / name).chmod(0o755)
    calls = tmp_path / "disk.calls"
    gate = AGENT / "lib" / "core" / "opengate.sh"
    # The real gate, over test_final_report's isolating stub. A tree without
    # the file (the negative control) keeps that stub -- and its agent never
    # calls it.
    stubs = f"[ -f {shlex.quote(posix(gate))} ] && . {shlex.quote(posix(gate))}; " + WRITE
    env = (f"DISK_CALLS={shlex.quote(posix(calls))} "
           f'PATH={shlex.quote(posix(tools))}:"$PATH"')
    result = run_path(tmp_path, server, call, stubs=stubs, env=env)
    result["disk"] = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result


#: (status, body) of the reply to the first report, and a word of the reason the
#: machine names before it boots its local disk.
REFUSALS = {
    "the round is closed": (200, b'{"ok":false,"code":"not_open","error":"this session is no longer open"}',
                            "is not open"),
    "ok without the round's id": (200, b'{"ok":true}', "not confirmed open"),
    "ok for another round": (200, b'{"ok":true,"session_id":"ses_other"}', "not confirmed open"),
    "not a member": (400, b'{"ok":false,"code":"not_member"}', "did not confirm"),
    "a body that is not json": (200, b"ok", "not confirmed open"),
}


@native_tools
@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_round_the_server_does_not_confirm_touches_no_disk(tmp_path, progress_server, path, case):
    progress_server.status, progress_server.body, why = REFUSALS[case]

    result = _run(tmp_path, _url(progress_server), PATHS[path])

    assert result["disk"] == [], f"{case}: the disk was touched -- {result['disk']}"
    assert "TEST-REBOOT" in result["out"] and why in result["out"], result["out"]
    assert "SMART" not in result["trace"] and "DRAWER_GATES" not in result["trace"], result["trace"]
    # One question, by name, before anything else: this job's first report.
    assert [p for p, _ in progress_server.posts] == ["/api/v1/agent/progress"], progress_server.posts
    first = progress_server.posts[0][1]
    assert first["session_id"] == SESSION and first["state"] == "waiting", first


@native_tools
@pytest.mark.parametrize("path", sorted(PATHS))
def test_a_server_that_does_not_answer_touches_no_disk(tmp_path, path):
    result = _run(tmp_path, _closed_port_url(), PATHS[path])

    assert result["disk"] == [], result["disk"]
    assert "TEST-REBOOT" in result["out"] and "did not confirm" in result["out"], result["out"]
    assert "SMART" not in result["trace"], result["trace"]


@native_tools
@pytest.mark.parametrize("path", sorted(PATHS))
def test_a_confirmed_round_goes_on_to_the_disk(tmp_path, progress_server, path):
    """The positive side: without it the cases above could pass on a gate that
    refuses everything -- and on disk stand-ins that never record."""
    progress_server.body = json.dumps({"ok": True, "session_id": SESSION}).encode()

    result = _run(tmp_path, _url(progress_server), PATHS[path])

    assert "TEST-REBOOT" not in result["out"], result["out"]
    assert any(c.startswith("sgdisk --zap-all") for c in result["disk"]), result
    assert any(c.startswith("blkdiscard") for c in result["disk"]), result
    assert "SMART" in result["trace"], result["trace"]


def _body(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end)]


def test_the_gate_stands_before_every_disk_step_of_both_paths():
    """Where the call sits, read off the script: before the reporter, the
    SMART gate (cloning room: also the RAID/split gates) and the write."""
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    drawers = _body(agent, "do_restore_drawers() {", "do_restore() {")
    station = _body(agent, "do_restore() {", "# --- main loop")
    for body, write in ((drawers, "run_restore_drawers"), (station, "run_restore ")):
        gate = body.index('session_open_gate "')
        # target_init first: the report carries this job's targets (#1916).
        assert (body.index('target_init "') < gate < body.index('progress_loop "')
                < body.index('smart_gate "') < body.index(write)), body
    assert drawers.index('session_open_gate "') < drawers.index("drawer_gates")


# --- the server: the reply that opens the gate --------------------------------

def test_an_accepted_round_report_names_its_round(server):
    mac = _session(server, "ses_open")
    reply = reports.ingest(server["app"].state.ctx.conn, _payload("ses_open", mac, "waiting"))
    assert reply["ok"] is True and reply["session_id"] == "ses_open", reply


def test_a_member_that_already_ended_gets_ok_without_the_id(server):
    """The idempotent answer of #771 stays `ok` -- and does not open the gate:
    this machine's part of the round is over."""
    mac = _session(server, "ses_ended")
    conn = server["app"].state.ctx.conn
    conn.execute("UPDATE session_members SET state = 'done', done = 1 WHERE session_id = 'ses_ended'")
    conn.commit()
    reply = reports.ingest(conn, _payload("ses_ended", mac, "waiting"))
    assert reply == {"ok": True}, reply


def test_a_machine_that_is_not_a_member_gets_no_id(server):
    _session(server, "ses_theirs")
    reply = reports.ingest(server["app"].state.ctx.conn,
                           _payload("ses_theirs", "b4:2e:99:07:1a:c5", "waiting"))
    assert reply["code"] == "not_member" and "session_id" not in reply, reply


def test_a_closed_round_reply_carries_no_id(server):
    mac = _session(server, "ses_shut", state="closed")
    reply = reports.ingest(server["app"].state.ctx.conn, _payload("ses_shut", mac, "waiting"))
    assert reply["code"] == "not_open" and "session_id" not in reply, reply


# --- the server: an image only inside an open round or pull -------------------

OTHER_IMAGE = "img_2c8e04"
MANIFEST = f"/api/v1/images/{LIB_IMAGE}/manifest"
FILE = f"/api/v1/images/{LIB_IMAGE}/files/p1.esp.pcl.zst"
READBACK = f"/api/v1/images/{LIB_IMAGE}/readback/1"


def _membership(srv, sid: str, image: str, state: str) -> None:
    ids = setup_classroom(srv)
    conn = srv["ctx"].conn
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, ?, ?, 'LAB1', 2, 300, ?, 'noc', '2026-11-07T00:00:00+00:00', 0.0, 'multicast')",
        (sid, ids["group"], image, state))
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, done, bytes_written, bytes_total,"
        " updated_at) VALUES (?, ?, 'waiting', 0, 0, 0, '2026-11-07T00:00:00+00:00')",
        (sid, MAC1))
    conn.commit()


def _refused(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "image_fetch_refused", response.text


@pytest.mark.parametrize("url,where", [(MANIFEST, "manifest"), (FILE, "files"), (READBACK, "readback")])
def test_a_registered_machine_without_an_open_job_gets_no_image(identity_server, url, where):
    setup_classroom(identity_server)
    _refused(identity_server["station"].get(url))
    rows = [d for _u, d in journal_details(identity_server, "image_fetch_refused")]
    assert any(STATION_IP in d and f"({where})" in d for d in rows), rows


@pytest.mark.parametrize("state", ["open", "running"])
def test_a_member_of_an_open_round_of_that_image_gets_it(identity_server, state):
    _membership(identity_server, "ses_live", LIB_IMAGE, state)
    ok = identity_server["station"].get(MANIFEST)
    assert ok.status_code == 200 and ok.json()["id"] == LIB_IMAGE, ok.text
    assert identity_server["station"].get(FILE).status_code == 200


def test_a_round_of_another_image_does_not_open_this_one(identity_server):
    _membership(identity_server, "ses_other", OTHER_IMAGE, "running")
    _refused(identity_server["station"].get(MANIFEST))
    _refused(identity_server["station"].get(FILE))
    assert identity_server["station"].get(f"/api/v1/images/{OTHER_IMAGE}/manifest").status_code == 200


@pytest.mark.parametrize("state", ["closed", "failed"])
def test_a_round_that_ended_does_not_open_it(identity_server, state):
    _membership(identity_server, "ses_over", LIB_IMAGE, state)
    _refused(identity_server["station"].get(MANIFEST))
    _refused(identity_server["station"].get(FILE))


def test_a_pull_the_machine_opened_opens_the_files(identity_server):
    setup_classroom(identity_server)
    _refused(identity_server["station"].get(FILE))
    opened = identity_server["station"].post("/api/v1/agent/pulls", json={
        "mac": MAC1, "image_id": LIB_IMAGE, "username": "labtech", "password": "deploy-pass-1"})
    assert opened.status_code == 200, opened.text
    assert identity_server["station"].get(FILE).status_code == 200
    assert identity_server["station"].get(MANIFEST).status_code == 200


def test_the_gui_console_session_opens_the_manifest_and_nothing_else(identity_server):
    """The GUI restore reads the manifest before its pull opens (the split
    question, #1898) and carries the operator's console session to do it."""
    from fastapi.testclient import TestClient
    setup_classroom(identity_server)
    station = TestClient(identity_server["app"], client=(STATION_IP, 40011))
    station.cookies.set(COOKIE_NAME, identity_server["deploy"].cookies.get(COOKIE_NAME))
    assert station.get(MANIFEST).status_code == 200
    _refused(station.get(FILE))
    _refused(station.get(READBACK))
    station.cookies.set(COOKIE_NAME, "not-a-session")
    _refused(station.get(MANIFEST))
