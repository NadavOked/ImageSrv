"""CodeQL py/log-injection on the public gate PR #95 (25 alerts, medium).

`GET /api/v1/agent/boot-step` is a FastAPI endpoint (new since v0.97.0), so its `mac`/`s`/`up`
query values are a remote source for CodeQL and every log line they reach is flagged. Runtime
validation (`normalize_mac`, `trace.is_step`) already keeps a line break out of most of them, but
the log call must not depend on a check that sits two functions away: each value goes through
`one_line` at the write point (same rule as #2160). Every test passes a value with CR/LF straight
to the logging function, so it fails on the unfixed code (the line is split) and passes after.
"""

from __future__ import annotations

import logging
import types

import pytest

from boot import http as boot_http
from server import app as server_app
from server.agent import boottrace
from server.console import monitor
from server.db import connect
from server.learn import boot as learn_boot, boot_marks

FORGED_MAC = "78:ac:c0:9b:11:c2\nFORGED boot step recorded"
FORGED_STEP = "menu\r\nFORGED"


def _messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _single_lines(caplog) -> None:
    messages = _messages(caplog)
    assert messages, "nothing was logged -- the test did not reach the log call"
    for message in messages:
        assert "\n" not in message and "\r" not in message, message
    assert any("FORGED" in message for message in messages)     # the value is kept, only joined to one line


def test_boot_http_one_line_turns_line_breaks_into_spaces():
    assert boot_http._one_line("a\r\nb\nc") == "a  b c"
    assert boot_http._one_line("plain") == "plain"          # a clean value is unchanged


def test_off_vlan_decline_logs_one_line(caplog):
    caplog.set_level(logging.DEBUG, logger=server_app.log.name)
    assert server_app._off_vlan_decline(FORGED_MAC, FORGED_STEP) is False
    _single_lines(caplog)


def test_probe_decline_logs_one_line(caplog):
    caplog.set_level(logging.DEBUG, logger=server_app.log.name)
    assert server_app._probe_decline(FORGED_MAC, FORGED_STEP) is False
    _single_lines(caplog)


def test_unverified_identity_logs_one_line(caplog, monkeypatch):
    monkeypatch.setattr(server_app, "server_source", lambda scope: False)
    monkeypatch.setattr(server_app, "boot_identity_ok", lambda ctx, mac, ip: False)
    rt = types.SimpleNamespace(ctx=None, conn=None)
    caplog.set_level(logging.DEBUG, logger=server_app.log.name)
    scope = {"client": ("10.44.0.9\nFORGED", 1234)}
    assert server_app._record_verified(rt, scope, FORGED_MAC, FORGED_STEP) is False
    _single_lines(caplog)


def test_record_step_failures_log_one_line(caplog):
    caplog.set_level(logging.DEBUG, logger=boot_http.log.name)
    # a recorder that raises -> log.exception with raw_step and mac
    def broken(mac, step):
        raise RuntimeError("down")
    boot_http.record_step(broken, "78:ac:c0:9b:11:c2", "menu", "10.44.0.9\nFORGED")
    # an unusable mac, and a step off the list: the raw values are quoted with %r, still one line
    boot_http.record_step(broken, FORGED_MAC, "menu", "10.44.0.9\nFORGED")
    boot_http.record_step(broken, "78:ac:c0:9b:11:c2", FORGED_STEP, "10.44.0.9\nFORGED")
    messages = _messages(caplog)
    assert len(messages) >= 3
    assert all("\n" not in m and "\r" not in m for m in messages), messages


def test_malformed_marks_log_one_line(caplog):
    caplog.set_level(logging.DEBUG, logger=boot_http.log.name)
    ok = boot_http.record_step(lambda mac, step: True, "78:ac:c0:9b:11:c2", "agent-net",
                               "10.44.0.9\nFORGED", raw_up="init:1\nFORGED")
    assert ok is True
    messages = _messages(caplog)
    assert messages and all("\n" not in m and "\r" not in m for m in messages), messages


def test_a_failed_marks_logger_logs_one_line(caplog):
    caplog.set_level(logging.DEBUG, logger=boot_http.log.name)

    def explode(mac, marks):
        raise RuntimeError("down")
    boot_http.record_step(lambda mac, step: True, "78:ac:c0:9b:11:c2", "agent-net",
                          "10.44.0.9", raw_up="init:1.00", marks_record=explode)
    assert all("\n" not in m for m in _messages(caplog))


@pytest.fixture()
def conn(tmp_path):
    return connect(tmp_path / "pr95.db")


def test_boottrace_timing_failure_logs_one_line(conn, caplog, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("down")
    monkeypatch.setattr(boottrace.learn_boot, "measure", broken)
    caplog.set_level(logging.DEBUG, logger=boottrace.log.name)
    boottrace.record(conn, FORGED_MAC, "menu")
    _single_lines(caplog)


def test_boot_regression_failure_logs_one_line(conn, caplog, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("down")
    monkeypatch.setattr(learn_boot, "regression", broken)
    caplog.set_level(logging.DEBUG, logger=learn_boot.log.name)
    learn_boot.after_record(conn, FORGED_MAC, FORGED_STEP, 3.0)
    _single_lines(caplog)


def test_boot_marks_store_failure_logs_one_line(conn, caplog, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("down")
    monkeypatch.setattr(boot_marks, "learn_boot", types.SimpleNamespace(version=broken))
    caplog.set_level(logging.DEBUG, logger=boot_marks.log.name)
    assert boot_marks.record(conn, FORGED_MAC, {"init": 1.0}) is False
    _single_lines(caplog)


def test_monitor_hello_lookup_failure_logs_one_line(caplog):
    import sqlite3

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("down")
    caplog.set_level(logging.DEBUG, logger=monitor.log.name)
    assert monitor._never_said_hello(types.SimpleNamespace(conn=BrokenConn()), FORGED_MAC) is False
    _single_lines(caplog)
