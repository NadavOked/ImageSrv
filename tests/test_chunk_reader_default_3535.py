"""#3535: a `chunkcast send` the server starts from a library file reads its source on a thread of its own, unless the operator said otherwise.

On the metal (LabRoot, cloner 2, `img_3fa17f`, partition 3 of 17.8GB; `lab/metal-1116-r16/results.md` and `lab/metal-1116-r18/results.md`):
`CHUNKCAST_READER=thread` p3 317, 318, 318, 321 s; the inline reader 326, 328, 328, 330 s. n=4 on each side, -9.5 s (2.9%), the ranges
do not overlap. The default stays `inline` inside `chunkcast` itself (the build machine sends with `--stdin`, a repair with `--device`,
and `thread` exits 2 with both): the server sets the variable, and only on a send of a `--file`.

The variable reaches the child through its environment, so what is observed here is the environment a real child gets -- on the
engine's own production runner -- and the decision itself (`chunklog.sender_env`) for the commands the engine never builds.

**Negative control:** ``server/rounds/chunklog.py`` back to the base (no `sender_env`, and `run_process` starts the child with the
server's own environment). The tests of the decision then fail with `AttributeError` (that alone is not a control), so the control that
counts is the one on the real child: `test_the_child_of_the_engines_runner_gets_the_thread_reader_with_nothing_set` reads
`reader=None` instead of `reader=thread`, a behaviour. The two that the operator overrides pass in both states (they are the
guards against a default that overrides everything).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.rounds import chunklog, sender

from test_chunk_send_reason_3261 import PART_FILE

READER, AHEAD = "CHUNKCAST_READER", "CHUNKCAST_AHEAD"

#: the engine's own command for a library file (`ChunkEngine._command`): a `--file`, never a device or a stdin.
FILE_CMD = ["chunkcast", "send", "--to", "239.83.67.1:21002", "--session", "0123456789abcdef", "--part", "3", "--pass", "1",
            "--index", "p3.idx", "--file", "/lib/img_1/p3.pcl.zst", "--kbps", "900000", "--write-kbps", "1200000", "--order", "mixed"]
STDIN_CMD = ["chunkcast", "send", "--to", "239.83.67.1:21002", "--session", "0123456789abcdef", "--part", "3", "--pass", "1",
             "--index", "p3.idx", "--stdin", "--kbps", "900000"]
DEVICE_CMD = ["chunkcast", "send", "--to", "239.83.67.1:21002", "--session", "0123456789abcdef", "--part", "3", "--pass", "1",
              "--index", "p3.idx", "--device", "/dev/sdb3", "--chunks-file", "/tmp/c.txt"]


@pytest.fixture(autouse=True)
def _operator_set_nothing(monkeypatch):
    monkeypatch.delenv(READER, raising=False)
    monkeypatch.delenv(AHEAD, raising=False)


# --- the decision, on every shape of command and environment ------------------------------------------------------------

def test_a_send_of_a_file_asks_for_the_reader_thread():
    env = chunklog.sender_env(FILE_CMD, {"PATH": "/usr/bin", "LANG": "C"})
    assert env is not None and env[READER] == "thread"
    assert env["PATH"] == "/usr/bin" and env["LANG"] == "C"      # the rest of the server's environment goes on as it is


def test_the_decision_does_not_touch_the_environment_it_was_given():
    given = {"PATH": "/usr/bin"}
    chunklog.sender_env(FILE_CMD, given)
    assert given == {"PATH": "/usr/bin"}


@pytest.mark.parametrize("cmd", [STDIN_CMD, DEVICE_CMD], ids=["stdin", "device"])
def test_a_send_of_a_stream_or_a_device_is_left_alone(cmd):
    """`thread` reads a --file: with `--stdin` or `--device` the sender refuses it (exit 2). The build machine and a repair never get it."""
    assert chunklog.sender_env(cmd, {"PATH": "/usr/bin"}) is None


def test_an_ahead_depth_set_by_the_operator_is_left_alone():
    """`thread` has no read-ahead advice: with CHUNKCAST_AHEAD the sender refuses it. The operator's experiment is not turned into a failed wave."""
    assert chunklog.sender_env(FILE_CMD, {"PATH": "/usr/bin", AHEAD: "4"}) is None


