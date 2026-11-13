"""#2634: no OS login on tty2-tty6 in a normal boot of the server.

#2438 made the graphical DCUI drop Alt+Fn, but when it cannot run (no
DRM/fbdev) the DCUI falls back to the text screen, which grabs no keyboard:
Alt+F2 reached logind's autovt@tty2 -- an OS login, the one local login
surface of a server with SSH closed (#1266). Only getty@tty1 was masked.

Now getty@ (and autovt@, its alias) carries a drop-in whose conditions open a
login only in the GRUB recovery entry (behind the GRUB password, #2450) or
after a failed first boot, which tells the operator "Alt+F2 to log in" and
marks it in /run. Conditions are evaluated here the way systemd does:
non-triggering ones must all hold; of the triggering (``|``) ones, one must.
That the getty really does not come up on Alt+F2 is a VM check (see the PR).
"""
from __future__ import annotations

import configparser
import re
import shutil
from pathlib import Path

import pytest

from native import requires_native
from server.dcui import app as dcui_app
from test_installer_1131 import BASH, _fake_repo, _seed_db, _status, fake_tool, posix
from test_server_upgrade_1193 import _git_shim, _run

REPO = Path(__file__).resolve().parent.parent
DROPIN = REPO / "deploy" / "getty-recovery-only.conf"
SETUP = REPO / "deploy" / "setup-boot-server.sh"
FIRSTBOOT = REPO / "tools" / "iso" / "firstboot.sh"
FINISH = REPO / "installer" / "lib" / "finish.sh"
TARGET = "/etc/systemd/system/getty@.service.d/imagesrv-recovery-only.conf"
NORMAL = "BOOT_IMAGE=/vmlinuz root=UUID=1234 ro quiet"


def _conditions() -> list[tuple[str, bool, str]]:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(DROPIN.read_text(encoding="utf-8"))
    out = []
    for key, value in parser["Unit"].items():
        assert key.startswith("Condition"), key
        out.append((key, value.startswith("|"), value.lstrip("|")))
    return out


def _starts(cmdline: str, existing: set[str]) -> bool:
    """systemd's rule for one unit's conditions."""
    def holds(key: str, arg: str) -> bool:
        if key == "ConditionKernelCommandLine":
            return arg in cmdline.split()
        if key == "ConditionPathExists":
            return arg in existing
        raise AssertionError(f"a condition this test does not model: {key}")
    plain = [holds(k, a) for k, trig, a in _conditions() if not trig]
    trig = [holds(k, a) for k, t, a in _conditions() if t]
    return all(plain) and (not trig or any(trig))


def _recovery_cmdline() -> str:
    line = re.search(r"linux /vmlinuz (.+)", FINISH.read_text(encoding="utf-8")).group(1)
    return "BOOT_IMAGE=/vmlinuz " + line.replace("$root_uuid", "1234")


def test_a_normal_boot_has_no_login_on_tty2():
    assert not _starts(NORMAL, set())


def test_the_recovery_entry_keeps_its_login():
    cmdline = _recovery_cmdline()
    assert dcui_app.recovery_boot(cmdline), cmdline   # the same entry the DCUI recognises
    assert _starts(cmdline, set())


def test_a_failed_first_boot_opens_the_login_it_promises():
    assert _starts(NORMAL, {"/run/imagesrv-login-open"})
    text = FIRSTBOOT.read_text(encoding="utf-8")
    body = text[text.index("fail() {"):text.index("unexpected_failure() {")]
    assert ": > /run/imagesrv-login-open" in body
    # marked before the line that tells the operator to press Alt+F2
    assert body.index("/run/imagesrv-login-open") < body.index("Press Alt+F2")


def test_the_mark_lives_in_run_so_the_next_boot_is_closed_again():
    paths = [arg for key, _t, arg in _conditions() if key == "ConditionPathExists"]
    assert paths and all(p.startswith("/run/") for p in paths)


def test_the_installer_installs_it_before_systemd_reloads():
    text = SETUP.read_text(encoding="utf-8")
    install = text.index('"$APP_DIR/deploy/getty-recovery-only.conf"')
    assert TARGET in text[install:install + 200]
    first_reload_after = text.index("run systemctl daemon-reload", install)
    assert first_reload_after < text.index("run systemctl restart imagesrv-dcui.service")


@requires_native(("bash", BASH), why="server-upgrade.sh is bash")
def test_the_update_installs_it_on_a_server_installed_before(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "reload-saw-dropin"
    installed = tmp_path / "units" / "getty@.service.d" / "imagesrv-recovery-only.conf"
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", f'''
case "$*" in
  daemon-reload) if [ -f "{posix(installed)}" ]; then echo yes; else echo no; fi >> "{posix(seen)}" ;;
  show*) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;;
esac
exit 0''')
    fake_tool(bindir, "curl", "exit 0")
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(DROPIN, repo / "deploy" / DROPIN.name)

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert installed.read_bytes() == DROPIN.read_bytes()
    assert seen.read_text(encoding="utf-8").split()[0] == "yes"
    assert _status(data)["state"] == "done"
