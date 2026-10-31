"""‏#1737 — שרת שכבר בשטח מקבל את תקרת ה-journald בעדכון הגרסה.

‏setup-boot-server.sh מתקין אותה בשרת חדש (‏test_disk_fill_1737); שרת המעבדה
TA, וכל שרת שהותקן לפני #1737, מתעדכן רק דרך server-upgrade.sh. הסקריפט
האמיתי רץ מול כלים מדומים על PATH, כמו ב-test_server_upgrade_1193.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH,
    _fake_repo,
    _seed_db,
    _status,
    calls,
    fake_tool,
)
from test_server_upgrade_1193 import _git_shim, _run

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

REPO = Path(__file__).resolve().parent.parent
CONF = REPO / "deploy" / "journald-imagesrv.conf"


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _tools(bindir: Path, journald: str = "exit 0") -> None:
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", f'''
case "$*" in
  "restart systemd-journald") {journald} ;;
  show*) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;;
esac
exit 0''')
    fake_tool(bindir, "curl", "exit 0")


def _repo_with_conf(tmp_path: Path) -> Path:
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(CONF, repo / "deploy" / "journald-imagesrv.conf")
    return repo


def test_the_upgrade_installs_the_journal_ceiling_and_restarts_journald(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    _tools(bindir)

    proc = _run(tmp_path, bindir, _repo_with_conf(tmp_path), data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    installed = tmp_path / "journald.conf.d" / "90-imagesrv.conf"
    assert installed.read_bytes() == CONF.read_bytes()
    assert "systemctl restart systemd-journald" in calls(tmp_path)
    status = _status(data)
    # ‏warning אחר (מסך השרת המדומה כאן) אינו העניין; זה של היומן — אסור שיופיע.
    assert status["state"] == "done" and "תקרת היומן" not in status.get("warning", "")


def test_a_journald_that_does_not_restart_is_a_warning_not_a_rollback(tmp_path, bindir):
    """השרת עובד גם בלי התקרה — אבל "לא הוחלה" נאמר בסטטוס, לא נבלע."""
    data = tmp_path / "data"
    _seed_db(data)
    _tools(bindir, journald='echo "Failed to restart" >&2; exit 1')

    proc = _run(tmp_path, bindir, _repo_with_conf(tmp_path), data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    status = _status(data)
    assert status["state"] == "done"
    assert status["warning"].startswith("תקרת היומן (journald) לא הוחלה: Failed to restart")


def test_a_tag_without_the_file_skips_the_step_by_name(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    _tools(bindir)

    proc = _run(tmp_path, bindir, _fake_repo(tmp_path, live_ok=True), data)

    assert proc.returncode == 0
    assert not (tmp_path / "journald.conf.d").exists()
    assert "systemctl restart systemd-journald" not in calls(tmp_path)
    assert "גרסה בלי תקרת יומן" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")
