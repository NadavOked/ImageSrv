"""‏#2841 — משכפל שהשתתק אחרי `direct_done`: ‏0/3 בשקט, ובלי יומן הסוכן בשרת.

עבודה, 11/11 (‏v0.91.10, ‏`room_eb19e599`): הזרם הסתיים ב-15:19:04, המשכפל דיווח
`verifying` על שתי מגירות עד 15:27:28 ושתק; ‏`client_lost` ב-15:30:28 (בדיוק 180ש'
אחרי), והסבב נסגר `room_done written=0/3` — כמו כל סיום. ב-15:32 המשכפל ביקש PXE
מחדש: הוא **אותחל**, לא כובה. ‏`agent_logs` ריק — הסוכן שולח את היומן רק בסוף סבב
(‏`imagesrv-agent` אחרי `report_final`, ‏`end_failed_round`), וסוף לא הגיע.

שני צדדים:
  * השרת: חבר שאבד עם מגירות פתוחות נרשם ביומן עם מה שהיה פתוח, וסבב ישיר שבו שום
    דבר לא נכתב ומשכפל אבד — נכשל בשמו (`room_failed` + ‏`failed_reason`), לא `room_done`.
  * הסוכן: היומן עולה לשרת בתחילת האימות (‏`checkpoint`), ולפני כל אתחול שהסוכן
    או ה-watchdog יוזמים באמצע עבודה (‏`fatal`).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db
from server.agent import logs as agent_logs, registry
from server.rounds import room as room_mod

from agentlib import flat_lib
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_direct_server import _put_manifest, _setup
from test_server_room import (           # noqa: F401 — ‏room_server הוא fixture
    CLONER2, cloner_hello, heartbeat, room, room_server,
)

LIB = flat_lib()
_AGE = getattr(room_mod, "LOST_SECONDS", 180) + 60


# --- השרת ---------------------------------------------------------------------


def _direct_wave_quiet_mid_readback(room_server) -> dict:
    """סבב ישיר ל-CLONER2 (שתי מגירות), שדיווח `verifying` ואז שתק מעל הספָּף."""
    pytest.importorskip("fastapi")
    anon, ctx = room_server["anon"], room_server["ctx"]
    opened = _setup(room_server, slots=[{"mac": CLONER2, "ports": [1, 2]}])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    wave = opened["wave_session_id"]
    r = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER2, "state": "verifying",
        "targets": [{"dev": d, "bytes_written": 4096, "bytes_total": 4096, "state": "verifying"}
                    for d in ("sda", "sdb")]})
    assert r.status_code == 200, r.text
    old = (datetime.now(timezone.utc) - timedelta(seconds=_AGE)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (old, wave))
    ctx.conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = ? AND mac = ?",
                     (old, wave, CLONER2))
    ctx.conn.commit()
    heartbeat(room_server)              # CLONER1 (לא בסבב) מריץ את tick
    return opened


def _journal(ctx) -> list[tuple[str, str]]:
    return [(r["event"], r["detail"]) for r in ctx.conn.execute("SELECT event, detail FROM journal")]


def test_a_cloner_lost_mid_readback_fails_the_direct_round_by_name(room_server):
    """הבקרה השלילית: היום — `room_done written=0/2` ומצב `closed`, כמו סיום רגיל."""
    opened = _direct_wave_quiet_mid_readback(room_server)
    ctx = room_server["ctx"]
    row = ctx.conn.execute("SELECT state, failed_reason FROM room_rounds WHERE id = ?",
                           (opened["id"],)).fetchone()
    assert row["state"] == "failed", "0/2 עם משכפל שאבד נסגר כאילו הסתיים"
    assert "הפסיק לדווח לפני שדיווח תוצאה" in (row["failed_reason"] or "")
    assert "0/2" in row["failed_reason"] and "באימות" in row["failed_reason"]
    events = [e for e, _ in _journal(ctx)]
    assert "room_failed" in events and "room_done" not in events


def test_the_lost_line_names_what_was_left_open(room_server):
    _direct_wave_quiet_mid_readback(room_server)
    lost = [d for e, d in _journal(room_server["ctx"]) if e == "client_lost"]
    assert len(lost) == 1
    assert "last=verifying" in lost[0] and "unreported=sda:verifying,sdb:verifying" in lost[0]


def test_unreadable_targets_are_not_no_open_drawers():
    """‏targets_json שלא נקרא — "לא ידוע", לא "אין מה שנשאר פתוח" (עיקרון 5)."""
    assert room_mod._unreported({"targets_json": "{not json"}) == [("?", "unread")]
    assert room_mod._unreported({"targets_json": json.dumps(
        [{"dev": "sda", "state": "done"}, {"dev": "sdb", "state": "verifying"}])}) == [("sdb", "verifying")]


@pytest.mark.parametrize("reason", ["checkpoint", "fatal"])
def test_the_server_keeps_a_mid_round_log(tmp_path, reason):
    conn = db.connect(tmp_path / "imagesrv.db")
    registry.add_machine(conn, "aa:bb:cc:dd:28:41", "cloner-1", "grp_CLONERS", "test")
    got = agent_logs.receive(conn, "aa:bb:cc:dd:28:41", "ses_1", reason,
                             gzip.compress(b"[15:27:28] readback\n"), known=True)
    assert got["ok"], got


# --- הסוכן --------------------------------------------------------------------


def _fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\n"
                    f'for a in "$@"; do echo "$a"; done >> {posix(tmp_path)}/curl.args\n'
                    "exit 0\n", encoding="utf-8", newline="\n")
    curl.chmod(0o755)
    return bin_dir


def _run_dir(tmp_path: Path, state: str | None) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "agent.log").write_text("[15:27:28] readback: 186GB per disk\n", encoding="utf-8", newline="\n")
    (run / "response.json").write_text('{"session": {"id": "ses_930ae04f"}}', encoding="utf-8")
    if state is not None:
        (run / "state").write_text(state + "\n", encoding="utf-8", newline="\n")
    return run


def _curl_urls(tmp_path: Path) -> list[str]:
    f = tmp_path / "curl.args"
    return [a for a in f.read_text(encoding="utf-8").splitlines() if "/agent/log" in a] if f.exists() else []


@requires_native(("bash", BASH))
def test_the_watchdog_sends_the_log_before_it_reboots(tmp_path):
    """שחזור עם שעון מזויף: חוזה ה-600ש' פג (הסוכן הראשי לא חידש) — היום אתחול בלי
    בקשה אחת לשרת; אחרי: היומן עם `reason=fatal`, ורק אז `reboot -f`."""
    run, bin_dir = _run_dir(tmp_path, "verifying"), _fake_bin(tmp_path)
    out = sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH"; RUN_DIR={posix(run)!r};'
             f' WATCHDOG_UPTIME="$RUN_DIR/uptime"; . {posix(LIB)}/watchdog.sh;'
             ' reboot() { echo "REBOOT $*"; exit 0; };'
             " echo '0.0 0' > \"$WATCHDOG_UPTIME\"; echo 0 > \"$RUN_DIR/watchdog.beat\";"
             ' WATCHDOG_DEVICE="$RUN_DIR/none"; WD_SERVER=http://20.0.0.1:8080; WD_MAC=64:31:50:42:b1:9c;'
             " sleep() { echo '600.0 0' > \"$WATCHDOG_UPTIME\"; }; watchdog_supervise 2>&1")
    assert "REBOOT -f" in out, out
    assert _curl_urls(tmp_path) == [
        "http://20.0.0.1:8080/api/v1/agent/log?mac=64:31:50:42:b1:9c&session_id=ses_930ae04f&reason=fatal"], out
    assert out.index("the agent log was sent") < out.index("REBOOT -f")


@requires_native(("bash", BASH))
def test_the_watchdog_without_a_server_still_reboots_and_says_so(tmp_path):
    run, bin_dir = _run_dir(tmp_path, "verifying"), _fake_bin(tmp_path)
    out = sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH"; RUN_DIR={posix(run)!r};'
             f' . {posix(LIB)}/watchdog.sh; reboot() {{ echo "REBOOT $*"; exit 0; }}; watchdog_reboot 2>&1')
    assert "REBOOT -f" in out and "the log is not sent" in out
    assert _curl_urls(tmp_path) == []


def test_the_supervisor_is_told_where_the_server_is():
    src = (LIB / "watchdog.sh").read_text(encoding="utf-8")
    start = src[src.index("watchdog_start() {"):]
    assert 'WD_SERVER=$3; WD_MAC=$4' in start and '"${SERVER:-}" "${MAC:-}"' in start


def _agent(tmp_path: Path, run: Path, bin_dir: Path, body: str) -> str:
    return sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH" RUN_DIR={posix(run)!r}'
              f' LOG_FILE={posix(run)!r}/agent.log SERVER=http://20.0.0.1:8080 MAC=64:31:50:42:b1:9c'
              f' IMAGESRV_TEST=1; . {posix(LIB)}/common.sh; . {posix(LIB)}/jsonq.sh;'
              f' . {posix(LIB)}/datareport.sh; RESP="$RUN_DIR/response.json"; sleep() {{ :; }}; {body}')


@requires_native(("bash", BASH))
def test_die_local_with_a_job_open_sends_the_log_first(tmp_path):
    run, bin_dir = _run_dir(tmp_path, "verifying"), _fake_bin(tmp_path)
    out = _agent(tmp_path, run, bin_dir, '( die_local "readback broke" ); echo "rc=$?"')
    assert "TEST-REBOOT: readback broke" in out
    assert _curl_urls(tmp_path) == [
        "http://20.0.0.1:8080/api/v1/agent/log?mac=64:31:50:42:b1:9c&session_id=ses_930ae04f&reason=fatal"]


@requires_native(("bash", BASH))
def test_die_local_with_no_job_sends_nothing(tmp_path):
    """ה-"no work for this machine" היומיומי של כל תחנה אינו בקשה לשרת."""
    run, bin_dir = _run_dir(tmp_path, None), _fake_bin(tmp_path)
    out = _agent(tmp_path, run, bin_dir, '( die_local "no work for this machine" ); echo "rc=$?"')
    assert "TEST-REBOOT: no work for this machine" in out
    assert _curl_urls(tmp_path) == []


@requires_native(("bash", BASH))
@pytest.mark.parametrize("role,sent", [("cloner", True), ("classroom", False)])
def test_the_readback_checkpoint_is_a_cloners_only(tmp_path, role, sent):
    run, bin_dir = _run_dir(tmp_path, "verifying"), _fake_bin(tmp_path)
    _agent(tmp_path, run, bin_dir, f"D_ROLE={role}; agent_log_checkpoint")
    want = ["http://20.0.0.1:8080/api/v1/agent/log?mac=64:31:50:42:b1:9c"
            "&session_id=ses_930ae04f&reason=checkpoint"] if sent else []
    assert _curl_urls(tmp_path) == want


def test_readback_drawers_takes_the_checkpoint_before_the_disks_start():
    src = (LIB / "readback.sh").read_text(encoding="utf-8")
    body = src[src.index("readback_drawers() {"):]
    assert body.index("agent_log_checkpoint") < body.index("_rb_start")
