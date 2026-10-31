"""‏#1882 — הלוג של המשכפל נשמר בשרת: קטע אחרון דחוס בסוף כל סבב, וכשלים מוכרים בעברית.

שלושה צדדים: (1) השרת מסנן סודות ומידע מהדיסק **לפני** שנשמר בייט (#1924), ומזהה חתימות;
(2) התקרות — גוף, פריסה, שורות למכונה — כדי שהדיסק של השרת לא יתמלא; (3) הסוכן שולח פעם
אחת, ושליחה שנכשלה אינה משנה דבר מלבד שורת לוג מקומית.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server.agent import agent_logs, registry
from server import db
from server.system import housekeeping
from agentlib import flat_lib

MAC = "aa:bb:cc:dd:18:82"

SECRET_LOG = """[10:00:01] hello: Cookie: imagesrv_session=abc.def.ghi
[10:00:02] curl -H X-Imagesrv-Task-Token: 7f3e9a0b11
[10:00:03] login token=s3cr3t-value&next=1 password=hunter2
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAA
-----END OPENSSH PRIVATE KEY-----
[10:00:04] partition 3: 3F2504E0-4F89-11D3-9A0C-0305E82C3301 unique guid
[10:00:05] blkid: LABEL="Nadav-Data" PARTLABEL="Basic data partition" UUID="1A2B-3C4D"
[10:00:06] ntfs serial 01D9A1B2C3D4E5F6 on sda3
[10:00:07] hostname verify failed: wrote 'LAB-07', read back computer='DESKTOP-OLD1' hostname='desk' nv='x' (rc=0/0)
[10:00:08] could not read Select\\Current (rc=1, raw='0x00000002')
[10:00:09] C:\\Users\\moshe\\Desktop and /home/rina/.cache
"""


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    registry.add_machine(c, MAC, "cloner-9", "grp_CLONERS", "test")
    return c


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


# --- סינון -------------------------------------------------------------------


@pytest.mark.parametrize("leak", [
    "abc.def.ghi", "7f3e9a0b11", "s3cr3t-value", "hunter2", "b3BlbnNzaC1rZXktdjEAAAAA",
    "3F2504E0-4F89-11D3-9A0C-0305E82C3301", "Nadav-Data", "Basic data partition", "1A2B-3C4D",
    "01D9A1B2C3D4E5F6", "DESKTOP-OLD1", "'desk'", "0x00000002", "moshe", "rina"])
def test_nothing_secret_or_read_from_a_disk_is_stored(conn, leak):
    got = agent_logs.receive(conn, MAC, "ses_1", "failed", _gz(SECRET_LOG), known=True)
    assert got["ok"], got
    text = agent_logs.text_of(conn, got["id"])["text"]
    assert leak not in text, text
    raw = conn.execute("SELECT text_gz, signatures_json FROM agent_logs").fetchone()
    assert leak.encode() not in gzip.decompress(raw["text_gz"])
    assert leak not in raw["signatures_json"]


def test_redaction_keeps_the_line_and_says_what_was_there(conn):
    text = agent_logs.redact(SECRET_LOG)
    assert "Cookie: <redacted>" in text and "<guid> unique guid" in text
    assert "LABEL=<label>" in text and "<private-key>" in text
    assert "[10:00:06] ntfs serial <serial> on sda3" in text


# --- חתימות ------------------------------------------------------------------


@pytest.mark.parametrize("line,key", [
    ("ata3.00: error: { ICRC ABRT }", "crc"),
    ("ata3: hard resetting link", "link_reset"),
    ("sd 2:0:0:0: rejecting I/O to offline device", "offline"),
    ("partclone: write failed: Input/output error", "io_error"),
    ("zstd: /*stdin*\\: Data corruption detected (corrupt block)", "zstd"),
    ("partition 2 (2.img): sha256 mismatch", "sha"),
    ("hivexget: key not found", "hivex"),
    ("partition 3 (3.img): write failed (rc=1)", "partition_write"),
])
def test_a_known_failure_is_named_in_hebrew(line, key):
    found = agent_logs.classify(f"[09:59:59] start\n[10:00:00] {line}\n")
    assert key in [f["key"] for f in found], found
    hit = next(f for f in found if f["key"] == key)
    assert hit["line"].endswith(line) and any("\u0590" <= c <= "\u05ff" for c in hit["he"])


def test_an_unknown_failure_is_not_guessed():
    assert agent_logs.classify("[10:00:00] something odd happened\n") == []


# --- תקרות ------------------------------------------------------------------


def test_a_decompression_bomb_is_refused_and_nothing_is_stored(conn):
    bomb = gzip.compress(b"A" * (agent_logs.MAX_INFLATE + 10))
    got = agent_logs.receive(conn, MAC, None, "failed", bomb, known=True)
    assert got["ok"] is False and got["code"] == "bad_log"
    assert conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0] == 0


@pytest.mark.parametrize("body", [b"", b"not gzip at all"])
def test_a_body_that_is_not_a_log_is_refused(conn, body):
    assert agent_logs.receive(conn, MAC, None, "failed", body, known=True)["code"] == "bad_log"


def test_only_the_tail_is_kept(conn):
    text = "".join(f"[10:00:00] line {i:06d} " + "x" * 80 + "\n" for i in range(2000))
    got = agent_logs.receive(conn, MAC, None, "round_end", _gz(text), known=True)
    kept = agent_logs.text_of(conn, got["id"])
    assert kept["truncated"] is True and "line 001999" in kept["text"]
    assert "line 000000" not in kept["text"]
    assert len(kept["text"].encode()) <= agent_logs.MAX_TEXT
    assert kept["text"].startswith("[10:00:00] line ")       # מתחילת שורה


def test_rows_per_machine_are_capped_on_every_insert(conn, monkeypatch):
    monkeypatch.setattr(agent_logs, "KEEP_PER_MAC", 3)
    ids = [agent_logs.receive(conn, MAC, f"ses_{i}", "round_end", _gz(f"line {i}\n"),
                              known=True)["id"] for i in range(5)]
    kept = [r["id"] for r in agent_logs.list_for(conn, MAC)]
    assert kept == ids[:1:-1][:3] == [ids[4], ids[3], ids[2]]


def test_housekeeping_drops_logs_older_than_90_days(conn):
    got = agent_logs.receive(conn, MAC, None, "failed", _gz("x\n"), known=True)
    old = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(timespec="seconds")
    conn.execute("UPDATE agent_logs SET at = ? WHERE id = ?", (old, got["id"]))
    conn.commit()
    agent_logs.receive(conn, MAC, None, "failed", _gz("y\n"), known=True)
    result = housekeeping.purge_tables(conn)
    assert result["deleted"].get("agent_logs") == 1
    assert len(agent_logs.list_for(conn, MAC)) == 1


@pytest.mark.parametrize("kw,code", [
    ({"known": False}, "unknown_machine"),
    ({"reason": "whatever"}, "bad_reason"),
    ({"session_id": "ses_1; DROP"}, "bad_session"),
])
def test_refusals_store_nothing(conn, kw, code):
    args = {"session_id": "ses_1", "reason": "failed", "known": True, **kw}
    got = agent_logs.receive(conn, MAC, args["session_id"], args["reason"], _gz("x\n"),
                             known=args["known"])
    assert got == {**got, "ok": False, "code": code}
    assert conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0] == 0


def test_a_failure_inside_storing_is_an_answer_not_a_crash(conn, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(agent_logs, "_store", boom)
    got = agent_logs.receive(conn, MAC, None, "failed", _gz("x\n"), known=True)
    assert got == {"ok": False, "code": "error", "error": "not stored"}


# --- HTTP -------------------------------------------------------------------


def test_the_agent_endpoint_and_the_console_end_to_end(server):
    ctx, admin = server["ctx"], server["admin"]
    registry.add_machine(ctx.conn, MAC, "cloner-9", "grp_CLONERS", "test")
    anon = server["anon"]
    r = anon.post(f"/api/v1/agent/log?mac={MAC}&session_id=ses_77&reason=failed",
                  content=_gz(SECRET_LOG + "[10:01:00] ata3.00: error: { ICRC ABRT }\n"),
                  headers={"Content-Type": "application/gzip"})
    assert r.status_code == 200 and r.json()["signatures"] == ["crc"], r.text
    data = admin.get(f"/api/console/machines/{MAC}/data").json()
    [entry] = data["agent_logs"]
    assert entry["session_id"] == "ses_77" and entry["reason"] == "failed"
    assert entry["signatures"][0]["key"] == "crc"
    text = admin.get(f"/api/console/agent-logs/{entry['id']}").json()["text"]
    assert "hunter2" not in text and "ICRC" in text
    assert server["deploy"].get(f"/api/console/machines/{MAC}/data").status_code == 403
    line = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'agent_log'").fetchone()
    assert line["detail"].startswith(f"{MAC} failed ses_77 lines=")


def test_an_unregistered_machine_is_refused_and_a_huge_body_never_read(server):
    anon = server["anon"]
    r = anon.post("/api/v1/agent/log?mac=aa:bb:cc:dd:00:01&reason=failed", content=_gz("x\n"))
    assert r.status_code == 403 and r.json()["code"] == "unknown_machine"
    registry.add_machine(server["ctx"].conn, MAC, "cloner-9", "grp_CLONERS", "test")
    big = b"\x1f\x8b" + b"\0" * (agent_logs.MAX_BODY + 1)
    r = anon.post(f"/api/v1/agent/log?mac={MAC}&reason=failed", content=big)
    assert r.status_code == 413
    assert server["ctx"].conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0] == 0


def test_a_chunked_body_without_length_stops_at_the_ceiling(server):
    """סקירה: בלי Content-Length (chunked) הגוף נקרא כזרם — 413 ברגע שעבר את התקרה,
    ולא נמשך עד הסוף לזיכרון. ה-TestClient אוגר את הגוף לפני השליחה, ולכן ה-endpoint
    נקרא כאן ישירות עם `receive` של ASGI שסופר כמה חלקים נמשכו."""
    import asyncio
    from starlette.requests import Request

    registry.add_machine(server["ctx"].conn, MAC, "cloner-9", "grp_CLONERS", "test")
    pulled = []

    async def receive():
        pulled.append(1)
        return {"type": "http.request", "body": b"Z" * 65536, "more_body": len(pulled) < 64}

    from server.agent.api import create_agent_router
    [endpoint] = [r.endpoint for r in create_agent_router(server["ctx"]).routes
                  if getattr(r, "path", None) == "/api/v1/agent/log"]
    scope = {"type": "http", "method": "POST", "path": "/api/v1/agent/log", "headers": [],
             "query_string": f"mac={MAC}&reason=failed".encode(), "client": ("10.0.0.5", 1)}
    r = asyncio.run(endpoint(Request(scope, receive)))
    assert r.status_code == 413 and b"too_large" in r.body
    assert len(pulled) == agent_logs.MAX_BODY // 65536 + 1 < 64     # 3 חלקים, לא 64
    assert server["ctx"].conn.execute("SELECT COUNT(*) FROM agent_logs").fetchone()[0] == 0


# --- הסוכן ------------------------------------------------------------------

from native import requires_native                       # noqa: E402
from test_agent import AGENT, BASH, posix, sh            # noqa: E402

LIB = flat_lib()
def _send(tmp_path: Path, curl_rc: int, failed: bool) -> tuple[str, Path]:
    run = tmp_path / "run"
    for dev, state in (("sda", "failed" if failed else "done"), ("sdb", "done")):
        (run / "targets" / dev).mkdir(parents=True)
        (run / "targets" / dev / "state").write_text(state + "\n", encoding="utf-8", newline="\n")
    log = run / "agent.log"
    log.write_text("".join(f"[10:00:00] line {i}\n" for i in range(600)), encoding="utf-8",
                   newline="\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "curl"
    fake.write_text("#!/bin/sh\n"
                    f'for a in "$@"; do echo "$a"; done > {posix(tmp_path)}/curl.args\n'
                    f'for a in "$@"; do case "$a" in @*) cp "${{a#@}}" {posix(tmp_path)}/body.gz ;; esac; done\n'
                    f"exit {curl_rc}\n", encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    out = sh(f'export PATH="$(cd {posix(bin_dir)!r} && pwd):$PATH" RUN_DIR={posix(run)!r} LOG_FILE={posix(log)!r}'
             f' SERVER=http://10.0.0.1:8080 MAC={MAC}; . {posix(LIB)}/common.sh;'
             f' . {posix(LIB)}/datareport.sh; agent_log_send ses_9; echo "rc=$?"')
    return out, log


@requires_native(("bash", BASH))
def test_the_agent_sends_the_gzipped_tail_once_with_the_reason(tmp_path):
    out, log = _send(tmp_path, 0, failed=True)
    assert "rc=0" in out
    args = (tmp_path / "curl.args").read_text(encoding="utf-8").splitlines()
    assert f"http://10.0.0.1:8080/api/v1/agent/log?mac={MAC}&session_id=ses_9&reason=failed" in args
    body = gzip.decompress((tmp_path / "body.gz").read_bytes()).decode()
    assert body.splitlines()[-1] == "[10:00:00] line 599"
    assert len(body.splitlines()) == 400 and "line 199\n" not in body
    assert "agent log: sent to the server (failed)" in log.read_text(encoding="utf-8")


@requires_native(("bash", BASH))
def test_a_round_without_a_failed_drawer_is_round_end(tmp_path):
    _send(tmp_path, 0, failed=False)
    assert any(a.endswith("reason=round_end")
               for a in (tmp_path / "curl.args").read_text(encoding="utf-8").splitlines())


@requires_native(("bash", BASH))
def test_a_send_that_fails_returns_zero_and_says_so_locally(tmp_path):
    out, log = _send(tmp_path, 7, failed=True)
    assert "rc=0" in out
    assert "agent log: NOT delivered to the server (failed)" in log.read_text(encoding="utf-8")
    assert not (tmp_path / "run" / "agentlog.gz").exists()


def test_the_agent_sends_at_the_end_of_both_round_paths():
    """הצלחה (‏imagesrv-agent, אחרי report_final) וכשל (‏end_failed_round) — פעם אחת כל אחד."""
    main = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    idle = (LIB / "idle.sh").read_text(encoding="utf-8")
    assert main.count('agent_log_send "$2"') == 1 and idle.count('agent_log_send "$2"') == 1
    ok = main[main.index("report_final \"$_ppid\" \"$2\""):]
    assert ok.index('agent_log_send "$2"') < ok.index("end_countdown")
    fail = idle[idle.index("end_failed_round() {"):]
    assert fail.index("report_final") < fail.index('agent_log_send "$2"') < fail.index("end_countdown")


def test_the_interface_documents_the_endpoint():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "POST /api/v1/agent/log" in doc and "agent_logs" in doc
    assert json.dumps(sorted(agent_logs.REASONS)) == '["failed", "round_end"]'
