"""#1361 -- an upgrade restarts the server screen (imagesrv-dcui) too.

The screen on tty1 is its own python process: after "update from the button"
it went on running the code it loaded at boot (Nadav, 13/10: the old English
text screen with the new version in its title). ``tools/server-upgrade.sh``
now restarts it after the new server is verified -- and only then: a screen
that does not come back is a warning on a verified upgrade, never a rollback,
and never silent. The real bash script runs against PATH shims (#1193).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import BASH, _status, _upgrade_env, calls, posix, run_bash
from test_server_upgrade_1301 import _setup
from test_server_upgrade_1193 import _success_tools

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

TAG = "v0.53.0"
UNIT = "imagesrv-dcui.service"


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _upgrade(tmp_path: Path, bindir: Path, **fake: str):
    data, repo = _setup(tmp_path)
    _success_tools(bindir)
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": TAG, **fake}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {TAG} "{posix(repo)}"',
                    tmp_path, bindir, env=env)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, log
    return _status(data), calls(tmp_path), log


def test_a_running_server_screen_is_restarted_after_the_upgrade_is_verified(tmp_path, bindir):
    status, log, _ = _upgrade(tmp_path, bindir)
    server = log.index("systemctl restart imagesrv-server")
    verified = log.index("systemctl is-active --quiet imagesrv-server")
    screen = log.index(f"systemctl restart {UNIT}")
    assert server < verified < screen          # never before the new server is proven
    assert f"systemctl is-active --quiet {UNIT}" in log[screen:]
    assert status["state"] == "done" and "warning" not in status


def test_a_screen_that_does_not_come_back_is_a_warning_not_a_rollback(tmp_path, bindir):
    status, log, text = _upgrade(tmp_path, bindir, FAKE_DCUI_DEAD="1")
    assert f"systemctl restart {UNIT}" in log
    assert not any(" checkout --detach v0.48.0" in row for row in log)   # no rollback
    assert status["state"] == "done" and status["stage"] == "done"
    assert status["warning"].startswith("מסך השרת לא הופעל מחדש")
    assert UNIT in status["warning"] and "server-upgrade: מסך השרת" in text


def test_a_server_without_the_screen_unit_is_said_and_not_a_warning(tmp_path, bindir):
    status, log, text = _upgrade(tmp_path, bindir, FAKE_DCUI_LOAD="not-found", FAKE_DCUI_ACTIVE="inactive")
    assert f"systemctl restart {UNIT}" not in log
    assert "warning" not in status and "אינו מותקן" in text


def test_a_screen_stopped_on_purpose_stays_stopped(tmp_path, bindir):
    status, log, text = _upgrade(tmp_path, bindir, FAKE_DCUI_ACTIVE="inactive")
    assert f"systemctl restart {UNIT}" not in log
    assert "warning" not in status and "עצור (inactive)" in text


def test_could_not_check_is_not_no_screen(tmp_path, bindir):
    """Principle 5: a 'systemctl show' with no answer is a warning, not 'no unit'."""
    status, log, _ = _upgrade(tmp_path, bindir, FAKE_DCUI_LOAD="", FAKE_DCUI_ACTIVE="")
    assert f"systemctl restart {UNIT}" not in log
    assert "לא נבדק" in status["warning"]
