"""#2031 — a cloner holding its results until "כבה" (#2024) is "דלוק — מחכה ללחיצה".

**Nadav's decision (22/10, v1).** After #2024 a cloner with a problem disk stays
on until "כבה", and sends no hello -- on purpose: a "seen" cloner would join no
wave, yet stop #1721's re-wake from failing the round by name. Without a word
the console drew it "not connected" after ONLINE_SECONDS. Wanted:

* the console shows "דלוק — מחכה ללחיצה: יש דיסק עם בעיה", with the problem disks;
* the cloner is **not** free for rounds: it does not join a wave, and a wave that
  waits for it fails in its name, saying why;
* "כבה" from the console reaches it while it holds;
* all green: still 20 s and off (no beat at all).

How: a light report on the progress path with no session and no task --
``{"mac","state":"held","problems":[...]}`` every ``HELD_BEAT_S`` (hold.sh) --
kept as ``progress_seen`` + ``held_at``/``held_json``, never ``last_seen``; its
answer carries the console's ``power_action``.

**Negative control** (in the PR): with the production files of the base
(df59da5a) the console / wave / power tests fail -- no ``held`` anywhere, the
report is refused, the round fails as "check power and WoL", there is no
``/machines/{mac}/poweroff`` -- and the agent tests find no beat.
"""

import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_end_hold_2024 import CLEAN, end
from test_end_of_task_1560 import bash, q, trace_of

pytest.importorskip("fastapi")

from server import monitor  # noqa: E402
from server import room as room_mod  # noqa: E402
from test_room_rewake_1721 import CAP, STEP, _packets_to, _round, _wave1_partial  # noqa: E402
from test_server_room import (  # noqa: E402,F401 — room_server is a fixture
    CLONER1, CLONER2, cloner_hello, room, room_server,
)

needs_jq = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

HELD_HE = "דלוק — מחכה ללחיצה: יש דיסק עם בעיה"
PROBLEMS = [{"disk": "sdb", "level": "r", "reason": "הכתיבה נכשלה"},
            {"disk": "sda", "level": "o", "reason": "מה שנכתב לא נבדק מול הדיסק"}]


def held_report(client, mac: str = CLONER1, problems=None) -> dict:
    response = client.post("/api/v1/agent/progress", json={
        "mac": mac, "state": "held", "problems": PROBLEMS if problems is None else problems})
    assert response.status_code == 200, response.text
    return response.json()


def machine(client, mac: str = CLONER1) -> dict:
    rows = client.get("/api/console/machines").json()
    return next(r for r in rows if r["mac"] == mac)


def room_machine(client, mac: str = CLONER1) -> dict:
    return next(m for m in room(client)["machines"] if m["mac"] == mac)


def quiet_since(ctx, minutes: int = 20) -> str:
    """The hold sends no hello: the last one is from before the round ended."""
    long_ago = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE net_devices SET last_seen = ?", (long_ago,))
    ctx.conn.commit()
    return long_ago


# --- the console ------------------------------------------------------------------------


