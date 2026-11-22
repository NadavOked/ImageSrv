"""#3261 -- a failed `chunkcast send` shows the sender's own sentence, not only its exit code.

`chunkcast send` says why it fell (`ir_say`: "cannot send to 239.84.82.1:7713: Operation not permitted") into the (פורט-הפצה-מכוון)
per-partition log the engine gives it; the engine read the exit code and never the log, so the cloner's screen
(`failed_reason`) said "chunkcast send נכשל על p3... (קוד 2, מעבר 1)" and the reason stayed in a file on the server.
The old sender had `sender_log_tail` (#2187); this is its twin in `chunklog.py`, with no dependency on `sender.py`
(which #3173 removes).

Negative control: server/rounds/chunksend.py back to the base (the engine does not read the log) -- and, for the
unit tests, server/rounds/chunklog.py removed.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.rounds import chunklog, chunksend
from test_chunksend_2533 import Proc, rig, wait_for   # noqa: F401 -- `rig` is a fixture

PART_FILE = "p3.windows.pcl.zst"
SAID = "chunkcast: send: cannot send to 239.84.82.1:7713: Operation not permitted"   # פורט-הפצה-מכוון


class TalkingRunner:
    """A `chunkcast send` that writes into the log the engine gave it (as the real one does) and falls with `code`."""

    def __init__(self, text: str | None, code: int = 2):
        self.text, self.code, self.commands = text, code, []

    def __call__(self, cmd):
        self.commands.append(cmd)
        if self.text is not None:
            chunklog.log_path(cmd[cmd.index("--file") + 1]).write_text(self.text, encoding="utf-8")
        return Proc(self.code)


@pytest.fixture(autouse=True)
def _logs_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)


def _fail_with(rig, runner) -> str:
    rig.runner = runner
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    (why,) = rig.named("send_failed")
    assert engine.status()["error"] == why
    return why


def test_the_senders_own_reason_reaches_the_failure_the_operator_reads(rig):
    why = _fail_with(rig, TalkingRunner(f"sent 3 12000\n{SAID}\n"))
    assert f"chunkcast send נכשל על {PART_FILE} (קוד 2, מעבר 1)" in why, why     # what it said before stays
    assert why.endswith(f": {SAID}"), why


def test_progress_lines_are_not_the_reason(rig):
    """The log of a sender that fell mid-wave ends with its progress lines; the sentence before them is the reason."""
    why = _fail_with(rig, TalkingRunner(f"{SAID}\nsent 4 16000\nsent 5 20000\n"))
    assert why.endswith(SAID) and "sent 5" not in why, why


def test_a_log_nobody_wrote_adds_no_reason_and_invents_none(rig):
    why = _fail_with(rig, TalkingRunner(None))
    assert why == f"chunkcast send נכשל על {PART_FILE} (קוד 2, מעבר 1)", why


def test_a_log_with_only_progress_adds_no_reason(rig):
    why = _fail_with(rig, TalkingRunner("sent 1 4000\nsent 2 8000\n"))
    assert ":" not in why.split("(קוד 2, מעבר 1)")[1], why


def test_the_tail_is_the_last_sentence_cut_to_the_limit(tmp_path):
    path = tmp_path / "x.log"
    path.write_text("first\n" + "z" * 1000 + "\nsent 9 9\n", encoding="utf-8")
    assert chunklog.log_tail(path) == "z" * chunklog.TAIL_LIMIT
    assert chunklog.log_tail(tmp_path / "missing.log") == ""        # not read = not said


def test_each_partition_has_its_own_log(tmp_path):
    a, b = chunklog.log_path("/lib/img_1/p3.windows.pcl.zst"), chunklog.log_path("/lib/img_1/p4.data.pcl.zst")
    assert a != b and a.parent == tmp_path and a == chunklog.log_path("img_1/p3.windows.pcl.zst")


def test_the_engines_default_runner_is_not_the_one_in_the_module_that_is_deleted():
    """#3173 removes sender.py: the runner is chunklog's, and chunklog imports nothing from it."""
    default = inspect.signature(chunksend.ChunkEngine.__init__).parameters["runner"].default
    assert default is chunklog.run_process
    imported = {n.module for n in ast.walk(ast.parse(Path(chunklog.__file__).read_text(encoding="utf-8")))
                if isinstance(n, ast.ImportFrom)}
    assert "server.rounds.sender" not in imported and "server.rounds" not in imported, imported


@requires_native(posix=True, why="nice/ionice and a real child process")
def test_the_real_runner_writes_the_childs_output_to_the_partition_log(tmp_path):
    cmd = [sys.executable, "-c", "import sys; print('sent 1 2'); print('cannot send: boom', file=sys.stderr); sys.exit(2)",
           "--file", str(tmp_path / "img" / PART_FILE)]
    # the child ignores the extra argument: it is only the path the log is derived from (the engine's own command has it too)
    process = chunklog.run_process(cmd)
    assert process.wait(30) == 2
    assert chunklog.log_tail(chunklog.log_path(tmp_path / "img" / PART_FILE)) == "cannot send: boom"
