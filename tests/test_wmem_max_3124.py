"""#3124 (B-NET-18), metal 21/11 (`892e891fc`, PR #3955): the sender's send buffer was a tenth of what was asked.

`chunkcast send` asks SO_SNDBUF of 4 MiB (`snd = 4 << 20`, agent/chunkcast.c) and the kernel grants min(ask, wmem_max) * 2.
On LabRoot `net.core.wmem_max` is the stock 212992, so every partition went out with `sndbuf 4194304 425984`. The receiver's
ceiling is raised by the agent itself at run time (rcvbuf.sh -- root in the initramfs); the sender is on the server and not
root, so the server's own install and upgrade raise it: `deploy/91-imagesrv-net-buffers.conf`.

What this guards: the value is the ask (not another number), the installer puts it in /etc/sysctl.d and applies it, and
`server-upgrade.sh` does the same for a server installed before -- with the **read-back** as the evidence (`sysctl -p` can
exit 0 on a host that keeps the old ceiling) and a failure said in the status, never a rollback and never silence.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import BASH, _fake_repo, _seed_db, _status, _upgrade_env, calls, fake_tool, posix, run_bash
from test_server_upgrade_1193 import _git_shim

REPO = Path(__file__).resolve().parent.parent
CONF = REPO / "deploy" / "91-imagesrv-net-buffers.conf"
SETUP = REPO / "deploy" / "setup-boot-server.sh"
CHUNKCAST = REPO / "agent" / "chunkcast.c"
STOCK_WMEM_MAX = 212992   # measured on LabRoot, 21/11 (`sndbuf 4194304 425984` = 2 x this)


def _conf_values() -> dict[str, int]:
    out = {}
    for line in CONF.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = int(value.strip())
    return out


def _asked_sndbuf() -> int:
    m = re.search(r"int s, fd, snd = (\d+) << 20;", CHUNKCAST.read_text(encoding="utf-8"))
    assert m, "chunkcast.c no longer asks SO_SNDBUF as `snd = N << 20`: update this test with the new ask"
    return int(m.group(1)) << 20


def test_the_ceiling_is_the_ask_and_no_other_number():
    """The conf holds exactly one key, and the ceiling lets the whole ask through: min(ask, wmem_max) == ask."""
    values = _conf_values()
    assert list(values) == ["net.core.wmem_max"], values
    assert min(_asked_sndbuf(), values["net.core.wmem_max"]) == _asked_sndbuf()
    assert values["net.core.wmem_max"] > STOCK_WMEM_MAX


def test_the_installer_ships_the_ceiling_applies_it_and_reads_it_back():
    setup = SETUP.read_text(encoding="utf-8")
    assert 'run install -m 0644 "$APP_DIR/deploy/91-imagesrv-net-buffers.conf" /etc/sysctl.d/91-imagesrv-net-buffers.conf' in setup
    assert "sysctl -q -p /etc/sysctl.d/91-imagesrv-net-buffers.conf || warn" in setup   # applied now; a failure is said
    assert "sysctl -n net.core.wmem_max" in setup                                          # and read back: the evidence


# --- the upgrade: the real server-upgrade.sh against recorders -------------------------------------------------------

pytestmark_upgrade = requires_native(("bash", BASH), why="server-upgrade.sh is bash")


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _tools(bindir: Path, tmp_path: Path) -> None:
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", 'case "$*" in show*) echo "IMAGESRV_URL=http://10.10.10.8:7712 X=y" ;; esac\nexit 0')
    fake_tool(bindir, "curl", "exit 0")
    # The kernel as a file: `sysctl -p` stores the conf's wmem_max unless FAKE_SYSCTL_APPLIES=0 (it still exits 0 -- a
    # container, a read-only /proc/sys), `sysctl -n` reads what is stored. The host starts at the stock 212992.
    (tmp_path / "wmem").write_text(f"{STOCK_WMEM_MAX}\n", encoding="utf-8")
    fake_tool(bindir, "sysctl", r'''
case "$1 $2" in
  "-q -p") if [ "${FAKE_SYSCTL_APPLIES:-1}" = 1 ]; then sed -n 's/^net\.core\.wmem_max *= *//p' "$3" > "$FAKE_WMEM"; fi; exit 0 ;;
  "-n net.core.wmem_max") cat "$FAKE_WMEM"; exit 0 ;;
esac
exit 1''')


def _upgrade(tmp_path: Path, bindir: Path, with_conf: bool, applies: str = "1"):
    data = tmp_path / "data"
    _seed_db(data)
    _tools(bindir, tmp_path)
    repo = _fake_repo(tmp_path, live_ok=True)
    if with_conf:
        shutil.copy(CONF, repo / "deploy" / CONF.name)
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": "v0.53.0", "IMAGESRV_SYSCTL_DIR": posix(tmp_path / "sysctl.d"),
           "FAKE_WMEM": posix(tmp_path / "wmem"), "FAKE_SYSCTL_APPLIES": applies}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" v0.53.0 "{posix(repo)}"', tmp_path, bindir, env=env)
    return proc, data


@pytestmark_upgrade
def test_the_upgrade_installs_the_ceiling_applies_it_and_reads_it_back(tmp_path, bindir):
    proc, data = _upgrade(tmp_path, bindir, with_conf=True)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    installed = tmp_path / "sysctl.d" / CONF.name
    assert installed.exists(), "the upgrade did not install the sysctl drop-in"
    assert installed.read_bytes() == CONF.read_bytes()
    log = calls(tmp_path)
    assert f"sysctl -q -p {posix(tmp_path / 'sysctl.d' / CONF.name)}" in log
    assert "sysctl -n net.core.wmem_max" in log
    assert int((tmp_path / "wmem").read_text(encoding="utf-8")) == _conf_values()["net.core.wmem_max"]
    status = _status(data)
    assert status["state"] == "done" and "wmem_max" not in status.get("warning", "")


@pytestmark_upgrade
def test_a_ceiling_that_did_not_take_is_a_warning_from_the_read_back_not_a_rollback(tmp_path, bindir):
    """`sysctl -p` exited 0 and the host still says 212992: rc 0 is not the evidence (principle 5). The upgrade goes on --
    the sender works with a small buffer -- but the status says what was read and what was needed."""
    proc, data = _upgrade(tmp_path, bindir, with_conf=True, applies="0")

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    status = _status(data)
    assert status["state"] == "done"
    warning = status.get("warning", "")
    assert warning.startswith("תקרת ה-socket של השולח (net.core.wmem_max, #3124) לא הוחלה"), warning
    assert f"נקרא חזרה {STOCK_WMEM_MAX}, נדרש {_conf_values()['net.core.wmem_max']}" in warning


@pytestmark_upgrade
def test_a_tag_without_the_file_skips_the_step_by_name(tmp_path, bindir):
    proc, _ = _upgrade(tmp_path, bindir, with_conf=False)

    assert proc.returncode == 0
    assert not (tmp_path / "sysctl.d").exists()
    assert not any(row.startswith("sysctl ") for row in calls(tmp_path))
    assert "גרסה בלי תקרת socket לשולח" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")
