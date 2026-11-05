"""#2450: the server's own GRUB menu, the ISO installer and the clone stick are locked.

#2437 locked the menus the server hands to stations. Three menus stayed open:
the server's own (ESC, then e: init=/bin/sh on the kernel line is root, with
SSH closed in production), the ISO's and the stick's.

* **The server.** ``installer/lib/grublock.sh`` writes the password the
  stations use (``/etc/imagesrv/grub-password``) and
  ``/etc/grub.d/01_imagesrv_lock``, which update-grub runs on every kernel
  upgrade. Every entry grub-mkconfig writes boots without a password (they all
  pass ``$menuentry_id_option``; the lock puts ``--unrestricted`` in front of
  it). The recovery entry asks for it (owner, 04/11). The install fails by
  name when grub.cfg, read back, is not that.
* **The console.** An admin sees the password in the settings (``POST
  /api/console/grub-password``); every view is in the journal.
* **ISO and stick.** ``superusers`` with no password: nobody edits.

The update-grub here is a stand-in that runs the target's own ``/etc/grub.d``
scripts the way grub-mkconfig does, around a 10_linux that writes what
Debian's does (grub-common 2.12-9+deb13u2, read from the ISO pool, 04/11).
Where the real GRUB tools are installed, the lock script also runs against
them (``grub-mkpasswd-pbkdf2``, ``grub-script-check``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from boot import grub_lock
from native import requires_native
from server.system import grub_password
from test_grub_lock_2437 import grub_check

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "installer" / "lib"
FINISH = LIB / "finish.sh"
GRUBLOCK = LIB / "grublock.sh"
ISO_GRUB = REPO / "tools" / "iso" / "grub.cfg.in"
USB_GRUB = REPO / "tools" / "usb" / "grub.cfg.in"
SH = shutil.which("sh")
NODE = shutil.which("node")
UUID = "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"
ROOT_PW = "R00t!chosen-on-screen"
RECOVERY = "ImageSrv recovery (text console)"
OVERRIDE = 'menuentry_id_option="--unrestricted $menuentry_id_option"'

needs_sh = pytest.mark.skipif(SH is None, reason="a POSIX sh is required")


def sh_path(path: Path) -> str:
    """A directory for PATH in the sh that runs here: MSYS reads C:/x as two entries."""
    text = path.as_posix()
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", text):
        return f"/{text[0].lower()}{text[2:]}"
    return text


# ---- GRUB, the way it reads a grub.cfg ---------------------------------------------------


def grub_view(cfg: str) -> tuple[str | None, list[tuple[str, bool]]]:
    """(superusers when the first entry is reached, [(title, boots without a password)]).

    The model of GRUB's script that the lock leans on: assignments in order,
    the ``feature_menuentry_id`` branch taken (GRUB 2.12 has the feature), an
    unquoted ``$var`` split into words (grub-core/script/execute.c,
    ``grub_script_argv_split_append``), and an entry is unrestricted only with
    ``--unrestricted`` among its own arguments (normal/menu.c)."""
    env: dict[str, str] = {}
    branches: list[bool] = []
    superusers_at_first: str | None = None
    entries: list[tuple[str, bool]] = []
    seen_entry = False
    for raw in cfg.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("if "):
            branches.append(True)
            continue
        if line == "else":
            branches[-1] = not branches[-1]
            continue
        if line == "fi":
            branches.pop()
            continue
        if not all(branches):
            continue
        words = shlex.split(line)
        if words[0] == "set" and len(words) == 2 and "=" in words[1]:
            words = words[1:]
        if len(words) == 1 and re.match(r"^[A-Za-z_]\w*=", words[0]):
            name, _, value = words[0].partition("=")
            env[name] = re.sub(r"\$\{?(\w+)\}?", lambda m: env.get(m.group(1), ""), value)
            continue
        if words[0] in ("menuentry", "submenu"):
            if not seen_entry:
                superusers_at_first, seen_entry = env.get("superusers"), True
            args: list[str] = []
            for word in words[1:]:
                if word == "{":
                    break
                if word.startswith("$"):
                    args.extend(env.get(word.strip("${}"), "").split())
                else:
                    args.append(word)
            title = next(a for a in args if not a.startswith("--") and a not in env.get("menuentry_id_option", "").split())
            entries.append((title, "--unrestricted" in args))
    return superusers_at_first, entries


# ---- the installer, with an update-grub that runs the target's /etc/grub.d ----------------

HEADER = r"""
if [ x"${feature_menuentry_id}" = xy ]; then
  menuentry_id_option="--id"
