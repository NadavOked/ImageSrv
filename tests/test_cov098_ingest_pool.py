"""Coverage of the failure branches of server/library/ingest_pool.py (#1517):
a worker that fails, hangs, lies or leaves a wrong file is IngestFailed, by name
(principle 6: the server does not trust the worker alone)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
import time

import pytest

from server.library import ingest_pool

PAYLOAD = b"0123456789" * 100
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _command(monkeypatch, code: str) -> None:
    monkeypatch.setattr(ingest_pool, "WORKER_COMMAND", [sys.executable, "-I", "-c", code])


def _worker_printing(monkeypatch, text: str) -> None:
    """A worker that drains its input (like the real one) and prints `text`."""
    _command(monkeypatch, f"import sys; sys.stdin.buffer.read(); print({text!r})")


def _run(path, chunks=(PAYLOAD,)):
    async def gen():
        for c in chunks:
            yield c

    async def go():
        assert ingest_pool.take(1)
        return await ingest_pool.stream(gen(), path, batch=64, sync_every=1 << 20,
                                        wrote=lambda n: None)
    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _clean_slots():
    before = ingest_pool.busy()
    yield
    assert ingest_pool.busy() == before             # every path above gave its slot back


@pytest.fixture
def part(tmp_path):
    p = tmp_path / "part.pcl.zst"
    p.write_bytes(PAYLOAD)
    return p


def _ok(**over):
    return json.dumps({"ok": True, "sha256": GOOD_SHA, "bytes": len(PAYLOAD), **over})


def test_a_good_worker_result_is_accepted(monkeypatch, part):
    _worker_printing(monkeypatch, _ok())
    assert _run(part) == (GOOD_SHA, len(PAYLOAD))


@pytest.mark.parametrize("value", ["abc", "9", "-1", "2.5", "1 2"])
def test_a_bad_workers_setting_turns_workers_off_and_says_so_once(monkeypatch, caplog, value):
    monkeypatch.setattr(ingest_pool, "_warned_bad_setting", False)
    monkeypatch.setenv(ingest_pool.ENV, value)
    with caplog.at_level(logging.WARNING, logger="imagesrv.capture"):
        assert ingest_pool.configured() == 0
        assert ingest_pool.configured() == 0
    warnings = [r for r in caplog.records if ingest_pool.ENV in r.getMessage()]
    assert len(warnings) == 1 and repr(value) in warnings[0].getMessage()


@pytest.mark.parametrize("value,expected", [("", 0), ("0", 0), ("3", 3), (" 8 ", 8)])
def test_a_valid_or_empty_setting_is_read_as_is_without_a_warning(monkeypatch, caplog, value, expected):
    monkeypatch.setattr(ingest_pool, "_warned_bad_setting", False)
    monkeypatch.setenv(ingest_pool.ENV, value)
    with caplog.at_level(logging.WARNING, logger="imagesrv.capture"):
        assert ingest_pool.configured() == expected
    assert not caplog.records


def test_a_worker_that_prints_no_json_is_a_named_failure_with_its_stderr(monkeypatch, part):
    _command(monkeypatch, "import sys; sys.stdin.buffer.read(); print('not json');"
                          " sys.stderr.write('PLANTED-TRACE'); sys.exit(3)")
    with pytest.raises(ingest_pool.IngestFailed) as exc:
        _run(part)
    assert exc.value.kind == "error"
    assert "without a result" in str(exc.value) and "exit code 3" in str(exc.value)
    assert "PLANTED-TRACE" in str(exc.value)


def test_a_worker_that_prints_a_json_list_is_not_a_result(monkeypatch, part):
    _worker_printing(monkeypatch, "[1, 2]")
    with pytest.raises(ingest_pool.IngestFailed, match="without a result"):
        _run(part)


def test_a_worker_that_closes_its_input_early_says_so(monkeypatch, part):
    _command(monkeypatch, "import sys; sys.stdin.close(); import os; os.close(0); sys.exit(0)")
    big = [PAYLOAD * 200] * 50                         # enough to fill the pipe and hit the closed end
    with pytest.raises(ingest_pool.IngestFailed, match="(closed its input early|ended) without a result"):
        _run(part, big)


def test_a_worker_that_gives_no_result_in_time_is_failed_not_awaited_forever(monkeypatch, part):
    monkeypatch.setattr(ingest_pool, "FINISH_TIMEOUT_S", 0.5)
    _command(monkeypatch, "import sys, time; sys.stdin.buffer.read(); time.sleep(60)")
    with pytest.raises(ingest_pool.IngestFailed, match="no result within"):
        _run(part)


def test_a_reader_that_raises_kills_the_worker_and_the_error_propagates(monkeypatch, part):
    _command(monkeypatch, "import time; time.sleep(60)")
    spawned = []
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        proc = await real(*a, **kw)
        spawned.append(proc)
        return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def broken():
        yield b"first"
        raise ConnectionError("client went away")

    async def go():
        assert ingest_pool.take(1)
        await ingest_pool.stream(broken(), part, batch=1, sync_every=1, wrote=lambda n: None)

    started = time.monotonic()
    with pytest.raises(ConnectionError):
        asyncio.run(go())
    assert time.monotonic() - started < 20          # killed, not waited out (it sleeps 60s)
    assert len(spawned) == 1 and spawned[0].returncode is not None   # not left running


@pytest.mark.parametrize("result,kind,text", [
    ({"ok": False, "kind": "enospc", "error": "full"}, "enospc", "failed (enospc)"),
    ({"ok": False, "kind": "io", "error": "boom"}, "error", "failed (io)"),
    ({}, "error", "failed (None)"),
])
def test_a_worker_that_reports_failure_is_failed_with_its_kind(part, result, kind, text):
    with pytest.raises(ingest_pool.IngestFailed, match=text.replace("(", r"\(").replace(")", r"\)")) as exc:
        ingest_pool._checked(result, len(PAYLOAD), part)
    assert exc.value.kind == kind


@pytest.mark.parametrize("digest", [None, 5, "", "ab", "G" * 64, GOOD_SHA.upper(), GOOD_SHA + "0"])
def test_a_missing_or_malformed_sha256_is_never_accepted(part, digest):
    with pytest.raises(ingest_pool.IngestFailed, match="no valid sha256"):
        ingest_pool._checked({"ok": True, "sha256": digest, "bytes": len(PAYLOAD)}, len(PAYLOAD), part)


def test_a_worker_that_miscounts_the_bytes_it_was_sent_is_caught(part):
    with pytest.raises(ingest_pool.IngestFailed, match="counted 1 bytes of the"):
        ingest_pool._checked({"ok": True, "sha256": GOOD_SHA, "bytes": 1}, len(PAYLOAD), part)


def test_a_file_whose_size_on_disk_differs_is_caught_even_when_the_worker_agrees(part):
    part.write_bytes(PAYLOAD[:-1])
    with pytest.raises(ingest_pool.IngestFailed, match="on the server disk is"):
        ingest_pool._checked({"ok": True, "sha256": GOOD_SHA, "bytes": len(PAYLOAD)}, len(PAYLOAD), part)


def test_a_file_that_cannot_be_read_back_is_a_failure_not_a_pass(tmp_path):
    with pytest.raises(ingest_pool.IngestFailed, match="could not be read back"):
        ingest_pool._checked({"ok": True, "sha256": GOOD_SHA, "bytes": len(PAYLOAD)},
                             len(PAYLOAD), tmp_path / "missing")


def test_the_priority_problems_of_a_worker_are_logged_not_swallowed(monkeypatch, caplog, part):
    _worker_printing(monkeypatch, _ok(priority=["PLANTED nice failed"]))
    with caplog.at_level(logging.WARNING, logger="imagesrv.capture"):
        assert _run(part) == (GOOD_SHA, len(PAYLOAD))
    assert any("PLANTED nice failed" in r.getMessage() for r in caplog.records)


def test_slots_are_bounded_and_released(monkeypatch):
    base = ingest_pool.busy()
    assert ingest_pool.take(base + 1) is True
    assert ingest_pool.take(base + 1) is False          # ceiling full: caller stays in-process
    assert ingest_pool.busy() == base + 1
    ingest_pool.give()
    assert ingest_pool.busy() == base
