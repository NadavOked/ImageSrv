"""#3125 (V4, V5) on the production wiring, end to end: the place in the stream is read from the log the runner wrote.

The app is built as production builds it (no `sender_runner`), a real child that prints what `chunkcast send` prints
is started through the runner the engine holds (`chunklog.run_process` since #3340), and `reading()` finds it by its
own command line, reads the log that runner wrote (the same file the engine reads), and says its place: part, pass,
chunk k, the bytes, and N from the index next to the part file.

Negative control: server/learn/server_senses.py back to the base (a reading has no `sender`) -- an assert.
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("fastapi")

from native import requires_native
from server.learn import server_senses as S
from server.rounds import chunklog
from test_chunksend_2533 import Lib

# --- the production wiring, end to end -----------------------------------------------------------------------------

#: stands in for `chunkcast send`: what it prints once a second, and then it keeps streaming. Short sleeps in a loop,
#: not one long `sleep`: killing the script leaves no child behind for more than a moment.
SCRIPT = ("#!/bin/sh\necho 'sent 4 6000000'\ni=0\n"
          "while [ $i -lt 600 ]; do sleep 0.1; i=$((i+1)); done\n")


@pytest.fixture()
def production(tmp_path, monkeypatch):
    """The engine of an app built without `sender_runner`, as `server.main` builds it."""
    from server.app import create_app

    (tmp_path / "chunk-logs").mkdir()
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path / "chunk-logs")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "chunkcast").write_text(SCRIPT, encoding="utf-8")
    (bin_dir / "chunkcast").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    app = create_app(tmp_path / "data", tmp_path / "images", "http://10.44.12.10:8080", wol_send=lambda _b: None)
    ctx = app.state.ctx
    started = []
    yield ctx.chunk.runner, started
    for process in started:
        process.kill()
        process.wait(30)
    ctx.sender.stop()
    ctx.chunk.close()


@requires_native(posix=True, why="/proc, nice/ionice and a real child process")
def test_the_place_in_the_stream_is_read_from_the_log_the_production_runner_wrote(production, tmp_path):
    runner, started = production
    parts = tmp_path / "images" / "img_1"
    parts.mkdir(parents=True)
    Lib(parts, chunks=10)
    part = parts / "p3.windows.pcl.zst"
    cmd = ["chunkcast", "send", "--to", "239.83.67.1:20568", "--session", "k", "--part", "3", "--pass", "1",
           "--index", f"{part}.idx", "--file", str(part), "--kbps", "700000", "--write-kbps", "864000"]
    process = runner(cmd)
    started.append(process)
    end = time.monotonic() + 15
    while time.monotonic() < end and "sent 4" not in (chunklog.log_path(part).read_text()
                                                       if chunklog.log_path(part).exists() else ""):
        time.sleep(0.05)
    got = S.reading(root=tmp_path, interface=None).get("sender")
    assert got is not None and str(process.pid) in got, (got, list(tmp_path.rglob("*.log")))
    seen = got[str(process.pid)]
    assert (seen["part"], seen["pass"], seen["chunks"], seen["bytes"], seen["of"]) == (3, 1, 4, 6_000_000, 10), seen
    assert seen["elapsed_s"] is not None and seen["done"] is None