@pytest.mark.parametrize("value", ["inline", "thread", "", "bogus"])
def test_a_reader_set_by_the_operator_is_what_the_sender_gets(value):
    """The operator's value wins whatever it is, an empty or a wrong one too: the sender refuses it by name (exit 2), the wave fails visibly."""
    assert chunklog.sender_env(FILE_CMD, {READER: value}) is None


def test_a_combination_the_operator_made_is_not_repaired():
    """READER=thread and AHEAD together are refused by the sender (exit 2, 'nothing is started'). That failure is the operator's to see:
    nothing here removes one of the two, and nothing falls back to inline."""
    assert chunklog.sender_env(FILE_CMD, {READER: "thread", AHEAD: "4"}) is None


def test_the_default_decision_reads_the_servers_own_environment(monkeypatch):
    assert chunklog.sender_env(FILE_CMD)[READER] == "thread"
    monkeypatch.setenv(READER, "inline")
    assert chunklog.sender_env(FILE_CMD) is None


# --- the child that really starts, on the engine's own production runner ----------------------------------------------------

#: stands in for `chunkcast send`: says what its environment holds for the two variables (a line that is not progress, so `log_tail` reads it)
CHILD = ("import os; print('reader=%s ahead=%s' % (os.environ.get('CHUNKCAST_READER'), os.environ.get('CHUNKCAST_AHEAD')))")


@pytest.fixture()
def production(tmp_path, monkeypatch):
    """The engine of an app built without `sender_runner`, as `server.main` builds it; the logs under tmp (see test_chunk_log_path_3340)."""
    from server.app import create_app

    chunk_logs, sender_logs = tmp_path / "chunk-logs", tmp_path / "sender-logs"
    chunk_logs.mkdir(), sender_logs.mkdir()
    monkeypatch.setattr(chunklog, "LOG_DIR", chunk_logs)
    monkeypatch.setattr(sender, "SENDER_LOG", sender_logs / "imagesrv-sender.log")
    monkeypatch.setattr(sender, "subprocess", subprocess)       # tests/hygiene.py blocks it (#79); the child here ends by itself
    app = create_app(tmp_path / "data", tmp_path / "images", "http://10.44.12.10:8080", wol_send=lambda _b: None)
    ctx = app.state.ctx
    yield {"runner": ctx.chunk.runner, "part": tmp_path / "images" / "img_1" / PART_FILE}
    ctx.sender.stop()
    ctx.chunk.close()


def _child_sees(production) -> str:
    process = production["runner"]([sys.executable, "-c", CHILD, "--file", str(production["part"])])
    assert process.wait(30) == 0
    return chunklog.log_tail(chunklog.log_path(production["part"]))


@requires_native(posix=True, why="nice/ionice and a real child process")
def test_the_child_of_the_engines_runner_gets_the_thread_reader_with_nothing_set(production):
    assert _child_sees(production) == "reader=thread ahead=None"


@requires_native(posix=True, why="nice/ionice and a real child process")
def test_the_child_gets_the_operators_reader_not_ours(production, monkeypatch):
    monkeypatch.setenv(READER, "inline")
    assert _child_sees(production) == "reader=inline ahead=None"


@requires_native(posix=True, why="nice/ionice and a real child process")
def test_the_child_gets_no_reader_when_the_operator_set_an_ahead_depth(production, monkeypatch):
    monkeypatch.setenv(AHEAD, "8")
    assert _child_sees(production) == "reader=None ahead=8"


def test_the_environment_is_passed_only_when_it_changes_something(monkeypatch, tmp_path):
    """`env=None` is the server's own environment: the child of a send the decision leaves alone is started exactly as before."""
    seen = []
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    monkeypatch.setattr(chunklog.subprocess, "Popen", lambda cmd, **kw: seen.append(kw.get("env", "absent")) or object())
    chunklog.run_process(FILE_CMD)
    monkeypatch.setenv(READER, "inline")
    chunklog.run_process(FILE_CMD)
    assert seen[0] is not None and seen[0] != "absent" and seen[0][READER] == "thread"
    assert seen[1] is None
