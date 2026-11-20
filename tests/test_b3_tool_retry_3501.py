"""#3501: `readbackhash.b3_tool` does not remember "no tool" for good.

metal-1115-compare: a `chunkcast` built after the server started stayed invisible until the server was restarted,
and every chunk wave failed "no chunkcast with b3list" in between. A tool that was found is kept for good (the
background pass asks for it per partition); "none" is asked again after `B3_RETRY_S`.

No gcc and no real chunkcast here: `subprocess.run` is the seam, `which` and the clock are injected.
"""

from __future__ import annotations

import logging
import subprocess

import pytest

from server.agent import readbackhash as RB


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def fresh(monkeypatch):
    """The tool as the server finds it itself: nothing remembered, and nothing put there by conftest."""
    monkeypatch.setattr(RB, "_B3_TOOL", [])
    monkeypatch.setattr(RB, "_B3_MISSED", [])
    runs: list[list[str]] = []

    def fake_run(argv, **kw):
        runs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=RB.B3_EMPTY.encode() + b"\n", stderr=b"")

    monkeypatch.setattr(RB.subprocess, "run", fake_run)
    return runs


def test_a_chunkcast_built_after_the_server_started_is_seen_without_a_restart(fresh):
    clock, where = Clock(), {"chunkcast": None}
    which = lambda name: where[name]                                   # noqa: E731
    assert RB.b3_tool(which, clock) is None
    where["chunkcast"] = "/usr/local/bin/chunkcast"                   # tools/server-tools-build.sh ran
    clock.t += RB.B3_RETRY_S - 1
    assert RB.b3_tool(which, clock) is None                            # not asked every call: a pass asks per partition
    clock.t += 2
    assert RB.b3_tool(which, clock) == "/usr/local/bin/chunkcast"
    assert fresh == [["/usr/local/bin/chunkcast", "b3sum"]]            # believed only after it gave the spec's BLAKE3


def test_a_tool_that_was_found_is_kept_for_good(fresh):
    clock, calls = Clock(), []

    def which(name):
        calls.append(name)
        return "/usr/local/bin/chunkcast"

    assert RB.b3_tool(which, clock) == "/usr/local/bin/chunkcast"
    clock.t += 10 * RB.B3_RETRY_S
    assert RB.b3_tool(which, clock) == "/usr/local/bin/chunkcast"
    assert calls == ["chunkcast"] and len(fresh) == 1


def test_a_tool_that_answers_wrong_is_still_no_tool_and_is_asked_again_later(fresh, monkeypatch):
    clock = Clock()

    def silent(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")      # an old chunkcast: no b3sum

    monkeypatch.setattr(RB.subprocess, "run", silent)
    assert RB.b3_tool(lambda name: "/usr/local/bin/chunkcast", clock) is None
    monkeypatch.setattr(RB.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout=RB.B3_EMPTY.encode(), stderr=b""))
    clock.t += RB.B3_RETRY_S + 1
    assert RB.b3_tool(lambda name: "/usr/local/bin/chunkcast", clock) == "/usr/local/bin/chunkcast"


def test_no_tool_is_said_once_not_every_retry(fresh, caplog):
    clock = Clock()
    with caplog.at_level(logging.DEBUG, logger=RB.log.name):
        for _ in range(4):
            assert RB.b3_tool(lambda name: None, clock) is None
            clock.t += RB.B3_RETRY_S + 1
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "no chunkcast with b3list" in warned[0].getMessage()


def test_an_answer_put_there_by_a_test_stays_final(monkeypatch):
    """The other tests of #3332 set `_B3_TOOL = [None]` to say "this server has no tool": it must stay that."""
    monkeypatch.setattr(RB, "_B3_TOOL", [None])
    monkeypatch.setattr(RB, "_B3_MISSED", [])
    clock = Clock()
    clock.t += 10 * RB.B3_RETRY_S
    assert RB.b3_tool(lambda name: pytest.fail("must not ask again"), clock) is None
