"""#3173: הסימולציה (`tools/e2e_simulation.py`) רצה על מנוע החתיכות — ברירת המחדל, והמנוע היחיד.

עד #3173 השרת של הסימולציה עלה עם `--sender-simulate`: שולח udpcast מדומה, שכפה udpcast על
כל גל, והסימולציה בחרה udpcast במפורש. מנוע ברירת המחדל לא נבדק בה כלל. עכשיו:

- השרת עולה **בלי** `--sender-simulate` ובלי הגדרת מנוע, ו-`simsend` מחליף רק את `chunkcast send`
  (המנוע עצמו אמיתי). `simsend` רושם כל פקודה ואינו משדר; בלי קובץ לרשום אליו הוא נכשל בשמו.
- הסוכן המדומה מצהיר `engines: ["chunk"]`, עובר כל מחיצה ב-`chunk-pass`, ונכשל בגלוי על גל
  שאינו של מנוע החתיכות או על תשובה שאינה `done`.

הסימולציה המלאה צריכה gcc ושרת; כאן החוליות שלה, בלי רשת ובלי שרת.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import server.app
from server.rounds import chunklog
from tools.e2e import harness, simsend
from tools.e2e.machines import SimMachine

SEND = ["chunkcast", "send", "--to", "239.84.82.1:30201", "--session", "k", "--part", "1", "--pass", "1",
        "--index", "x.idx", "--file", "", "--kbps", "1000", "--write-kbps", "800"]


def _send(file: Path) -> list[str]:
    cmd = list(SEND)
    cmd[cmd.index("--file") + 1] = str(file)
    return cmd


# --- simsend --------------------------------------------------------------------------------------

def test_simsend_records_the_command_and_exits_zero_without_sending(tmp_path, monkeypatch):
    record = tmp_path / "sends.jsonl"
    monkeypatch.setenv(simsend.ENV, str(record))
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    cmd = _send(tmp_path / "img_a" / "p1.esp.pcl.zst")
    process = simsend.run(cmd)
    assert process.wait(timeout=30) == 0
    assert [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()] == [cmd]
    assert chunklog.log_path(cmd[cmd.index("--file") + 1]).read_bytes() == simsend.LOG_LINE


def test_simsend_without_its_record_fails_by_name_and_never_pretends_to_send(tmp_path, monkeypatch):
    """עיקרון 5: שולח שאין לו איפה לרשום מה שלח אינו "שלח" — `OSError`, שהמנוע הופך לכישלון של הגל."""
    monkeypatch.delenv(simsend.ENV, raising=False)
    with pytest.raises(OSError, match=simsend.ENV):
        simsend.run(_send(tmp_path / "p1"))
    monkeypatch.setenv(simsend.ENV, str(tmp_path / "sends.jsonl"))
    with pytest.raises(OSError, match="not a chunkcast send"):
        simsend.run(["udp-sender", "--file", str(tmp_path / "p1")])
    assert not (tmp_path / "sends.jsonl").exists()


def test_a_server_built_after_install_sends_through_simsend(tmp_path, monkeypatch):
    """`install` לפני `create_runtime` (כמו ה-bootstrap של `start_server`): הפקודה של מנוע החתיכות
    על קובץ של הספרייה מגיעה ל-`simsend`, ולא ל-`chunkcast` אמיתי."""
    monkeypatch.setattr(chunklog, "run_process", chunklog.run_process)     # restored after the test
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    monkeypatch.setenv(simsend.ENV, str(tmp_path / "sends.jsonl"))
    simsend.install()
    ctx = server.app.create_app(tmp_path / "data", tmp_path / "images", "http://10.44.12.10:7712").state.ctx
    cmd = _send(tmp_path / "images" / "img_a" / "p1.esp.pcl.zst")
    assert ctx.chunk.runner(cmd).wait(timeout=30) == 0
    assert harness.sent_commands(tmp_path / "data") == [cmd]


# --- the server's command line ---------------------------------------------------------------------

def _start(tmp_path, monkeypatch) -> tuple[list[str], dict]:
    captured: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: captured.append((list(cmd), kw)) or object())
    harness.start_server(tmp_path)
    assert len(captured) == 1, captured
    return captured[0][0], captured[0][1]


def test_the_server_runs_on_the_default_engine_with_no_simulated_udpcast(tmp_path, monkeypatch):
    argv, _kw = _start(tmp_path, monkeypatch)
    assert "--sender-simulate" not in argv
    assert not [a for a in argv if "udpcast" in a or "stream_engine" in a]
    bootstrap = argv[argv.index("-c") + 1]
    assert "simsend" in bootstrap and bootstrap.index("s.install()") < bootstrap.index("main()")


def test_the_server_finds_the_built_chunkcast_first_and_knows_where_simsend_records(tmp_path, monkeypatch):
    _argv, kw = _start(tmp_path, monkeypatch)
    env = kw["env"]
    assert env["PATH"].split(__import__("os").pathsep)[0] == str(tmp_path / "bin")
    assert env[harness.SIMSEND_ENV] == str(harness.sends_path(tmp_path))
    assert harness.SIMSEND_ENV == simsend.ENV


# --- the simulated agent ---------------------------------------------------------------------------

def test_the_simulated_agent_declares_the_chunk_engine_in_its_hello(monkeypatch):
    seen = []

    class Fake:
        def json(self, method, path, body=None):
            seen.append(body)
            return 200, {}

    harness.hello(Fake(), "aa:bb:cc:00:00:20", [])
    assert seen[0]["engines"] == ["chunk"]


class _Server:
    """השרת, כפי שהמכונה המדומה רואה אותו: מניפסט, `chunk-pass` ו-`progress`. `wait` לפני כל `done`."""

    def __init__(self, ended_answer: str = "done"):
        self.calls: list[tuple[str, dict | None]] = []
        self.ended_answer = ended_answer
        self.asked: dict[int, int] = {}

    def json(self, method, path, body=None):
        self.calls.append((path, body))
        if path.endswith("/manifest"):
            return 200, {"total_compressed_bytes": 100, "partitions": [
                {"index": 1, "file": "p1.esp.pcl.zst", "readback_chunks": 1},
                {"index": 2, "file": None},
                {"index": 3, "file": "p3.windows.pcl.zst", "readback_chunks": 4}]}
        if path.endswith("/chunk-pass"):
            if body["state"] == "listening":
                return 200, {"ok": True, "action": "receive", "pass": 1}
            self.asked[body["part"]] = self.asked.get(body["part"], 0) + 1
            if self.asked[body["part"]] == 1:
                return 200, {"ok": True, "action": "wait", "poll": 2}
            return 200, {"ok": True, "action": self.ended_answer}
        return 200, {"ok": True}


def _machine(server: _Server) -> SimMachine:
    m = SimMachine("aa:bb:cc:00:00:20", [harness.disk("sda", harness.GB256, "DRW-A", port=1),
                                         harness.disk("sdb", harness.GB256, "DRW-B", port=2)])
    m.client = server
    m.pass_sent = lambda session, part: True
    return m


CHUNK_WAVE = {"id": "ses_1", "image_id": "img_a", "state": "running", "engine": "chunk",
              "chunk": {"group": "239.84.82.1", "port": 30201, "key": "k"}}


def test_the_simulated_agent_passes_every_partition_through_chunk_pass_before_its_progress():
    server = _Server()
    m = _machine(server)
    m.write_plans = [[("sda", "done"), ("sdb", "failed", "I/O error")]]
    m._write(dict(CHUNK_WAVE))
    passes = [(b["part"], b["state"]) for p, b in server.calls if p.endswith("/chunk-pass")]
    assert passes == [(1, "listening"), (1, "ended"), (1, "ended"), (3, "listening"), (3, "ended"), (3, "ended")]
    ended = [b for p, b in server.calls if p.endswith("/chunk-pass") and b["state"] == "ended" and b["part"] == 3]
    assert {t["serial"]: (t["failed"], t["durable"]) for t in ended[-1]["targets"]} == {
        "DRW-A": (False, [{"part": 3, "ranges": [[0, 3]]}]),       # every chunk of the partition
        "DRW-B": (True, [{"part": 3, "ranges": []}])}               # the drawer the plan fails: nothing durable
    progress = [i for i, (p, _b) in enumerate(server.calls) if p.endswith("/progress")]
    last_pass = max(i for i, (p, _b) in enumerate(server.calls) if p.endswith("/chunk-pass"))
    assert progress and min(progress) > last_pass
    assert m.chunk_parts == [("ses_1", 1), ("ses_1", 3)]


def test_a_wave_that_is_not_the_chunk_engines_fails_the_simulated_agent_by_name():
    server = _Server()
    m = _machine(server)
    m.write_plans = [[("sda", "done")]]
    with pytest.raises(RuntimeError, match="אינו במנוע החתיכות"):
        m._write({"id": "ses_2", "image_id": "img_a", "state": "running"})
    assert not [p for p, _b in server.calls if p.endswith("/progress")]      # no "done" without the engine


def test_an_answer_that_is_not_done_fails_the_simulated_agent_by_name():
    """`fetch` או מעבר השלמה אינם "כמעט done": הסוכן המדומה דיווח הכול, ותשובה אחרת היא כשל גלוי."""
    server = _Server(ended_answer="fetch")
    m = _machine(server)
    m.write_plans = [[("sda", "done")]]
    with pytest.raises(RuntimeError, match="fetch"):
        m._write(dict(CHUNK_WAVE))
    assert not [p for p, _b in server.calls if p.endswith("/progress")]