else
  menuentry_id_option=""
fi

export menuentry_id_option
"""

# What Debian's 10_linux writes (grub-common 2.12-9+deb13u2): every entry and
# the submenu pass $menuentry_id_option. HARDCODED_ID stands for a 10_linux
# that would write --id itself; the recovery entries only without
# GRUB_DISABLE_RECOVERY=true, as there.
FAKE_UPDATE_GRUB = r"""
fake_10_linux() {
    _fl_id='$menuentry_id_option'; [ -z "${HARDCODED_ID:-}" ] || _fl_id=--id
    _fl_k=6.12.113+deb13-amd64
    printf "menuentry 'Debian GNU/Linux' --class debian --class gnu-linux --class gnu --class os %s 'gnulinux-simple-%s' {\n\tlinux\t/boot/vmlinuz-%s root=UUID=%s ro  quiet\n}\n" "$_fl_id" "$UUID" "$_fl_k" "$UUID"
    printf "submenu 'Advanced options for Debian GNU/Linux' %s 'gnulinux-advanced-%s' {\n" "$_fl_id" "$UUID"
    printf "\tmenuentry 'Debian GNU/Linux, with Linux %s' --class debian --class gnu-linux --class gnu --class os %s 'gnulinux-%s-advanced-%s' {\n\t\tlinux\t/boot/vmlinuz-%s root=UUID=%s ro  quiet\n\t}\n" "$_fl_k" "$_fl_id" "$_fl_k" "$UUID" "$_fl_k" "$UUID"
    if [ "$1" != true ]; then
        printf "\tmenuentry 'Debian GNU/Linux, with Linux %s (recovery mode)' --class debian --class gnu-linux --class gnu --class os %s 'gnulinux-%s-recovery-%s' {\n\t\tlinux\t/boot/vmlinuz-%s root=UUID=%s ro single\n\t}\n" "$_fl_k" "$_fl_id" "$_fl_k" "$UUID" "$_fl_k" "$UUID"
    fi
    printf "}\nmenuentry 'UEFI Firmware Settings' %s 'uefi-firmware' {\n\tfwsetup\n}\n" "$_fl_id"
}
# grub-mkconfig: the scripts in name order, each one's stdout; one that exits
# non-zero fails update-grub (set -e there). /etc/imagesrv is the target's.
fake_update_grub() {
    _fu_t=$1
    mkdir -p "$_fu_t/boot/grub" || return 1
    _fu_norec=$(sed -n 's/^GRUB_DISABLE_RECOVERY=//p' "$_fu_t/etc/default/grub.d/imagesrv.cfg" 2>/dev/null)
    {
        printf '%s\n' "$HEADER"
        for _fu_s in "$_fu_t"/etc/grub.d/0*; do
            [ -f "$_fu_s" ] || continue
            sed "s#/etc/imagesrv/#$_fu_t/etc/imagesrv/#g" "$_fu_s" | sh || return 1
        done
        fake_10_linux "$_fu_norec"
        for _fu_s in "$_fu_t"/etc/grub.d/[1-9]*; do
            [ -f "$_fu_s" ] || continue
            sh "$_fu_s" || return 1
        done
    } > "$_fu_t/boot/grub/grub.cfg.new" || return 1
    mv "$_fu_t/boot/grub/grub.cfg.new" "$_fu_t/boot/grub/grub.cfg"
}
"""

MKPASSWD_PY = r'''
import hashlib, os, sys
count = 10000
for arg in sys.argv[1:]:
    if arg.startswith("--iteration-count="):
        count = int(arg.split("=", 1)[1])
sys.stdout.write("Enter password: ")
first = sys.stdin.readline().rstrip("\r\n")
sys.stdout.write("\nReenter password: ")
second = sys.stdin.readline().rstrip("\r\n")
if first != second:
    sys.exit("passwords differ")
salt = os.urandom(64)
digest = hashlib.pbkdf2_hmac("sha512", first.encode(), salt, count, 64)
print(f"\nPBKDF2 hash of your password is grub.pbkdf2.sha512.{count}.{salt.hex().upper()}.{digest.hex().upper()}")
'''


def _tools(tmp_path: Path, name: str = "bin") -> tuple[Path, Path]:
    """grub-mkpasswd-pbkdf2 (the hash GRUB's tool makes, by hashlib) and grub-probe, on PATH."""
    bindir = tmp_path / name
    bindir.mkdir()
    argv = tmp_path / "mkpasswd-argv"
    (tmp_path / "mkpasswd.py").write_text(MKPASSWD_PY, encoding="utf-8")
    for name, body in {
        "grub-mkpasswd-pbkdf2": f"""printf '%s\\n' "$*" >> '{argv.as_posix()}'
[ -z "${{MKPASSWD_FAIL:-}}" ] || exit 1
exec '{Path(sys.executable).as_posix()}' '{(tmp_path / "mkpasswd.py").as_posix()}' "$@"
""",
        "grub-probe": f'case "$1" in --target=fs_uuid) echo {UUID} ;; --target=device) echo /dev/sda2 ;; *) exit 1 ;; esac\n',
    }.items():
        path = bindir / name
        path.write_text("#!/bin/sh\n" + body, newline="\n")
        path.chmod(0o755)
    return bindir, argv


def _install(tmp_path: Path, **knobs: str):
    """install_bootloader for real: what the target holds, what was logged, and the run."""
    target = tmp_path / "target"
    target.mkdir()
    bindir, argv = _tools(tmp_path)
    calls = tmp_path / "calls"
    env = "".join(f"{k}='{v}'\nexport {k}\n" for k, v in knobs.items())
    harness = tmp_path / "harness.sh"
    harness.write_text(f"""set -u
{env}PATH='{sh_path(bindir)}':$PATH
UUID={UUID}
HEADER='{HEADER}'
DRY_RUN=0 TARGET='{target.as_posix()}' CDROM='{tmp_path.as_posix()}' ANSWER_DISK=/dev/sda ANSWER_ROOT_PASS='{ROOT_PW}'
SYSROOT='{tmp_path.as_posix()}'
log() {{ printf '%s\\n' "$*" >> '{calls.as_posix()}'; }}
fail() {{ log "fail $*"; exit 2; }}
progress() {{ :; }}
print_command() {{ :; }}
must_run() {{ shift 2; "$@" || {{ log "must_run failed: $*"; exit 2; }}; }}
mount() {{ return 1; }}
ln() {{ log "ln $*"; }}
{FAKE_UPDATE_GRUB}
chroot() {{
    log "chroot $*"; _ch_t=$1; shift
    case "$1" in
        chpasswd) cat > /dev/null ;;
        grub-install) : ;;
        grub-probe) grub-probe "$2" ;;
        update-grub) fake_update_grub "$_ch_t" ;;
        *) return 1 ;;
    esac
}}
write_target() {{
    log "write-target $1 $2"
    mkdir -p "$TARGET${{2%/*}}" && printf '%s' "$3" > "$TARGET$2" && chmod "$1" "$TARGET$2"
}}
. '{FINISH.as_posix()}'
if [ -f '{GRUBLOCK.as_posix()}' ]; then . '{GRUBLOCK.as_posix()}'; fi
install_bootloader
log done
""", newline="\n")
    got = subprocess.run([SH, str(harness)], text=True, capture_output=True, stdin=subprocess.DEVNULL)
    log = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return got, log, target, argv


def _cfg(target: Path) -> str:
    return (target / "boot" / "grub" / "grub.cfg").read_text(encoding="utf-8")


def _password_file(target: Path) -> Path:
    return target / "etc" / "imagesrv" / "grub-password"


@needs_sh
def test_e_and_c_on_the_server_ask_for_the_password_the_stations_use(tmp_path):
    got, log, target, _ = _install(tmp_path)
    assert got.returncode == 0 and log[-1] == "done", (got.stderr, log)
    superusers, _ = grub_view(_cfg(target))
    assert superusers == "imagesrv", "e and c are open: no superusers before the first entry"
    lines = [line for line in _cfg(target).splitlines() if line.startswith("password_pbkdf2 ")]
    assert len(lines) == 1, lines
    _, user, value = lines[0].split()
    password = _password_file(target).read_text(encoding="ascii").strip()
    assert user == grub_lock.GRUB_USER
    assert grub_check(value, password) and not grub_check(value, password + "x")
    assert value.split(".")[3] == str(grub_lock.ITERATIONS)


@needs_sh
def test_a_normal_boot_never_asks_and_the_recovery_entry_does(tmp_path):
    got, log, target, _ = _install(tmp_path)
    assert got.returncode == 0, (got.stderr, log)
    _, entries = grub_view(_cfg(target))
    assert entries[0] == ("Debian GNU/Linux", True), entries   # the default entry: GRUB_DEFAULT=0
    asks = [title for title, free in entries if not free]
    assert asks == [RECOVERY], entries
    assert len(entries) == 5, entries   # Debian, the submenu, its kernel, firmware, recovery
    assert not [t for t, _ in entries if "recovery mode" in t], "Debian's own recovery (single) stays out"


@needs_sh
def test_the_password_is_made_by_the_installer_and_the_server_keeps_it(tmp_path):
    got, log, target, _ = _install(tmp_path)
    assert got.returncode == 0, (got.stderr, log)
    path = _password_file(target)
    password = path.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[a-z2-9]{5}(-[a-z2-9]{5}){3}", password), password
    assert set(password.replace("-", "")) <= set(grub_password._ALPHABET)
    assert "write-target 0600 /etc/imagesrv/grub-password" in log
    # written before update-grub reads it, and the lock script with it
    assert log.index("write-target 0600 /etc/imagesrv/grub-password") < log.index(
        "write-target 0755 /etc/grub.d/01_imagesrv_lock") < log.index(f"chroot {target.as_posix()} update-grub")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # the server's first start reads it -- one password for the server and the stations (#2437)
    assert grub_password.load_or_create(path) == (password, False)


@needs_sh
def test_the_password_reaches_no_argv_no_output_and_no_grub_cfg(tmp_path):
    got, log, target, argv = _install(tmp_path)
    assert got.returncode == 0, (got.stderr, log)
    password = _password_file(target).read_text(encoding="ascii").strip()
    assert argv.read_text(encoding="utf-8").splitlines() == [f"--iteration-count={grub_lock.ITERATIONS}"]
    assert password not in _cfg(target)
    assert password not in got.stdout + got.stderr + "\n".join(log)
    assert password not in (target / "etc" / "grub.d" / "01_imagesrv_lock").read_text(encoding="utf-8")


@needs_sh
def test_a_10_linux_that_stopped_passing_menuentry_id_option_fails_the_install_by_name(tmp_path):
    """Its entries would ask for a password to boot: the server would stop at a prompt."""
    got, log, target, _ = _install(tmp_path, HARDCODED_ID="1")
    assert got.returncode == 2, (got.stderr, log)
    failed = [line for line in log if line.startswith("fail grub-menu-unlocked")]
    assert failed and "an entry asks for the password to boot" in failed[0], log


@needs_sh
def test_no_hash_keeps_update_grub_working_and_fails_the_install_by_name(tmp_path):
    got, log, target, _ = _install(tmp_path, MKPASSWD_FAIL="1")
    assert got.returncode == 2, (got.stderr, log)
    assert not [line for line in log if line.startswith("must_run failed")], "update-grub itself failed"
    failed = [line for line in log if line.startswith("fail grub-menu-unlocked")]
    assert failed and "no password_pbkdf2" in failed[0], log
    superusers, entries = grub_view(_cfg(target))
    assert superusers == "imagesrv" and entries[0][1], "without a hash: locked, and the server still boots"


# ---- the read-back verdict, on its own -------------------------------------------------

LOCK = ['set superusers="imagesrv"', "password_pbkdf2 imagesrv grub.pbkdf2.sha512.100000.AB.CD", OVERRIDE]
DEBIAN = "menuentry 'Debian GNU/Linux' --class debian $menuentry_id_option 'gnulinux-simple-x' {"
REC = f'menuentry "{RECOVERY}" {{'


@needs_sh
@pytest.mark.parametrize("lines, says", [
    ([DEBIAN, *LOCK, REC], "no set superusers before the first entry"),
    ([*LOCK, HEADER.strip(), DEBIAN, REC], "no --unrestricted for the generated entries after 00_header"),
    ([HEADER.strip(), *LOCK, DEBIAN, REC.replace(" {", " --unrestricted {")], "the recovery entry boots without the password"),
    ([HEADER.strip(), *LOCK, DEBIAN], "not one recovery entry (0)"),
    ([HEADER.strip(), *LOCK[:1], LOCK[2], DEBIAN, REC], "no password_pbkdf2 before the first entry"),
    ([HEADER.strip(), *LOCK, REC], "no entry that boots without a password"),
])
def test_the_verdict_names_what_is_wrong_in_grub_cfg(tmp_path, lines, says):
    (tmp_path / "boot" / "grub").mkdir(parents=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text("\n".join(lines) + "\n", newline="\n")
    script = f"""DRY_RUN=0 TARGET='{tmp_path.as_posix()}'
fail() {{ printf '%s\\n' "$*"; exit 2; }}
. '{GRUBLOCK.as_posix()}'
grub_menu_locked
echo locked
"""
    got = subprocess.run([SH, "-c", script], text=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert got.returncode == 2 and got.stdout.startswith("grub-menu-unlocked "), got.stdout + got.stderr
    assert says in got.stdout, got.stdout


@needs_sh
def test_the_verdict_passes_a_locked_menu(tmp_path):
    (tmp_path / "boot" / "grub").mkdir(parents=True)
    (tmp_path / "boot" / "grub" / "grub.cfg").write_text(
        "\n".join([HEADER.strip(), *LOCK, DEBIAN, REC]) + "\n", newline="\n")
    got = subprocess.run([SH, "-c", f"DRY_RUN=0 TARGET='{tmp_path.as_posix()}'\nfail() {{ echo \"$*\"; exit 2; }}\n"
                          f". '{GRUBLOCK.as_posix()}'\ngrub_menu_locked\necho locked"],
                         text=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert got.stdout.strip() == "locked", got.stdout + got.stderr


# ---- the lock script itself -------------------------------------------------------------


def _lock_script(tmp_path: Path, password_file: Path) -> Path:
    got = subprocess.run([SH, "-c", f". '{GRUBLOCK.as_posix()}'; printf '%s' \"$GRUB_LOCK_SCRIPT\""],
                         text=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    assert "file=/etc/imagesrv/grub-password" in got.stdout
    path = tmp_path / "01_imagesrv_lock"
    path.write_text(got.stdout.replace("/etc/imagesrv/grub-password", password_file.as_posix()), newline="\n")
    return path


@needs_sh
@pytest.mark.parametrize("content", [None, "two words\n", "\n", "שלום\n"])
def test_the_lock_script_never_fails_update_grub_and_stays_locked(tmp_path, content):
    """grub-mkconfig runs under set -e: a script that fails fails update-grub,
    and with it a kernel upgrade. A password it cannot use is locked, not open."""
    password_file = tmp_path / "grub-password"
    if content is not None:
        password_file.write_text(content, encoding="utf-8", newline="\n")
    bindir, argv = _tools(tmp_path)
    script = _lock_script(tmp_path, password_file)
    got = subprocess.run([SH, "-c", f"PATH='{sh_path(bindir)}':$PATH; sh '{script.as_posix()}'"],
                         text=True, capture_output=True, stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    assert got.stdout.splitlines() == ['set superusers="imagesrv"', OVERRIDE, "export menuentry_id_option"]
    assert "stay locked, with no password (#2450)" in got.stderr, got.stderr
    assert not argv.exists() or not argv.read_text(encoding="utf-8").strip()


@requires_native("grub-mkpasswd-pbkdf2", "grub-script-check", posix=True,
                 why="the lock script against GRUB's own tools")
def test_the_lock_script_with_grubs_own_tools(tmp_path):
    password_file = tmp_path / "grub-password"
    password_file.write_text("abcde-fghjk-mnpqr-stuvw\n", encoding="ascii")
    got = subprocess.run(["sh", str(_lock_script(tmp_path, password_file))], text=True,
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
    assert got.returncode == 0, got.stderr
    value = next(line for line in got.stdout.splitlines() if line.startswith("password_pbkdf2 ")).split()[2]
    assert grub_check(value, "abcde-fghjk-mnpqr-stuvw")
    cfg = tmp_path / "grub.cfg"
    cfg.write_text(HEADER + got.stdout + DEBIAN + "\n  linux /vmlinuz\n}\n" + REC + "\n  linux /vmlinuz\n}\n")
    check = subprocess.run(["grub-script-check", str(cfg)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
    assert check.returncode == 0, check.stdout + check.stderr


@needs_sh
def test_the_installer_and_the_server_agree_on_the_password(tmp_path):
    def var(name: str) -> str:
        got = subprocess.run([SH, "-c", f". '{GRUBLOCK.as_posix()}'; printf '%s' \"${name}\""],
                             text=True, capture_output=True, stdin=subprocess.DEVNULL)
        return got.stdout
    assert var("GRUB_LOCK_USER") == grub_lock.GRUB_USER
    assert var("GRUB_PASSWORD_FILE") == grub_password.PASSWORD_FILE.as_posix()
    assert var("GRUB_PASSWORD_ALPHABET") == grub_password._ALPHABET
    script = var("GRUB_LOCK_SCRIPT")
    assert f"user={grub_lock.GRUB_USER}\n" in script
    assert f"--iteration-count={grub_lock.ITERATIONS}" in script
    made = subprocess.run([SH, "-c", f". '{GRUBLOCK.as_posix()}'; for i in 1 2 3 4 5; do new_grub_password; done"],
                          text=True, capture_output=True, stdin=subprocess.DEVNULL).stdout.split()
    assert len(set(made)) == 5, made
    for password in made:
        assert grub_password._usable(password)
        assert re.fullmatch(r"[a-z2-9]{5}(-[a-z2-9]{5}){3}", password), password
        assert set(password.replace("-", "")) <= set(grub_password._ALPHABET)


def test_the_recovery_entry_passes_neither_menuentry_id_option_nor_unrestricted():
    text = FINISH.read_text(encoding="utf-8")
    entry = text[text.index(f'menuentry "{RECOVERY}"'):]
    first = entry.splitlines()[0]
    assert first == f'menuentry "{RECOVERY}" {{', first
    assert "GRUB_DISABLE_RECOVERY=true" in text


# ---- the ISO installer and the clone stick ---------------------------------------------


@pytest.mark.parametrize("template", [ISO_GRUB, USB_GRUB], ids=["iso", "usb"])
def test_the_iso_and_the_stick_are_locked_for_everyone_and_boot_without_asking(template):
    text = template.read_text(encoding="utf-8")
    superusers, entries = grub_view(text.replace("@TAG@", "v0").replace("@VERSION@", "v0").replace("@DI_ARGS@", "x"))
    assert superusers == "imagesrv", "e and c are open"
    assert entries and all(free for _, free in entries), entries
    # no password at all: an ISO is built before any server exists, a stick for a release
    assert not [line for line in text.splitlines() if line.strip().startswith("password")]


# ---- the console: an admin sees it, and it is in the journal -----------------------------


def _console(tmp_path: Path, images_root: Path, password_file):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from conftest import complete_console_login
    from server.app import create_app
    from server.console import users

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     health_hooks={"interfaces": lambda: []}, grub_password_file=password_file)
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    complete_console_login(admin, conn, "noc", "admin-pass-123")
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"conn": conn, "admin": admin, "deploy": deploy, "anon": TestClient(app)}


def _viewed(conn) -> list[tuple[str, str]]:
    return [(r["user"], r["detail"]) for r in conn.execute(
        "SELECT user, detail FROM journal WHERE event = 'grub_password_viewed'")]


def test_an_admin_sees_the_password_and_the_view_is_in_the_journal(tmp_path, images_root):
    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)
    password = path.read_text(encoding="ascii").strip()   # made on the first start (#2437)
    r = box["admin"].post("/api/console/grub-password")
    assert r.status_code == 200, r.text
    assert r.json() == {"user": "imagesrv", "password": password}
    assert r.headers.get("cache-control") == "no-store"
    viewed = _viewed(box["conn"])
    assert [user for user, _ in viewed] == ["noc"]
    assert password not in viewed[0][1]


def test_deploy_and_anonymous_get_nothing_and_leave_no_view(tmp_path, images_root):
    path = tmp_path / "etc" / "grub-password"
    box = _console(tmp_path, images_root, path)
    password = path.read_text(encoding="ascii").strip()
    for who, status in (("deploy", 403), ("anon", 401)):
        r = box[who].post("/api/console/grub-password")
        assert r.status_code == status, (who, r.status_code, r.text)
        assert password not in r.text
    assert box["admin"].get("/api/console/grub-password").status_code == 405   # a view is an action
    assert _viewed(box["conn"]) == []


def test_no_password_file_and_an_unreadable_one_say_so_without_a_view(tmp_path, images_root):
    box = _console(tmp_path, images_root, None)
    r = box["admin"].post("/api/console/grub-password")
    assert r.status_code == 404 and "אין סיסמת GRUB" in r.json()["detail"], r.text
    path = tmp_path / "bad" / "grub-password"
    box = _console(tmp_path / "b", images_root, path)
    path.write_text("two words\n", encoding="ascii")
    r = box["admin"].post("/api/console/grub-password")
    assert r.status_code == 503 and "לא נקראה" in r.json()["detail"], r.text
    assert _viewed(box["conn"]) == []


@requires_native(("node", NODE))
def test_the_settings_row_hides_it_until_shown_and_a_secondary_shows_its_own(tmp_path):
    js = (REPO / "server" / "static" / "console.js").read_text(encoding="utf-8")
    names = ["grubPasswordRowHtml", "grubPasswordSheet"]
    found = {n: re.search(rf"^function {n}\(.*?^\}}", js, re.M | re.S) for n in names}
    src = {n: m.group(0) if m else f"function {n}() {{ return ''; }}" for n, m in found.items()}
    row = re.search(r'\{ key: "grub_password", card: "(\w+)", label: "([^"]+)", type: "(\w+)"', js)
    probe = tmp_path / "probe.cjs"
    probe.write_text("""
const esc = (x) => String(x == null ? "" : x);
const ghostBtn = (label, fn) => `<btn ${fn}>${label}</btn>`;
const impactList = () => "";
const isAdmin = () => true;
let SERVER_CTX = null, opened = [], posted = [], reply = {};
const sheet = (o) => opened.push(o);
const post = async (p, b) => { posted.push(p); return reply; };
""" + "\n".join(src.values()) + """
(async () => {
  const out = { own: grubPasswordRowHtml() };
  SERVER_CTX = "haifa"; out.secondary = grubPasswordRowHtml(); grubPasswordSheet(); out.secondaryOpened = opened.length;
  SERVER_CTX = null; grubPasswordSheet();
  const confirm = opened[0];
  out.confirmSubmit = confirm ? confirm.submitLabel : null;
  reply = { user: "imagesrv", password: "" };
  try { await confirm.onSubmit({}); out.empty = "no error"; } catch (e) { out.empty = e.message; }
  reply = { user: "imagesrv", password: "abcde-fghjk-mnpqr-stuvw" };
  out.next = typeof (await confirm.onSubmit({}));
  out.posted = posted;
  console.log(JSON.stringify(out));
})();
""", encoding="utf-8", newline="\n")
    got = subprocess.run([NODE, str(probe)], capture_output=True, text=True, encoding="utf-8",
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode == 0, got.stderr
    out = json.loads(got.stdout)
    assert row and row.groups() == ("security", "סיסמת GRUB", "grubpw"), row
    assert "מוסתרת" in out["own"] and "grubPasswordSheet()" in out["own"]
    assert "abcde" not in out["own"]
    assert "disabled" in out["secondary"] and "grubPasswordSheet()" not in out["secondary"]
    assert out["secondaryOpened"] == 0
    assert out["confirmSubmit"] == "הצג"
    assert out["empty"] == "השרת לא החזיר סיסמה"   # principle 5: no password is a visible failure
    assert out["next"] == "function" and out["posted"] == ["/grub-password", "/grub-password"]
    assert 'if (r.type === "grubpw") return grubPasswordRowHtml();' in js
