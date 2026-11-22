"""#3340 -- The log of `chunkcast send` is written where the engine reads it -- on the production wiring, not on an injected runner.

`ChunkEngine` reads the sender's sentence from `chunklog.log_path(<part file>)` (#3261: the reason of a failed pass,
and what the senses of the server read from the same log). In production `create_app` gets no `sender_runner`, and
`DirectChunks.runner(None)` then fell back to `udpcast.run_process` (the old sender's `partition_log`, `imagesrv-sender-...log`):
the output of `chunkcast send` went to one file and the engine read another. Every test of #3261 injected a runner that
writes to `chunklog.log_path` itself, so none of them could see it.

#3173: the old sender is gone, and with it the second log. What is left of the claim: the runner production really holds
writes the log the engine reads (`chunklog.log_path`), and nowhere else.

These tests build the app as production does (no `sender_runner`), take the runner the engine really holds, and run a
real child through it.

Negative control: `DirectChunks.runner` falling back to any runner other than `chunklog.run_process` (#3340: it was
`udpcast.run_process`).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.rounds import chunklog
from test_chunk_send_reason_3261 import PART_FILE, SAID, _fail_with
from test_chunksend_2533 import rig   # noqa: F401 -- a fixture

pytestmark = requires_native(posix=True, why="nice/ionice and a real child process")

#: stands in for `chunkcast send`: progress on stdout, its own sentence on stderr, falls with 2 (what `ir_say` does).
CHILD = f"import sys; print('sent 1 2'); print({SAID!r}, file=sys.stderr); sys.exit(2)"


@pytest.fixture()
def production(tmp_path, monkeypatch):
    """The engine of an app built without `sender_runner`, as `server.main` builds it; the log directory under tmp."""
    from server.app import create_app

    chunk_logs = tmp_path / "chunk-logs"
    chunk_logs.mkdir()
    monkeypatch.setattr(chunklog, "LOG_DIR", chunk_logs)
    # tests/hygiene.py makes `chunklog.subprocess` refuse `chunkcast` by its bare name (#79), and a test never reaches
    # the production runner with an injected one. Here the `chunkcast` is a stand-in on PATH that this test starts and
    # that ends by itself, so the real `subprocess` is put back for this test.
    monkeypatch.setattr(chunklog, "subprocess", subprocess)
    app = create_app(tmp_path / "data", tmp_path / "images", "http://10.44.12.10:7712", wol_send=lambda _b: None)
    ctx = app.state.ctx
    yield {"runner": ctx.chunk.runner, "chunk_logs": chunk_logs,
           "part": tmp_path / "images" / "img_1" / PART_FILE}
    ctx.chunk.stop()
    ctx.chunk.close()


def _fake_chunkcast(tmp_path, monkeypatch):
    """A `chunkcast` on PATH that says what the real one says and falls with 2."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "chunkcast"
    script.write_text(f"#!/bin/sh\necho 'sent 3 12000'\necho '{SAID}' >&2\nexit 2\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")


def test_the_log_of_the_production_runner_is_the_one_the_engine_reads(production):
    process = production["runner"]([sys.executable, "-c", CHILD, "--file", str(production["part"])])
    assert process.wait(30) == 2
    wrote = {p.name: p.read_text() for p in production["chunk_logs"].iterdir()}
    said = chunklog.log_tail(chunklog.log_path(production["part"]))
    assert said == SAID, f"the engine reads {chunklog.log_path(production['part']).name} and got {said!r}; written: {wrote}"


def test_the_failure_the_operator_reads_carries_the_senders_sentence_on_the_production_runner(
        production, rig, tmp_path, monkeypatch):    # noqa: F811
    _fake_chunkcast(tmp_path, monkeypatch)
    why = _fail_with(rig, production["runner"])
    assert why.endswith(f": {SAID}"), why
