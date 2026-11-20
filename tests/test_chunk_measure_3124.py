"""#3124 -- chunk-engine touch points that were not measured or were swallowed (A-DSK-28, A-NET-08, A-DB-06, A-DB-07).

What these tests hold:
* a `zstd -dc` that does not return in time is stopped and named `Underived` (the frame was not judged), not waited for forever;
* a chunk-list file that cannot be removed is said in the log, by name, and the pass is unaffected;
* a `disks_json` that is not JSON still reads as "no disks" (conservative), but the log says it was unreadable.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from server import db
from server.library import chunkformat as F
from server.agent.readbackhash import Underived
from server.rounds import chunkledger, chunksend, ledger

MAC = "aa:bb:cc:dd:ee:01"
SLOW = (sys.executable, "-c", "import time; time.sleep(6)")       # a decompressor that is stuck for longer than the limit


def test_a_decompressor_that_does_not_return_in_time_is_stopped_and_not_judged(monkeypatch):
    monkeypatch.setattr(F, "DECODE_TIMEOUT_S", 1, raising=False)
    began = time.monotonic()
    with pytest.raises(Underived, match="did not finish in 1 s"):
        F.decode_frame(b"x", cmd=SLOW)
    assert time.monotonic() - began < 5, "decode_frame waited for the stuck decompressor"


def test_the_default_decode_timeout_is_a_positive_number_of_seconds():
    assert F.DECODE_TIMEOUT_S > 0


def _pass(tmp_path, listed, *, code=0):
    """One `_send_pass` of the real ChunkEngine, with everything around it a stub: the command carries `--chunks-file listed`."""
    eng = MagicMock()
    eng._command.return_value = ["chunkcast", "send", "--chunks-file", str(listed)]
    eng._lock, eng._stop = threading.Lock(), threading.Event()
    eng._wait_pass.return_value = code
    eng.clock = time.monotonic
    w = MagicMock(round=2, carousel_cut=False, marks={}, part_write={})
    part = {"index": 1, "file": "p1.zst"}
    return chunksend.ChunkEngine._send_pass(eng, w, part, tmp_path / "p1.zst", 1000, 1000, [(0, 9)])


def test_a_chunk_list_that_cannot_be_removed_is_named_in_the_log_and_the_pass_goes_on(tmp_path, monkeypatch, caplog):
    def refuse(_name):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(chunksend.os, "unlink", refuse)
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        assert _pass(tmp_path, tmp_path / "imagesrv-chunks-xyz.txt") is True
    assert "imagesrv-chunks-xyz.txt" in caplog.text and "was not removed" in caplog.text


def test_a_chunk_list_that_is_already_gone_says_nothing(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunksend"):
        assert _pass(tmp_path, tmp_path / "gone.txt") is True
    # only chunksend's own say-so is in question: the stubbed pass has no cast log, and chunklog says that (unrelated)
    assert [r.getMessage() for r in caplog.records if r.name == "imagesrv.chunksend"] == []


def test_a_chunk_list_that_exists_is_removed(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("0-9")
    assert _pass(tmp_path, f) is True
    assert not f.exists()


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    c.close()


def _seen(conn, disks_json):
    conn.execute("INSERT INTO net_devices (mac, disks_json) VALUES (?, ?)", (MAC, disks_json))


def test_an_unreadable_disks_json_is_empty_for_the_ledger_and_said(conn, caplog):
    _seen(conn, "{not json")
    with caplog.at_level(logging.WARNING, logger="imagesrv.ledger"):
        assert ledger._inventory(conn, MAC) == []
    assert MAC in caplog.text and "unreadable" in caplog.text


def test_an_unreadable_disks_json_is_empty_for_the_chunk_ledger_and_said(conn, caplog):
    _seen(conn, "{not json")
    with caplog.at_level(logging.WARNING, logger="imagesrv.chunkledger"):
        assert chunkledger._hello_disks(conn, MAC) == []
    assert MAC in caplog.text and "unreadable" in caplog.text


def test_a_readable_disks_json_says_nothing(conn, caplog):
    _seen(conn, '[{"dev": "sda", "serial": "S1", "size_bytes": 5}]')
    with caplog.at_level(logging.WARNING):
        assert [d["dev"] for d in chunkledger._hello_disks(conn, MAC)] == ["sda"]
        assert len(ledger._inventory(conn, MAC)) == 1
    assert caplog.text == ""
