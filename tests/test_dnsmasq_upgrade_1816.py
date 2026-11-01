"""#1816 -- a server installed before the fix gets dnsmasq's retry in the update.

setup-boot-server.sh writes dnsmasq.service.d/imagesrv-restart.conf (a late
USB NIC / VLAN: dnsmasq exits "unknown interface" and comes back when it
appears, test_dnsmasq_1816). A server that is only ever updated by the button
-- every server installed from an ISO before #1816, the lab's TA among them --
runs server-upgrade.sh and never setup-boot-server.sh, so without this step
its dnsmasq stays down after a reboot without the NIC until someone restarts
it. The real script runs against tools faked on PATH, as in
test_server_upgrade_1737.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import BASH, _fake_repo, _seed_db, _status, fake_tool, posix
from test_server_upgrade_1193 import _git_shim, _run

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

REPO = Path(__file__).resolve().parent.parent
DROPIN = REPO / "deploy" / "dnsmasq-imagesrv-restart.conf"


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _tools(tmp_path: Path, bindir: Path) -> Path:
    """systemctl notes, at each daemon-reload, whether systemd would see the drop-in."""
    seen = tmp_path / "reload-saw-dropin"
    installed = tmp_path / "units" / "dnsmasq.service.d" / "imagesrv-restart.conf"
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", f'''
case "$*" in
  daemon-reload) if [ -f "{posix(installed)}" ]; then echo yes; else echo no; fi >> "{posix(seen)}" ;;
  show*) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;;
esac
exit 0''')
    fake_tool(bindir, "curl", "exit 0")
    return seen


def _repo_with_dropin(tmp_path: Path) -> Path:
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(DROPIN, repo / "deploy" / DROPIN.name)
    return repo


def test_the_upgrade_installs_the_retry_before_systemd_reloads(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    seen = _tools(tmp_path, bindir)

    proc = _run(tmp_path, bindir, _repo_with_dropin(tmp_path), data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    installed = tmp_path / "units" / "dnsmasq.service.d" / "imagesrv-restart.conf"
    assert installed.is_file(), "the update did not install dnsmasq's retry"
    assert installed.read_bytes() == DROPIN.read_bytes()
    # The first reload after the units already sees it -- no second reload is needed.
    assert seen.read_text(encoding="utf-8").split()[0] == "yes"
    status = _status(data)
    assert status["state"] == "done" and "dnsmasq" not in status.get("warning", "")


def test_a_retry_that_cannot_be_installed_is_a_warning_not_a_rollback(tmp_path, bindir):
    """dnsmasq works without it while the NIC is there -- but "not installed" is said."""
    data = tmp_path / "data"
    _seed_db(data)
    _tools(tmp_path, bindir)
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "dnsmasq.service.d").write_text("a file, not a directory\n", encoding="utf-8")

    proc = _run(tmp_path, bindir, _repo_with_dropin(tmp_path), data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    status = _status(data)
    assert status["state"] == "done"
    assert "הניסיון החוזר של dnsmasq (#1816) לא הותקן: " in status["warning"]


def test_a_tag_without_the_file_skips_the_step_by_name(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    _tools(tmp_path, bindir)

    proc = _run(tmp_path, bindir, _fake_repo(tmp_path, live_ok=True), data)

    assert proc.returncode == 0
    assert not (tmp_path / "units" / "dnsmasq.service.d").exists()
    assert "גרסה בלי ניסיון חוזר ל-dnsmasq" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")
