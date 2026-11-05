"""#2452: a server that upgrades from the console's button gets the GRUB lock of #2450 too.

#2450 locked the server's own GRUB menu (e and c ask for the password, the
recovery entry asks for it, every other entry boots without it) -- but only in
``installer/lib/finish.sh``, on a fresh install. Servers that were installed
before it and upgrade stayed open.

``deploy/grub-lock-host.sh`` brings the same lock to the running server, and
``tools/server-upgrade.sh`` runs it after the new server is verified:

* the password is made only when there is none -- an existing one is never
  replaced (the stations' menus are locked with it, #2437);
* ``01_imagesrv_lock`` and ``GRUB_DISABLE_RECOVERY`` are written, the menu is
  generated to a side file and read back first; only a menu that is locked the
  way #2450 means reaches ``update-grub``, and the live grub.cfg is read back
  after it (principle 5);
* a failure is named in the upgrade log, in the run's warning and in the
  journal (``grub_server_lock_failed``) -- and it never fails the upgrade.

The recovery entry stays behind the password (owner, 05/11). A server from
14/10-31/10 has ``41_imagectl_recovery`` ("ImageCtl recovery"), one from
setup-boot-server.sh has none; both are servers this has to lock.

grub-mkconfig here is a stand-in that runs the root's own ``/etc/grub.d``
scripts around a 10_linux that writes what Debian's does (the harness of
test_grub_server_lock_2450).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from boot import grub_lock
from native import requires_native
from server.console import journal_he
from test_grub_lock_2437 import grub_check
from test_grub_server_lock_2450 import FAKE_UPDATE_GRUB, HEADER, SH, UUID, _tools, grub_view, needs_sh, sh_path
from test_installer_1131 import BASH, _status, _upgrade_env, calls, posix, run_bash
from test_server_upgrade_1193 import _success_tools
from test_server_upgrade_1301 import _setup

REPO = Path(__file__).resolve().parent.parent
HOST = REPO / "deploy" / "grub-lock-host.sh"
UPGRADE = REPO / "tools" / "server-upgrade.sh"
PASSWORD = "abcde-fghjk-mnpqr-stuvw"
OLD_CFG = "# the grub.cfg the server booted with before the upgrade\n"
EVENT = "grub_server_lock_failed"

# The recovery entry as the installer wrote it on 14/10 (bcc9cf27) and since 31/10.
RECOVERY_SCRIPT = """#!/bin/sh
set -eu
root_uuid=$(grub-probe --target=fs_uuid /)
cat <<EOF
menuentry "{name} recovery (text console)" {{
    search --no-floppy --fs-uuid --set=root $root_uuid
    linux /vmlinuz root=UUID=$root_uuid ro nomodeset systemd.unit=multi-user.target
    initrd /initrd.img
}}
EOF
"""

# grub-mkconfig -o <file>: GRUB_DISABLE_RECOVERY from every /etc/default/grub.d
# file, the way grub-mkconfig sources them; update-grub is grub-mkconfig -o
# /boot/grub/grub.cfg, as on Debian.
MKCONFIG = FAKE_UPDATE_GRUB + r"""
mkconfig() {
    _mk_t=$1; _mk_out=$2
    _mk_norec=$(for _mk_f in "$_mk_t"/etc/default/grub.d/*.cfg; do [ -f "$_mk_f" ] && . "$_mk_f"; done
                printf '%s' "${GRUB_DISABLE_RECOVERY:-}")
    {
        printf '%s\n' "$HEADER"
        for _mk_s in "$_mk_t"/etc/grub.d/0*; do
            [ -f "$_mk_s" ] || continue
            sed "s#/etc/imagesrv/#$_mk_t/etc/imagesrv/#g" "$_mk_s" | sh || return 1
        done
        fake_10_linux "$_mk_norec"
        for _mk_s in "$_mk_t"/etc/grub.d/[1-9]*; do
            [ -f "$_mk_s" ] || continue
            sh "$_mk_s" || return 1
        done
    } > "$_mk_out.new" || return 1
    mv "$_mk_out.new" "$_mk_out"
}
"""


def _root(tmp_path: Path, layout: str) -> Path:
    """A server's / before the upgrade: what each kind of installed server has."""
    root = tmp_path / "root"
    (root / "boot" / "grub").mkdir(parents=True)
    (root / "boot" / "grub" / "grub.cfg").write_text(OLD_CFG, newline="\n")
    grubd, defaults = root / "etc" / "grub.d", root / "etc" / "default" / "grub.d"
    grubd.mkdir(parents=True)
    defaults.mkdir(parents=True)
    if layout == "iso-1014":       # before the #1699 rename
        (defaults / "imagectl.cfg").write_text("GRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=3\n", newline="\n")
        (grubd / "41_imagectl_recovery").write_text(RECOVERY_SCRIPT.format(name="ImageCtl"), newline="\n")
    elif layout == "iso-1101":     # after the rename, before #2450
        (defaults / "imagesrv.cfg").write_text("GRUB_TIMEOUT_STYLE=hidden\nGRUB_TIMEOUT=3\n", newline="\n")
        (grubd / "41_imagesrv_recovery").write_text(RECOVERY_SCRIPT.format(name="ImageSrv"), newline="\n")
    else:
        assert layout == "setup-boot-server"   # Debian's own GRUB, no entry of ours
    return root


def _host(tmp_path: Path, root: Path, *, run: str = "bin", first: Path | None = None, **knobs: str):
    """deploy/grub-lock-host.sh for real, against ``root``, with GRUB's tools on PATH
    (``first``: a directory ahead of them)."""
    bindir, _ = _tools(tmp_path, run)
    lib = tmp_path / "mkconfig.sh"
    lib.write_text(f"UUID={UUID}\nHEADER='{HEADER}'\n{MKCONFIG}", newline="\n")
    log = tmp_path / "grub-calls"
    for name, body in {
        "grub-mkconfig": f"""printf 'grub-mkconfig %s\\n' "$*" >> '{log.as_posix()}'
[ "$1" = -o ] || exit 64
. '{lib.as_posix()}'
mkconfig '{root.as_posix()}' "$2"
""",
        "update-grub": f"""printf 'update-grub\\n' >> '{log.as_posix()}'
[ -z "${{UPDATE_GRUB_FAIL:-}}" ] || {{ echo "/usr/sbin/grub-mkconfig: error: out of space" >&2; exit 1; }}
exec grub-mkconfig -o '{root.as_posix()}/boot/grub/grub.cfg'
""",
    }.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body, newline="\n")
        path.chmod(0o755)
    env = "".join(f"{k}='{v}'; export {k}\n" for k, v in knobs.items())
    path = (f"'{sh_path(first)}':" if first else "") + f"'{sh_path(bindir)}'"
    script = (f"{env}PATH={path}:$PATH; IMAGESRV_GRUB_ROOT='{root.as_posix()}'\n"
              f"export IMAGESRV_GRUB_ROOT\nsh '{HOST.as_posix()}' '{REPO.as_posix()}'")
    got = subprocess.run([SH, "-c", script], text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    ran = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return got, ran


def _cfg(root: Path) -> str:
    return (root / "boot" / "grub" / "grub.cfg").read_text(encoding="utf-8")


def _password(root: Path) -> Path:
    return root / "etc" / "imagesrv" / "grub-password"


def _hash(cfg: str) -> str:
    lines = [line for line in cfg.splitlines() if line.startswith("password_pbkdf2 ")]
    assert len(lines) == 1, lines
    _, user, value = lines[0].split()
    assert user == grub_lock.GRUB_USER
    return value


# ---- deploy/grub-lock-host.sh -------------------------------------------------------------


@needs_sh
@pytest.mark.parametrize("layout", ["iso-1014", "iso-1101", "setup-boot-server"])
def test_an_upgraded_server_is_locked_and_still_boots_without_asking(tmp_path, layout):
    root = _root(tmp_path, layout)
    got, ran = _host(tmp_path, root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout.splitlines()[-1] == f"grub-locked: {root.as_posix()}/boot/grub/grub.cfg"
    assert "update-grub" in ran, ran
    cfg = _cfg(root)
    superusers, entries = grub_view(cfg)
    assert superusers == "imagesrv", "e and c are open: no superusers before the first entry"
    assert grub_check(_hash(cfg), _password(root).read_text(encoding="ascii").strip())
    assert entries[0] == ("Debian GNU/Linux", True), entries      # GRUB_DEFAULT=0 boots without asking
    asks = [title for title, free in entries if not free]
    expected = {"iso-1014": ["ImageCtl recovery (text console)"],
                "iso-1101": ["ImageSrv recovery (text console)"], "setup-boot-server": []}[layout]
    assert asks == expected, entries                                # the recovery entry asks (owner, 05/11)
    assert not [t for t, _ in entries if "recovery mode" in t], "Debian's single-user entries boot unasked"


@needs_sh
def test_a_password_that_is_there_is_kept(tmp_path):
    root = _root(tmp_path, "iso-1101")
    _password(root).parent.mkdir(parents=True)
    _password(root).write_bytes(PASSWORD.encode() + b"\n")
    got, _ = _host(tmp_path, root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert _password(root).read_bytes() == PASSWORD.encode() + b"\n"
    assert grub_check(_hash(_cfg(root)), PASSWORD)


@needs_sh
def test_the_password_made_here_is_the_servers_shape_private_and_never_printed(tmp_path):
    root = _root(tmp_path, "iso-1014")
    got, _ = _host(tmp_path, root)
    assert got.returncode == 0, got.stdout + got.stderr
    password = _password(root).read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[a-z2-9]{5}(-[a-z2-9]{5}){3}", password), password
    assert password not in got.stdout + got.stderr + _cfg(root)
    if os.name == "posix":
        assert stat.S_IMODE(_password(root).stat().st_mode) == 0o600


@needs_sh
def test_a_second_run_changes_nothing_and_says_locked(tmp_path):
    root = _root(tmp_path, "iso-1101")
    first, _ = _host(tmp_path, root)
    password = _password(root).read_text(encoding="ascii")
    second, _ = _host(tmp_path, root, run="bin-2")
    assert (first.returncode, second.returncode) == (0, 0), second.stdout + second.stderr
    assert _password(root).read_text(encoding="ascii") == password
    assert grub_check(_hash(_cfg(root)), password.strip())


@needs_sh
def test_a_lock_that_would_stop_the_boot_is_never_applied(tmp_path):
    """A 10_linux that writes --id itself: its entries would ask for the password
    to boot, and the server would stop at a prompt on the next reboot. Nothing
    reaches the live grub.cfg, and the lock this run added is taken out."""
    root = _root(tmp_path, "iso-1101")
    got, ran = _host(tmp_path, root, HARDCODED_ID="1")
    assert got.returncode != 0
    assert "an entry asks for the password to boot" in got.stderr, got.stderr
    assert got.stderr.strip().splitlines()[-1].startswith("grub-lock-host: grub-menu-unlocked: ")
    assert "update-grub" not in ran, ran
    assert _cfg(root) == OLD_CFG
    assert not (root / "etc" / "grub.d" / "01_imagesrv_lock").exists()
    assert not (root / "etc" / "default" / "grub.d" / "imagesrv-lock.cfg").exists()
    assert not [p for p in (root / "boot" / "grub").iterdir() if p.name != "grub.cfg"], "a side file was left"


@needs_sh
def test_no_hash_is_named_and_the_live_menu_is_not_touched(tmp_path):
    root = _root(tmp_path, "setup-boot-server")
    got, ran = _host(tmp_path, root, MKPASSWD_FAIL="1")
    assert got.returncode != 0
    assert "no password_pbkdf2 before the first entry" in got.stderr, got.stderr
    assert "update-grub" not in ran and _cfg(root) == OLD_CFG


@needs_sh
def test_update_grub_failing_is_named(tmp_path):
    root = _root(tmp_path, "iso-1101")
    got, ran = _host(tmp_path, root, UPDATE_GRUB_FAIL="1")
    assert got.returncode != 0
    assert got.stderr.strip().splitlines()[-1].startswith("grub-lock-host: grub-config: update-grub failed")
    assert "grub-locked:" not in got.stdout
    assert ran.count("update-grub") == 1


@needs_sh
def test_a_live_menu_that_does_not_read_back_locked_is_a_failure(tmp_path):
    """Principle 5: update-grub said 0 is not the verdict -- grub.cfg read back is."""
    root = _root(tmp_path, "iso-1101")
    bindir = tmp_path / "bin-pre"
    bindir.mkdir()
    # an update-grub that answers 0 and writes the old menu
    (bindir / "update-grub").write_text(f"#!/bin/sh\nprintf '%s' '{OLD_CFG}' > '{root.as_posix()}/boot/grub/grub.cfg'\n",
                                        newline="\n")
    (bindir / "update-grub").chmod(0o755)
    got, _ = _host(tmp_path, root, first=bindir)
    assert got.returncode != 0, got.stdout + got.stderr
    assert "no set superusers before the first entry" in got.stderr, got.stderr
    assert "grub-locked:" not in got.stdout


# ---- tools/server-upgrade.sh runs it -------------------------------------------------------

TAG = "v0.53.0"

FAKE_HOST = """#!/bin/sh
printf 'grub-lock-host %s\\n' "$*" >> "$FAKE_LOG"
case "${FAKE_GRUB_LOCK:-ok}" in
  ok) echo "grub-locked: /boot/grub/grub.cfg" ;;
  fail) echo "grub-lock-host: grub-config: update-grub failed (exit 1)" >&2; exit 1 ;;
  silent) exit 0 ;;
esac
"""


def _upgrade(tmp_path: Path, *, host: bool = True, **fake: str):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    data, repo = _setup(tmp_path)
    if host:
        (repo / "deploy" / "grub-lock-host.sh").write_text(FAKE_HOST, encoding="utf-8", newline="\n")
    _success_tools(bindir)
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": TAG, **fake}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {TAG} "{posix(repo)}"',
                    tmp_path, bindir, env=env)
    text = (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, text
    return _status(data), calls(tmp_path), text, _journal(data), repo


def _journal(data: Path) -> list[tuple[str, str, str]]:
    from server.db import connect
    rows = connect(data / "imagesrv.db").execute(
        "SELECT event, detail, user FROM journal WHERE event = ? ORDER BY id", (EVENT,)).fetchall()
    return [(r["event"], r["detail"], r["user"]) for r in rows]


native_bash = requires_native(("bash", BASH), why="server-upgrade.sh is bash")


@native_bash
def test_the_upgrade_locks_the_servers_grub_after_the_new_server_is_verified(tmp_path):
    status, log, text, journal, repo = _upgrade(tmp_path)
    lock = log.index(f"grub-lock-host {posix(repo)}")
    assert log.index("systemctl is-active --quiet imagesrv-server") < lock
    assert next(i for i, row in enumerate(log) if row.startswith("verify-boot-payload ")) < lock
    assert status["state"] == "done" and "warning" not in status
    assert "grub-locked: /boot/grub/grub.cfg" in text
    assert journal == []


@native_bash
@pytest.mark.parametrize("how, says", [
    ("fail", "grub-lock-host: grub-config: update-grub failed (exit 1)"),
    ("silent", "אין שורת grub-locked"),      # exit 0 with no evidence is not "locked"
])
def test_a_lock_that_failed_is_a_named_warning_and_a_journal_entry_never_a_rollback(tmp_path, how, says):
    status, log, text, journal, _ = _upgrade(tmp_path, FAKE_GRUB_LOCK=how)
    assert not any(" checkout --detach v0.48.0" in row for row in log), "a verified upgrade was rolled back"
    assert status["state"] == "done" and status["stage"] == "done"
    assert "GRUB" in status["warning"] and says in status["warning"], status
    assert f"server-upgrade: {status['warning']}" in text
    assert [(event, user) for event, _, user in journal] == [(EVENT, "server-upgrade")]
    assert says in journal[0][1]


@native_bash
def test_a_tag_without_the_script_says_so_and_warns_of_nothing(tmp_path):
    status, log, text, journal, _ = _upgrade(tmp_path, host=False)
    assert not any(row.startswith("grub-lock-host ") for row in log)
    assert "deploy/grub-lock-host.sh אינו בתג" in text
    assert status["state"] == "done" and "warning" not in status and journal == []


def test_the_journal_event_the_upgrade_writes_has_a_hebrew_label():
    """test_journal_events_he scans server/ only; this event is written by the upgrade script."""
    written = set(re.findall(r'journal\([^,]+,\s*"([a-z0-9_]+)"', UPGRADE.read_text(encoding="utf-8")))
    assert EVENT in written, written
    assert re.search(r"[֐-׿]", journal_he.EVENTS_HE.get(EVENT, "")), journal_he.EVENTS_HE.get(EVENT)


def test_the_host_lock_is_the_installers_lock():
    """One lock: the script sources installer/lib/grublock.sh, never a copy of it."""
    text = HOST.read_text(encoding="utf-8")
    assert '. "$REPO_DIR/installer/lib/grublock.sh"' in text
    assert "GRUB_LOCK_SCRIPT" in text and "grub_menu_locked" in text
    assert "password_pbkdf2" not in text and "menuentry_id_option" not in text


# ---- #2452 (Fable 05/11): an entry that passes no $menuentry_id_option --------------------
#
# The PoC's example (memtest86+) is wrong for Debian 13: memtest86+ 7.20-1's
# /etc/grub.d/20_memtest86+ passes $menuentry_id_option (read from the package, 05/11).
# The mechanism is real: grub-common 2.12-9+deb13u2's 30_os-prober writes its Multiboot
# entry (line 355) without it, and so does any hand-written 40_custom entry. Such an
# entry asks for the password to boot -- it is locked, and the server still boots: it is
# not the default and not one of 10_linux's. The verdict used to refuse the whole lock
# for it, on every upgrade, so the server stayed open.

from test_grub_server_lock_2450 import GRUBLOCK, LOCK, REC  # noqa: E402

MULTIBOOT = 'menuentry "Other OS (on /dev/sdb1, Multiboot)" {'
MEMTEST = '\tmenuentry "Memory test (memtest86+x64.efi)" --class memtest {'
DEBIAN_FREE = "menuentry 'Debian GNU/Linux' --class debian $menuentry_id_option 'gnulinux-simple-x' {"
ADVANCED_ID = "\tmenuentry 'Debian GNU/Linux, with Linux 6.12' --class debian --id 'gnulinux-6.12-advanced-x' {"
ADVANCED = "submenu 'Advanced options for Debian GNU/Linux' $menuentry_id_option 'gnulinux-advanced-x' {"


def _verdict(tmp_path: Path, lines: list[str]) -> subprocess.CompletedProcess:
    (tmp_path / "boot" / "grub").mkdir(parents=True, exist_ok=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text(
        "\n".join([HEADER.strip(), *LOCK, *lines]) + "\n", newline="\n")
    script = (f"DRY_RUN=0 TARGET='{tmp_path.as_posix()}'\nfail() {{ printf '%s\n' \"$*\"; exit 2; }}\n"
              f". '{GRUBLOCK.as_posix()}'\ngrub_menu_locked\necho locked")
    return subprocess.run([SH, "-c", script], text=True, capture_output=True, stdin=subprocess.DEVNULL)


@needs_sh
@pytest.mark.parametrize("extra", [MULTIBOOT, MEMTEST], ids=["os-prober-multiboot", "poc-memtest"])
def test_an_entry_of_someone_elses_without_the_option_is_locked_not_a_reason_to_stay_open(tmp_path, extra):
    got = _verdict(tmp_path, [DEBIAN_FREE, extra, REC])
    assert got.stdout.strip() == "locked", got.stdout + got.stderr


@needs_sh
@pytest.mark.parametrize("lines, entry", [
    ([MULTIBOOT, DEBIAN_FREE, REC], MULTIBOOT),
    ([DEBIAN_FREE, ADVANCED, ADVANCED_ID, "}", REC], ADVANCED_ID.strip()),
], ids=["first-entry", "10_linux-kernel"])
def test_the_entries_that_boot_the_server_must_still_boot_without_asking(tmp_path, lines, entry):
    """Guards (pass before and after the fix): the default entry, and every one of
    10_linux's ('gnulinux-' ids, the Advanced kernels too) still has to boot unasked."""
    got = _verdict(tmp_path, lines)
    assert got.returncode == 2 and got.stdout.startswith("grub-menu-unlocked "), got.stdout + got.stderr
    assert "asks for the password to boot" in got.stdout and entry in got.stdout, got.stdout


OS_PROBER = """#!/bin/sh
cat <<'END'
menuentry "Other OS (on /dev/sdb1, Multiboot)" {
	multiboot /boot/kernel
}
END
"""


@needs_sh
def test_an_upgraded_server_with_such_an_entry_is_locked_and_the_entry_asks(tmp_path):
    root = _root(tmp_path, "iso-1101")
    (root / "etc" / "grub.d" / "30_os-prober").write_text(OS_PROBER, newline="\n")
    got, ran = _host(tmp_path, root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert got.stdout.splitlines()[-1] == f"grub-locked: {root.as_posix()}/boot/grub/grub.cfg"
    superusers, entries = grub_view(_cfg(root))
    assert superusers == "imagesrv" and entries[0] == ("Debian GNU/Linux", True), entries
    assert ("Other OS (on /dev/sdb1, Multiboot)", False) in entries, entries
