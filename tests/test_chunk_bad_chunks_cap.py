"""The `ended` report carries a bounded `bad_chunks`: the server refuses a body over 1 MiB (413), and with it the evidence."""

import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from server.agent.body import MAX_BYTES
from test_chunksend_2533 import SID, rig, wait_for  # noqa: F401  (rig is a fixture)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = '. agent/lib/restore/chunkgate.sh; CHUNK_DIR="$1"; chunk_stray_json ended'


def _fields(tmp_path, count, extra="", env=None):
    if os.name == "nt":
        probe = subprocess.run(["sh", "-c", "exit 0"], stdin=subprocess.DEVNULL, capture_output=True)
        if probe.returncode and b"CreateFileMapping" in probe.stderr:
            pytest.skip("Git sh cannot start in this Windows sandbox (CreateFileMapping denied)")
    (tmp_path / "skipped").write_text("".join(f"bad_chunk {k}\n" for k in range(count)) + extra, encoding="ascii")
    out = subprocess.check_output(["sh", "-c", SCRIPT, "sh", tmp_path.as_posix()], stdin=subprocess.DEVNULL,
                                  cwd=ROOT, env={**os.environ, **(env or {})}, text=True)
    return out


def test_a_pass_that_rejected_every_chunk_still_fits_the_servers_body_cap(tmp_path):
    # 250,000 rejected ids (a 1 TB partition, every chunk wrong against the index): ~1.5 MB of ids as one list
    fields = _fields(tmp_path, 250_000, extra="stray 3\n")
    assert len(fields.encode()) < MAX_BYTES // 4, len(fields)
    body = json.loads('{"pass":1' + fields + "}")
    assert body["bad_chunks"] == list(range(len(body["bad_chunks"])))        # the first ones, in order
    assert 0 < len(body["bad_chunks"]) < 250_000
    assert body["bad_chunks_total"] == 250_000                                # the server is told there are more
    assert body["stray"] == 3                                                 # the fields after the list are untouched


def test_a_short_list_is_sent_whole_and_without_a_total(tmp_path):
    body = json.loads('{"pass":1' + _fields(tmp_path, 3) + "}")
    assert body["bad_chunks"] == [0, 1, 2]
    assert "bad_chunks_total" not in body


def test_the_cap_is_a_distinct_count_not_a_line_count(tmp_path):
    (tmp_path / "skipped").write_text("bad_chunk 7\nbad_chunk 7\nbad_chunk 7\n", encoding="ascii")
    out = subprocess.check_output(["sh", "-c", SCRIPT, "sh", tmp_path.as_posix()], stdin=subprocess.DEVNULL,
                                  cwd=ROOT, env={**os.environ, "CHUNK_BAD_MAX": "1"}, text=True)
    body = json.loads('{"pass":1' + out + "}")
    assert body["bad_chunks"] == [7] and "bad_chunks_total" not in body


def _started(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac: []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    return engine


def _ended_with(rig, mac, **extra):
    rig.engine.report(SID, mac, {"part": 3, "pass": 1, "state": "ended", "bad_chunks": [5], "targets": [
        {"serial": mac, "skipped": 0, "durable": [{"part": 3, "ranges": [[0, 4], [6, 9]]}]}], **extra})


def test_the_server_says_when_a_list_arrived_cut(rig, caplog):
    engine = _started(rig)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        _ended_with(rig, "a", bad_chunks_total=250_000)
    assert engine._wave.marks[3, "a"].bad_chunks == frozenset({5})            # the list is used as before
    said = [r.getMessage() for r in caplog.records if "reached the server" in r.getMessage()]
    assert said and "250000" in said[0] and "p3" in said[0], caplog.text


def test_no_total_or_a_total_equal_to_the_list_is_not_said(rig, caplog):
    _started(rig)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        _ended_with(rig, "a")
        _ended_with(rig, "b", bad_chunks_total=1)
    assert not [r for r in caplog.records if "reached the server" in r.getMessage()]