def test_a_held_cloner_is_shown_on_and_waiting_with_its_problem_disks(room_server):
    ctx, anon, deploy = room_server["ctx"], room_server["anon"], room_server["admin"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    long_ago = quiet_since(ctx)

    reply = held_report(anon)

    assert reply == {"ok": True}, reply                               # nothing to do yet
    held = machine(deploy)["held"]
    assert held is not None and held["problems"] == PROBLEMS, held    # the machines page
    rm = room_machine(deploy)
    assert rm["held"] and rm["held"]["problems"] == PROBLEMS, rm      # the room
    assert rm["awake"] is False, "the hold is not 'seen' (#1721): no hello, no awake"
    seen = ctx.conn.execute("SELECT last_seen FROM net_devices WHERE mac = ?", (CLONER1,)).fetchone()
    assert seen["last_seen"] == long_ago, "a held report wrote last_seen -- #1721 would stop failing the round"
    online = [r["online"] for r in monitor.machine_rows(ctx.conn) if r["mac"] == CLONER1]
    assert online == [True], "on, not 'לא מחובר' (progress_seen, #1839)"
    assert machine(deploy, CLONER2)["held"] is None


def test_the_console_draws_the_sentence_and_the_button():
    """The words and the button are in the console, on every view of the cloner."""
    js = (Path(__file__).resolve().parents[1] / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert f'const HELD_HE = "{HELD_HE}";' in js
    for where in ("if (m.held) return { cls: \"warn\", text: HELD_HE, held: true };",   # machineState
                  "if (rm.held) return { cls: \"warn\", text: \"דלוק — מחכה ללחיצה\", held: true };",   # roomCardState
                  'post("/machines/" + encodeId(mac) + "/poweroff"'):
        assert where in js, where


def test_a_held_report_goes_stale_and_a_joining_hello_clears_it(room_server, monkeypatch):
    ctx, anon, deploy = room_server["ctx"], room_server["anon"], room_server["admin"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    held_report(anon)
    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)          # an attended beat: still held
    assert machine(deploy)["held"] is not None
    cloner_hello(anon, CLONER1, ["S1", "S2"])                         # back in the queue
    assert machine(deploy)["held"] is None

    held_report(anon)
    from server import held as held_mod
    old = (datetime.now(timezone.utc) - timedelta(seconds=held_mod.FRESH_SECONDS + 5)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE net_devices SET held_at = ? WHERE mac = ?", (old, CLONER1))
    ctx.conn.commit()
    assert machine(deploy)["held"] is None, "a held report a minute old is no evidence it is on"


def test_a_held_report_from_an_unknown_machine_is_refused(room_server):
    stranger = "aa:bb:cc:00:00:99"
    response = room_server["anon"].post("/api/v1/agent/progress", json={
        "mac": stranger, "state": "held", "problems": PROBLEMS})
    assert response.status_code == 403, response.text
    assert room_server["ctx"].conn.execute(
        "SELECT 1 FROM net_devices WHERE mac = ?", (stranger,)).fetchone() is None


# --- not free for rounds ----------------------------------------------------------------


def test_a_held_cloner_does_not_join_the_wave_and_the_wave_fails_in_its_name(room_server, monkeypatch):
    """The cloner held after wave 1 (a drawer failed). Wave 2 waits for it: it is
    'missing' as held -- with "כבה אותו" -- never a member, and after the
    re-wake cap the round fails naming it and saying it waits for "כבה"."""
    ctx, anon, deploy, woken = room_server["ctx"], room_server["anon"], room_server["deploy"], room_server["woken"]
    clock, wave2 = _wave1_partial(room_server, monkeypatch)
    before = _packets_to(woken, CLONER1)

    held_report(anon)
    missing = room(deploy)["round"]["missing"]
    assert [(x["mac"], x["reason"], x["can_poweroff"]) for x in missing] == [(CLONER1, "held", True)], missing
    assert "מחכה ללחיצה" in missing[0]["text_he"], missing

    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        held_report(anon)
        assert CLONER1 not in {m["mac"] for m in ctx.store.members(wave2)}, "a held report joined the wave"
        room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) - before == CAP                # #1721 as it was
    row = _round(ctx)
    assert row["state"] == "failed", row
    assert "shich-1" in row["failed_reason"] and "מחכה ללחיצה על 'כבה'" in row["failed_reason"], row["failed_reason"]
    assert "Wake-on-LAN" not in row["failed_reason"], "a lit machine is not sent after its power cable"


def test_a_cloner_that_is_not_held_still_fails_as_not_woken(room_server, monkeypatch):
    """The reason is the old one when there is no held report (the change is narrow)."""
    ctx = room_server["ctx"]
    clock, _wave2 = _wave1_partial(room_server, monkeypatch)
    for _ in range(CAP + 10):
        clock.now += timedelta(seconds=STEP)
        room_mod.sweep(ctx.conn, ctx.store)
    reason = _round(ctx)["failed_reason"]
    assert "Wake-on-LAN" in reason and "מחכה ללחיצה" not in reason, reason


# --- "כבה" from the console -------------------------------------------------------------


def test_power_off_from_the_console_reaches_a_held_cloner(room_server):
    ctx, anon, deploy, admin = (room_server[k] for k in ("ctx", "anon", "deploy", "admin"))
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3"])
    quiet_since(ctx)
    held_report(anon)

    url = f"/api/console/machines/{CLONER1}/poweroff"
    assert anon.post(url, json={"confirm_name": "shich-1"}).status_code == 401
    assert deploy.post(url, json={"confirm_name": "shich-1"}).status_code == 403   # deploy: no console (#1073)
    assert admin.post(url, json={"confirm_name": "shich-2"}).status_code == 400    # not its name
    assert admin.post(f"/api/console/machines/{CLONER2}/poweroff",
                      json={"confirm_name": "shich-2"}).status_code == 409         # not held
    answer = admin.post(url, json={"confirm_name": "shich-1"})
    assert answer.status_code == 200, answer.text

    reply = held_report(anon)                                         # the next beat
    assert reply.get("power_action") == "poweroff", reply
    assert machine(admin)["held"] is None, "delivered: it leaves the hold"
    assert held_report(anon).get("power_action") is None, "handed out once"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal ORDER BY rowid")]
    assert "held_poweroff" in events and "power_delivered" in events, events


# --- the agent --------------------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.received.append((self.path, json.loads(body)))
        out = json.dumps(self.server.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_):
        pass


@pytest.fixture
def fake():
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.received, httpd.reply = [], {"ok": True}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def _libs() -> str:
    return "".join(f". {posix(AGENT)}/lib/{n}; " for n in
                   ("common.sh", "jsonq.sh", "attended.sh", "idle.sh", "hold.sh"))


def _run(tmp_path: Path, fake, body: str, targets=None, timeout: int = 60):
    run = tmp_path / "run"
    for disk, files in (targets or {}).items():
        d = run / "targets" / disk
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text + "\n", encoding="utf-8", newline="\n")
    run.mkdir(parents=True, exist_ok=True)
    host, port = fake.server_address[:2]
    script = (f"export RUN_DIR={q(run)} LOG_FILE={q(run / 'agent.log')} MAC=aa:bb:cc:dd:ee:ff "
              f"SERVER=http://{host}:{port} HTTP_TIMEOUT=5 HTTP_RETRIES=0; " + _libs() + body
              + '; echo "rc=$?"')
    return bash(script, timeout=timeout), run


@needs_jq
def test_the_held_report_carries_the_problems_and_keeps_the_console_power_off(tmp_path, fake):
    fake.reply = {"ok": True, "power_action": "poweroff"}
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "end.problems").write_text(
        "sdb|r|הכתיבה נכשלה\nsda|o|מה שנכתב לא נבדק מול הדיסק\n", encoding="utf-8", newline="\n")

    out, run = _run(tmp_path, fake, "held_report")

    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert fake.received == [("/api/v1/agent/progress",
                              {"mac": "aa:bb:cc:dd:ee:ff", "state": "held", "problems": PROBLEMS})]
    assert json.loads((run / "power.resp").read_text())["power_action"] == "poweroff", \
        "the console's power-off is kept for the hold loop (attended_power_check)"


