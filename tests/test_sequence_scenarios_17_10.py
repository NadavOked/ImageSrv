"""Sequence regressions found on metal on 17/10.

These tests deliberately exercise two or more actions against the same real
server runtime.  They use the HTTP-facing simulated machines from the existing
room, pull, sender and kiosk fixtures.  No agent shell runs, except the one
link where the bug lived in the agent's jq (``_gui_state_lines``).

Each ``xfail(strict=True)`` names the PR that fixes it; when that PR is merged
the test XPASSes (strict -> red) and the marker must be dropped.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from native import requires_native
from server.rounds import drive_rates, room as room_mod
from test_class_deploy_switch import PRELUDE, STATION
from test_cloner_gui import BASH, posix, sh
from test_cloner_round_server import ask, cloner, now  # noqa: F401  (autouse: the route's clock)
from test_kiosk_app import _login_kiosk, kiosk_env  # noqa: F401
from test_machine_identity import identity_server  # noqa: F401
from test_sender import free_ports  # noqa: F401
from test_server_room import (CLONER1, CLONER2, cloner_hello, heartbeat,
                              report as room_report, room, room_body,
                              room_server)  # noqa: F401
from test_server_streams import overview, pull, report, second_class
from test_wol import macs_of


#: The question the agent asks for a drawer whose last clone failed (same shape
#: as ``test_sender.SMART_ASK``, which only exists once #1606 is merged).
SMART_ASK = {"nonce": "3-2", "port": 2, "verdict": "failed_last", "reason": "נכשל בשיכפול הקודם"}


def _gui_state_lines(tmp_path, room_view: dict) -> list[str]:
    """What the build machine's GUI agent (guistate.sh) makes of the room view
    the server really answered -- the link where 17/10's failed round hijacked
    the picker.  Needs bash and jq (the VM lab has them)."""
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "station.json").write_text(json.dumps(STATION), newline="\n")
    (run / "room.json").write_text(json.dumps(room_view), newline="\n")
    (gui / "mode").write_text("room\n", newline="\n")
    out = sh(f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
             + PRELUDE
             + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; '
             + f'gui_get() {{ case "$1" in room) cp {posix(run / "room.json")!r} "$2" ;; '
             + 'images) printf "[]" > "$2" ;; *) return 1 ;; esac; }; '
             + 'gui_state')
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines()


@requires_native(("bash", BASH), "jq", why="gui_state builds the screen state in jq")
def test_failed_room_round_then_new_round_is_live(identity_server, tmp_path):
    srv = identity_server
    cloner(srv)
    first = ask(srv)
    assert first.status_code == 200, first.text
    old = room_mod.active_round(srv["ctx"].conn)
    reason = "no machine joined within 180 s"
    assert srv["ctx"].store.fail(
        old["wave_session_id"], reason,
        related=lambda: room_mod.fail_wave(
            srv["ctx"].conn, old["wave_session_id"], reason, commit=False))

    # The build machine's picker must not turn the failed round into the live
    # round screen: one red line says why, and no `round=` is drawn.
    lines = _gui_state_lines(tmp_path, srv["admin"].get("/api/console/room").json())
    assert any(l.startswith("room_failed=") for l in lines), lines
    assert not any(l.startswith("round=") for l in lines), lines

    second = ask(srv, ports=[2])
    assert second.status_code == 200, second.text
    assert second.json()["id"] != old["id"]
    visible = srv["admin"].get("/api/console/room").json()["round"]
    assert visible["id"] == second.json()["id"] and visible["state"] == "active"
    saved = srv["ctx"].conn.execute(
        "SELECT state, failed_reason FROM room_rounds WHERE id = ?", (old["id"],)).fetchone()
    assert (saved["state"], saved["failed_reason"]) == ("failed", reason)


@pytest.mark.usefixtures("classrooms_on")
def test_failed_pull_then_new_pull_closes_old(server):
    lab = second_class(server)
    first = pull(server, lab["mac1"])
    assert first.status_code == 200, first.text
    assert report(server, first.json()["id"], lab["mac1"], "failed").status_code == 200

    second = pull(server, lab["mac1"], image="img_2c8e04")
    assert second.status_code == 200, second.text
    assert second.json()["id"] != first.json()["id"]
    old = server["ctx"].conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (first.json()["id"],)).fetchone()
    assert old["state"] == "failed"
    assert [p["id"] for p in overview(server)["pulls"]] == [second.json()["id"]]


@pytest.mark.parametrize("delay", [0.0, 5.0])
def test_smart_ask_after_send_start_pauses_timeout_then_resumes(
        server_with_sender, free_ports, monkeypatch, delay):
    from server.rounds import sender as sender_module
    from test_sender import NOBODY_JOINED_LOG, TRANSFER_LOG, wait_for
    from test_server_streams import setup_classroom

    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02, raising=False)
    monkeypatch.setattr(sender_module, "PACE_POLL", 0.02)
    server, recorder = server_with_sender
    recorder.log_text = NOBODY_JOINED_LOG
    ids = setup_classroom(server)
    server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91",
        "prefix": "LAB1", "expected_clients": 1})
    for _ in range(2):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    threading.Event().wait(delay)
    server["anon"].post("/api/v1/agent/hello", json={
        **hello_body(ids["mac1"]), "ask": SMART_ASK})
    assert wait_for(lambda: recorder.processes[0].terminated)
    # Represents 200 seconds of unanswered wall time without sleeping.
    asked = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    server["ctx"].conn.execute("UPDATE smart_asks SET asked_at = ? WHERE mac = ?",
                               (asked, ids["mac1"]))
    server["ctx"].conn.commit()
    assert not any(r["event"] == "send_failed" for r in
                   server["ctx"].conn.execute("SELECT event FROM journal"))
    recorder.log_text = TRANSFER_LOG
    server["anon"].post("/api/v1/agent/hello", json={
        **hello_body(ids["mac1"]), "ask": SMART_ASK,
        "ask_answered": {"nonce": SMART_ASK["nonce"], "answer": "skip"}})
    assert wait_for(lambda: len(recorder.commands) == 2)
    assert recorder.commands[0][recorder.commands[0].index("--file") + 1] == \
        recorder.commands[1][recorder.commands[1].index("--file") + 1]


# Server-side guard, green on main: #1603 fixes the agent (a drawer that died
# mid-stream is not reported as a disk fault); the server already tells a lost
# machine from a disk_failure, and must keep doing so.
def test_lost_machine_advances_wave_without_disk_failure(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    heartbeat(room_server)
    room_report(anon, wave, CLONER1, {"sda": "done", "sdb": "done"})
    writing = {"session_id": wave, "mac": CLONER2, "state": "writing", "targets": [
        {"dev": d, "bytes_written": 40, "bytes_total": 100, "state": "writing"}
        for d in ("sda", "sdb")]}
    assert anon.post("/api/v1/agent/progress", json=writing).status_code == 200
    old = (datetime.now(timezone.utc) - timedelta(seconds=room_mod.LOST_SECONDS + 60)).isoformat()
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (old, wave))
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
                     (old, wave, CLONER2))
    ctx.conn.commit()
    heartbeat(room_server)
    state = ctx.conn.execute("SELECT state FROM session_members WHERE session_id=? AND mac=?",
                             (wave, CLONER2)).fetchone()["state"]
    assert state == "lost"
    assert room(deploy)["round"]["wave_number"] == 2
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "client_lost" in events and "disk_failure" not in events


# Server-side guard, green on main: a failed drawer is recorded as a failure
# while its sibling that finished is still counted written.
def test_failed_drawer_does_not_poison_completed_drawer(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    heartbeat(room_server)
    response = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "failed", "targets": [
            {"dev": "sda", "serial": "S1", "state": "failed", "error": "write error",
             "ata_log": ["ata1.00: failed command: WRITE FPDMA QUEUED"]},
            {"dev": "sdb", "serial": "S2", "state": "done", "bytes_written": 100,
             "bytes_total": 100}]})
    assert response.status_code == 200, response.text
    heartbeat(room_server)      # the wave is finished and tallied on the next agent event
    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["target_drives"] == 2
    failures = ctx.conn.execute("SELECT serial FROM disk_failures WHERE cleared_at IS NULL").fetchall()
    assert [r["serial"] for r in failures] == ["S1"]


def test_build_gui_task_boot_cancel_does_not_cancel_console_task(kiosk_env):
    from fastapi.testclient import TestClient

    console = TestClient(kiosk_env["console_app"])
    assert console.post("/api/console/login", json={
        "username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert console.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:10", "name": "build-01", "group_id": "grp_BUILD"}).status_code == 200
    gui = _login_kiosk(kiosk_env)
    body = {"mac": "aa:bb:cc:00:00:10", "disk": "sda", "name": "Win11 lab", "folder": "Lab"}
    # One open task per machine: the GUI order is cancelled by the boot first,
    # and only then can the operator order from the console.
    gui_id = gui.post("/api/console/tasks/capture", json=body).json()["id"]
    assert kiosk_env["anon"].get("/boot/menu?mac=aa:bb:cc:00:00:10").status_code == 200
    console_response = console.post("/api/console/tasks/capture", json=body)
    assert console_response.status_code == 200, console_response.text
    console_id = console_response.json()["id"]
    assert kiosk_env["anon"].get("/boot/menu?mac=aa:bb:cc:00:00:10").status_code == 200
    rows = {r["id"]: r for r in kiosk_env["rt"].conn.execute(
        "SELECT id, state, error FROM tasks WHERE id IN (?, ?)", (gui_id, console_id))}
    assert rows[gui_id]["state"] == "cancelled" and rows[gui_id]["error"]
    assert rows[console_id]["state"] == "pending"


def test_selective_wake_and_malformed_body_wakes_nothing(room_server):
    room_server["woken"].clear()
    selected = room_server["deploy"].post(
        "/api/console/room/wake", json={"macs": [CLONER2.upper()]})
    assert selected.status_code == 200 and selected.json()["sent"] == 1
    assert macs_of(room_server["woken"]) == {CLONER2}
    room_server["woken"].clear()
    malformed = room_server["deploy"].post(
        "/api/console/room/wake", content=b'{"mac": ["aa:bb:cc:00:00:21"]}',
        headers={"content-type": "application/json"})
    assert malformed.status_code == 400
    assert room_server["woken"] == []


def test_three_clean_waves_raise_rate_even_with_one_failed_drawer(tmp_path):
    from server import db

    conn = db.connect(tmp_path / "rate.db")
    sent = 10 ** 10
    blocks = -(-sent // drive_rates.UDPCAST_BLOCK)
    mac = "aa:bb:cc:00:00:31"
    rates = []
    for number in range(3):
        sid = f"ses_{number}"
        drives = [{"serial": f"{sid}-good", "model": "EVO", "mac": mac, "port": 1},
                  {"serial": f"{sid}-bad", "model": "EVO", "mac": mac, "port": 2}]
        rates.append(drive_rates.decide(conn, sid, lambda d=drives: d, None))
        cap = drive_rates.parse_bitrate(rates[-1]) // 8
        conn.execute("INSERT INTO drive_rates (serial,model,session_id,rate_bps,cap_bps,bytes,seconds,at) VALUES (?,?,?,?,?,?,?,?)",
                     (drives[0]["serial"], "EVO", sid, cap, cap, cap * 600, 600, "t"))
        conn.commit()
        drive_rates.record_stream(conn, sid, {"sent_bytes": sent,
            "loss_blocks": int(blocks * 0.001), "stream_bps": int(cap * .95), "stream_s": 600})
        drive_rates.record_wave(conn, sid, [{"mac": mac, "targets_json": json.dumps([
            {"dev": "sda", "state": "done"}, {"dev": "sdb", "state": "failed"}]),
            "udp_drops_json": json.dumps({"rcvbuf": int(blocks * 0.001), "inerrors": 0})}])
    assert rates[0] < rates[1] < rates[2]
    assert drive_rates.choose(conn, [{"serial": "next", "model": "EVO", "mac": mac, "port": 1}], None)["basis"] == "up"
