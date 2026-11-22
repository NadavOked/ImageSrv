"""#3739 -- a server that is only ever upgraded gets its boot-screen picture drawn by the upgrade.

imagesrv-boot-splash.service (install_units) paints /usr/share/imagesrv/splash-*x*.raw and, with none, exits 1 with "no prebuilt screen" on every boot.
The picture is drawn by the GUI binary (`--splash-raw`), which exists in the tree only after the payload build. setup-boot-server.sh draws it only
when the binary is already there; the live installer copies one. The lab server (LabRoot) had neither -- setup ran before the first payload build --
so the service failed on 13/11 and 16/11 and the upgrade of 19/11 reinstalled the unit and left the picture missing.

The real server-upgrade.sh runs against tools faked on PATH (as test_dnsmasq_upgrade_1816); the GUI binary is a fake that writes what `--splash-raw`
would. Not tested here: the real GUI binary, and a cold boot of a server (needs a machine; the lab server is read-only for this change).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import BASH, _fake_repo, _seed_db, _status, _upgrade_env, fake_tool, posix, run_bash
from test_server_upgrade_1193 import _git_shim

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

RAW_BYTES = 640 * 240 * 4

#: what the GUI's --splash-raw does, three ways (FAKE_SPLASH): a good picture, a crash, an exit 0 with a short file
FAKE_GUI = r'''#!/bin/sh
[ "$1" = --splash-raw ] || exit 64
case "${FAKE_SPLASH:-ok}" in
  ok) head -c %d /dev/zero > "$2" ;;
  crash) echo "cannot open display" >&2; exit 1 ;;
  short) head -c 100 /dev/zero > "$2" ;;
esac
''' % RAW_BYTES


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _upgrade(tmp_path: Path, bindir: Path, *, gui: bool, mode: str = "ok", existing: bytes | None = None):
    data = tmp_path / "data"
    _seed_db(data)
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", 'case "$1" in show) echo "IMAGESRV_URL=http://10.10.10.8:7712 X=y" ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")
    repo = _fake_repo(tmp_path, live_ok=True)
    if gui:
        binary = repo / "native-gui" / "imagesrv-station-gui"
        binary.parent.mkdir()
        binary.write_text(FAKE_GUI, encoding="utf-8", newline="\n")
        binary.chmod(0o755)
    share = tmp_path / "share"
    if existing is not None:
        share.mkdir()
        (share / "splash-640x240.raw").write_bytes(existing)
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": "v0.53.0", "FAKE_SPLASH": mode,
           "IMAGESRV_SPLASH_DIR": posix(share)}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" v0.53.0 "{posix(repo)}"',
                    tmp_path, bindir, env=env)
    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    return share, _status(data)


def test_the_upgrade_draws_the_picture_the_boot_service_needs(tmp_path, bindir):
    share, status = _upgrade(tmp_path, bindir, gui=True)
    raw = share / "splash-640x240.raw"
    assert raw.is_file() and raw.stat().st_size == RAW_BYTES, "the boot service would still fail with 'no prebuilt screen'"
    assert status["state"] == "done" and "מסך האתחול" not in status.get("warning", "")
    assert [p.name for p in share.iterdir()] == ["splash-640x240.raw"], "a temporary file was left beside the picture"


@pytest.mark.parametrize("mode", ["crash", "short"])
def test_a_picture_that_was_not_drawn_is_a_warning_and_never_a_picture(tmp_path, bindir, mode):
    """A crash, or an exit 0 with a short file: said by name, the upgrade still finishes, and no file the service would take is left."""
    share, status = _upgrade(tmp_path, bindir, gui=True, mode=mode)
    assert status["state"] == "done"
    assert "תמונת מסך האתחול לא צוירה" in status["warning"], status
    assert not list(share.glob("splash-*x*.raw")), "a half-drawn picture is where the boot service would find it"


def test_a_failed_draw_keeps_the_picture_the_installer_copied(tmp_path, bindir):
    share, status = _upgrade(tmp_path, bindir, gui=True, mode="crash", existing=b"installer-copy")
    assert (share / "splash-640x240.raw").read_bytes() == b"installer-copy"
    assert "תמונת מסך האתחול לא צוירה" in status["warning"]


def test_no_binary_and_no_picture_is_said_by_name(tmp_path, bindir):
    share, status = _upgrade(tmp_path, bindir, gui=False)
    assert status["state"] == "done"
    assert "ייכשל באתחול" in status["warning"], status


def test_no_binary_but_a_picture_is_left_alone_and_quiet(tmp_path, bindir):
    share, status = _upgrade(tmp_path, bindir, gui=False, existing=b"installer-copy")
    assert (share / "splash-640x240.raw").read_bytes() == b"installer-copy"
    assert "מסך האתחול" not in status.get("warning", "")