@needs_jq
def test_the_hold_beats_while_it_holds_and_stops_with_it(tmp_path, fake):
    """Real time, a 1 s beat: reports while held, none after "כבה"."""
    body = ('finish_and_stop() { echo FINISH >> "$RUN_DIR/trace"; return 0; }; '
            'safe_poweroff() { echo SAFE >> "$RUN_DIR/trace"; return 1; }; '
            '( sleep 3.5; : > "$RUN_DIR/end.now" ) < /dev/null > /dev/null 2>&1 & '
            'HELD_BEAT_S=1 end_countdown failed "round failed" 0 1 sdb')
    out, run = _run(tmp_path, fake, body, {"sdb": {"state": "failed", "error": "הכתיבה נכשלה"}})

    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace_of(run) == ["FINISH"], trace_of(run)
    n = len(fake.received)
    assert n >= 3, f"{n} held reports in 3.5 s of a 1 s beat"
    assert all(b["state"] == "held" and b["problems"] == [
        {"disk": "sdb", "level": "r", "reason": "הכתיבה נכשלה"}] for _p, b in fake.received), fake.received
    time.sleep(0.5)                       # a report already in flight when the beat was stopped
    n = len(fake.received)
    time.sleep(2.5)
    assert len(fake.received) == n, "the beat outlived the hold"


@needs_jq
def test_the_console_power_off_in_the_beat_answer_powers_the_held_machine_off(tmp_path, fake):
    """The loop takes what the beat kept: safe_poweroff with the console's reason, at once."""
    fake.reply = {"ok": True, "power_action": "poweroff"}
    from test_end_hold_2024 import HARNESS
    body = (HARNESS + 'held_beat_start() { [ -n "${HELD_PID:-}" ] || { HELD_PID=1; held_report; }; }; '
            'end_countdown failed "round failed" 0 1 sdb')
    out, run = _run(tmp_path, fake, body, {"sdb": {"state": "failed"}})

    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    trace = trace_of(run)
    assert trace[0] == "SAFE-POWEROFF 0 power: the console asked to power off", trace
    assert len(fake.received) == 1


@needs_jq
def test_a_clean_end_sends_nothing_and_a_held_end_starts_the_beat_once(tmp_path):
    stub = ('held_beat_start() { [ -n "${HELD_PID:-}" ] || { echo "HELD-BEAT $tick" >> "$RUN_DIR/trace"; HELD_PID=1; }; }; '
            'held_beat_stop() { [ -z "${HELD_PID:-}" ] || echo "HELD-STOP" >> "$RUN_DIR/trace"; HELD_PID=; }; ')
    out, run = end(tmp_path / "clean", {"sdb": CLEAN},
                   stub + 'end_countdown drawers "drawers complete (1 of 1)" 1 1 sdb')
    assert trace_of(run) == ["FINISH 20"], trace_of(run)               # all green: 20 s, no beat

    out, run = end(tmp_path / "held", {"sdb": {"state": "failed"}},
                   stub + 'end_countdown drawers "drawers complete (0 of 1)" 0 1 sdb')
    assert out.stdout.strip().endswith("rc=0"), out.stdout + out.stderr
    assert trace_of(run) == ["HELD-BEAT 0", "HELD-STOP", "FINISH 601"], trace_of(run)
