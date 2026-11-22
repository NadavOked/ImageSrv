"""#3127 -- the server's `systemctl`/`systemd-run` calls say how long they took and how they ended.

`run_timed` is `subprocess.run` plus one journal line (command, exit code, seconds). Pinned here:
the line for each way a call can end, that nothing else changes (same result object, same
exceptions), and that the five call sites of the touch map's system domain (reboot, poweroff, the
NUT `systemctl`, `systemd-run` for the delayed power action, `systemctl enable dnsmasq`) go
through it.
"""

from __future__ import annotations

import logging
import subprocess
import sys

import pytest

from server import auto_reboot
from server.net import deploy_net
from server.system import ups
from server.system.timedrun import run_timed

PY = sys.executable
LOGGER = "imagesrv.proc"


def _lines(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == LOGGER]


def test_a_successful_call_logs_its_exit_code_and_seconds(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        done = run_timed([PY, "-c", "print('hi')"], what="py ok", capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
    assert done.returncode == 0 and done.stdout.strip() == "hi"      # the result is subprocess.run's own
    (level, line), = _lines(caplog)
    assert level == logging.INFO and line.startswith("py ok: rc=0 in ") and line.endswith("s"), line


def test_a_failing_call_is_a_warning_and_still_returned(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        done = run_timed([PY, "-c", "raise SystemExit(3)"], what="py rc3", capture_output=True,
                         stdin=subprocess.DEVNULL, check=False)
    assert done.returncode == 3
    (level, line), = _lines(caplog)
    assert level == logging.WARNING and line.startswith("py rc3: rc=3 in "), line


def test_check_true_failure_is_logged_and_raised_unchanged(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(subprocess.CalledProcessError) as raised:
        run_timed([PY, "-c", "raise SystemExit(4)"], what="py check", check=True, stdin=subprocess.DEVNULL)
    assert raised.value.returncode == 4
    (level, line), = _lines(caplog)
    assert level == logging.WARNING and line.startswith("py check: rc=4 in "), line


def test_a_timeout_is_logged_with_its_limit_and_raised_unchanged(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(subprocess.TimeoutExpired):
        run_timed([PY, "-c", "import time; time.sleep(30)"], what="py slow", timeout=0.5,
                  stdin=subprocess.DEVNULL, capture_output=True)
    (level, line), = _lines(caplog)
    assert level == logging.WARNING and line.startswith("py slow: timed out after ") and "(limit 0.5s)" in line, line


def test_a_binary_that_is_not_there_is_logged_and_raised_unchanged(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(OSError):
        run_timed(["/no/such/binary-3127"], what="gone", stdin=subprocess.DEVNULL)
    (level, line), = _lines(caplog)
    assert level == logging.WARNING and line.startswith("gone: could not start (after "), line


def test_the_default_label_is_the_first_two_words(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        run_timed([PY, "-c", "pass"], stdin=subprocess.DEVNULL)
    (_, line), = _lines(caplog)
    assert line.startswith(f"{PY} -c: rc=0"), line


# --- the call sites ---------------------------------------------------------------------------

@pytest.fixture
def fake_run(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_the_scheduled_reboot_is_timed(fake_run, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        auto_reboot.default_reboot()
    assert fake_run[0][0] == ["systemctl", "reboot"] and fake_run[0][1]["check"] is True
    assert [m for _, m in _lines(caplog)][0].startswith("systemctl reboot: rc=0 in ")


def test_the_ups_poweroff_is_timed(fake_run, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ups.default_poweroff()
    assert fake_run[0][0] == ["systemctl", "poweroff"] and fake_run[0][1]["timeout"] == 30
    assert [m for _, m in _lines(caplog)][0].startswith("systemctl poweroff: rc=0 in ")


def test_the_nut_systemctl_is_timed_and_still_raises_on_failure(monkeypatch, caplog):
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 5, "", "boom"))
    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(RuntimeError, match="systemctl restart nut-server.service: boom"):
        ups._systemctl("restart", "nut-server.service")
    (level, line), = _lines(caplog)
    assert level == logging.WARNING and line.startswith("systemctl restart: rc=5 in "), line


def test_deploy_nets_run_covers_systemctl_enable_and_systemd_run(fake_run, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert deploy_net.enable_dnsmasq() is None
        ok, _out, _err = deploy_net._run(["systemd-run", "--on-active=3", "--unit=u-1", "systemctl", "reboot"], timeout=15)
    assert ok
    labels = [m.split(":")[0] for _, m in _lines(caplog)]
    assert labels == ["systemctl enable", "systemd-run --on-active=3"], labels
