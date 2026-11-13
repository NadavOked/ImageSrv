"""#2437: the stations' GRUB menu was not locked -- `e`/`c` edited the kernel line.

Without `superusers`, GRUB lets anyone at the keyboard press `e` on the
ImageSrv entry and add `init=/bin/sh` (a root shell in the agent's initramfs,
with every disk of the machine), or `c` for a full GRUB console. ESC held
during boot opens the menu even in the hidden `timeout=0` files, so the lock
goes into **every** file the server writes, not only the visible menu.

What is checked here, from each side:

* **The format.** `password_pbkdf2` reads `grub.pbkdf2.sha512.<c>.<salt>.<hash>`
  (grub-core/commands/password_pbkdf2.c). The string is parsed here the way
  that C parses it, and the digest is re-derived from what was parsed; plus a
  published PBKDF2-HMAC-SHA512 vector, and -- where GRUB's own tool exists --
  a hash made by ``grub-mkpasswd-pbkdf2`` checked by the same verifier.
* **The menus.** Every decision path, the escape file and the TFTP bootstrap
  carry `set superusers` before the first entry, and every `menuentry` is
  `--unrestricted` -- booting never asks for a password (principle 1).
* **No hash = still locked.** A password that could not be read is not
  "open to everyone" (principle 5): `superusers` stays, nobody can edit.
* **The password.** Random, created on the first start (a fresh install or an
  upgraded server), 0600, and only its hash reaches a menu.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from boot import grub_lock
from boot.grub_menu import GrubConfig, render, render_bootstrap, render_local_only
from boot.http import build_config_text, config_for_scope
from server.net import deploy_net
from server.system import grub_password

BASE = "http://10.44.12.10:8080"
PASSWORD = "abcde-fghjk-mnpqr-stuvw"
HASH = grub_lock.pbkdf2_hash(PASSWORD, iterations=1000)   # fast; the format is the same
LOCK = 'set superusers="imagesrv"'
PASSWORD_LINE = f"password_pbkdf2 imagesrv {HASH}"

#: one answer for every branch of decide() -- interface 3
ANSWERS = {
    "bad-answer": "not a dict",
    "bad-schema": {"schema": 99, "known": True},
    "answer-failure-screen": {"schema": 99, "known": True, "answer_failure_screen": True},   # #2666
    "unregistered": {"known": False},
    "unregistered-screen": {"known": False, "unregistered_screen": True},
    "unregistered-autoboot": {"known": False, "unregistered_autoboot": True},
    "task": {"known": True, "role": "classroom", "task": {"id": 1}},
    "session": {"known": True, "role": "build", "session": {"state": "open"}},
    "cloner-wait": {"known": True, "role": "cloner"},
    "no-task-build": {"known": True, "role": "build"},
    "no-task-classroom": {"known": True, "role": "classroom"},
    "no-task-teacher": {"known": True, "role": "teacher"},
    "boot-loop-guard": {"known": True, "role": "classroom", "boot_guard": "exhausted"},
}


def cfg(password_hash: str = HASH) -> GrubConfig:
    """Built per test, never at import: the negative control runs these
    tests against the code before #2437, and they must fail there on what
    the file says -- not on a collection error."""
    if not password_hash:
        return GrubConfig(server_base=BASE)
    return GrubConfig(server_base=BASE, password_hash=password_hash)


NAMES = sorted([*ANSWERS, "recovery-requested", "local-only", "bootstrap"])


def every_file(config: GrubConfig | None = None) -> dict[str, str]:
    config = config or cfg()
    files = {name: render(answer, config) for name, answer in ANSWERS.items()}
    files["recovery-requested"] = render({"known": True}, config, recovery=True)
    password_hash = getattr(config, "password_hash", "")
    files["local-only"] = (render_local_only("x", password_hash=password_hash)
                           if password_hash else render_local_only("x"))
    files["bootstrap"] = render_bootstrap(config)
    return files


# ---- the format, the way GRUB reads it -----------------------------------------------


def _hex2val(ch: str) -> int:
    return int(ch, 16) if ch in "0123456789abcdefABCDEF" else -1


def grub_parse(value: str) -> tuple[int, bytes, bytes]:
    """grub_cmd_password() in password_pbkdf2.c, step by step."""
    prefix = "grub.pbkdf2.sha512."
    assert value.startswith(prefix), "invalid PBKDF2 password (prefix)"
    rest = value[len(prefix):]
    count = re.match(r"[0-9]+", rest)          # grub_strtoul(ptr, &ptr, 0)
    assert count and not (count.group(0).startswith("0") and len(count.group(0)) > 1), \
        "a leading 0 is octal to strtoul(base 0)"
    iterations, rest = int(count.group(0)), rest[count.end():]
    assert rest.startswith("."), "invalid PBKDF2 password (after the count)"
    salt_hex, dot, hash_hex = rest[1:].partition(".")
    assert dot and len(salt_hex) % 2 == 0 and len(hash_hex) % 2 == 0, "odd hex"
    for ch in salt_hex + hash_hex:
        assert _hex2val(ch) >= 0, f"not hex: {ch!r}"
    return iterations, bytes.fromhex(salt_hex), bytes.fromhex(hash_hex)


def grub_check(value: str, entered: str) -> bool:
    """check_password(): grub_crypto_pbkdf2(SHA512, entered, salt, c, buflen)."""
    iterations, salt, digest = grub_parse(value)
    return hashlib.pbkdf2_hmac("sha512", entered.encode(), salt, iterations, len(digest)) == digest


def test_the_hash_is_what_grub_parses_and_checks():
    value = grub_lock.pbkdf2_hash(PASSWORD)
    iterations, salt, digest = grub_parse(value)
    assert iterations >= 100_000
    assert (len(salt), len(digest)) == (64, 64)     # grub-mkpasswd-pbkdf2's sizes
    assert grub_check(value, PASSWORD)
    assert not grub_check(value, PASSWORD + "x")
    assert grub_lock.is_pbkdf2_hash(value)


def test_a_published_pbkdf2_sha512_vector():
    """P="password", S="salt", c=1, dkLen=64 -- the widely published vector."""
    value = grub_lock.pbkdf2_hash("password", salt=b"salt", iterations=1)
    assert value == ("grub.pbkdf2.sha512.1.73616C74."
                     "867F70CF1ADE02CFF3752599A3A53DC4AF34C7A669815AE5D513554E1C8CF252"
                     "C02D470A285A0501BAD999BFE943C08F050235D7D68B1DA55E63F73B60A57FCE")


def test_every_salt_is_new():
    assert grub_lock.pbkdf2_hash(PASSWORD, iterations=1) != grub_lock.pbkdf2_hash(PASSWORD, iterations=1)


@pytest.mark.skipif(shutil.which("grub-mkpasswd-pbkdf2") is None,
                    reason="grub-mkpasswd-pbkdf2 is not installed here (grub-common); "
                           "the format is still checked against the C grammar above")
def test_grub_s_own_tool_agrees_with_this_verifier():
    """GRUB made the hash; the verifier that checks ours accepts GRUB's."""
    out = subprocess.run(["grub-mkpasswd-pbkdf2", "--iteration-count=1000"],
                         input=f"{PASSWORD}\n{PASSWORD}\n", capture_output=True,
                         text=True, timeout=60).stdout
    made = re.search(r"grub\.pbkdf2\.sha512\.\S+", out)
    assert made, out
    assert grub_lock.is_pbkdf2_hash(made.group(0))
    assert grub_check(made.group(0), PASSWORD)
    assert not grub_check(made.group(0), "wrong")


@pytest.mark.parametrize("bad", ["", "grub.pbkdf2.sha512.0100.AA.BB", "grub.pbkdf2.sha512.10.A.BB",
                                 "grub.pbkdf2.sha512.10.AA.BB; insmod linux", "x\npassword_pbkdf2 a b"])
def test_what_is_not_a_hash_never_reaches_a_grub_line(bad):
    assert grub_lock.lock_lines(bad) == [LOCK]


# ---- every file the server writes ------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_every_file_is_locked_before_its_first_entry(name):
    text = every_file()[name]
    lines = text.splitlines()
    assert lines.count(LOCK) == 1, f"{name}: no superusers"
    assert lines.count(PASSWORD_LINE) == 1, f"{name}: no password line"
    first_entry = next((i for i, line in enumerate(lines) if line.startswith("menuentry ")), len(lines))
    first_configfile = next((i for i, line in enumerate(lines) if "configfile " in line), len(lines))
    assert lines.index(LOCK) < min(first_entry, first_configfile)
    assert lines.index(PASSWORD_LINE) == lines.index(LOCK) + 1
    assert text.isascii()


@pytest.mark.parametrize("name", NAMES)
def test_booting_any_entry_never_asks_for_a_password(name):
    """A restricted entry asks for a password even to boot -- a station, or a
    cloner nobody sits at, would wait at that prompt forever (principle 1)."""
    entries = [line for line in every_file(cfg(""))[name].splitlines()
               if line.lstrip().startswith("menuentry ")]
    assert all("--unrestricted" in line for line in entries), entries
    assert not any("--users" in line for line in entries)


def test_the_bootstrap_has_no_entry_of_its_own_but_is_locked():
    text = render_bootstrap(cfg(""))
    assert "menuentry " not in text
    assert text.splitlines().index(LOCK) < text.index("configfile ")


@pytest.mark.parametrize("name", NAMES)
def test_without_a_password_the_lock_stays_and_nobody_can_edit(name):
    lines = every_file(cfg(""))[name].splitlines()
    assert LOCK in lines
    assert not any(line.lstrip().startswith("password_pbkdf2") for line in lines)


def test_the_password_itself_never_reaches_a_file():
    assert not any(PASSWORD in text for text in every_file().values())


# ---- boot/http.py: the escape files and the per-scope config keep the hash ------------


def test_a_malformed_mac_and_a_failed_lookup_are_locked_with_the_password():
    def boom(mac, ip):
        raise RuntimeError("db down")
    for text in (build_config_text("zz", "10.0.0.5", boom, cfg()),
                 build_config_text("aa:bb:cc:dd:ee:ff", "10.0.0.5", boom, cfg())):
        assert PASSWORD_LINE in text.splitlines()


def test_the_address_the_station_reached_keeps_the_hash():
    scoped = config_for_scope({"server": ("10.10.10.8", 8080)}, cfg())
    assert scoped.server_base == "http://10.10.10.8:8080"
    assert scoped.password_hash == HASH


# ---- the password on the server ---------------------------------------------------------


def test_the_first_start_creates_a_random_password_readable_by_root_only(tmp_path):
    path = tmp_path / "etc" / "imagesrv" / "grub-password"
    value, created, error = grub_password.ensure(path)
    assert (created, error) == (True, None)
    stored = path.read_text(encoding="ascii").strip()
    assert re.fullmatch(r"[a-z2-9]{5}(-[a-z2-9]{5}){3}", stored)
    assert grub_check(value, stored)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_next_start_keeps_the_same_password(tmp_path):
    path = tmp_path / "grub-password"
    first, _, _ = grub_password.ensure(path)
    second, created, error = grub_password.ensure(path)
    assert (created, error) == (False, None)
    stored = path.read_text(encoding="ascii").strip()
    assert grub_check(first, stored) and grub_check(second, stored)


def test_two_servers_do_not_share_a_password(tmp_path):
    grub_password.ensure(tmp_path / "a")
    grub_password.ensure(tmp_path / "b")
    assert (tmp_path / "a").read_text() != (tmp_path / "b").read_text()


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_a_password_file_open_to_others_is_closed_again(tmp_path):
    path = tmp_path / "grub-password"
    path.write_text("mine-only\n", encoding="ascii")
    path.chmod(0o644)
    _, _, error = grub_password.ensure(path)
    assert error is None and stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["", "   \n", "has space\n", "שלום\n"])
def test_a_password_grub_cannot_take_is_an_error_and_the_menus_stay_locked(tmp_path, content):
    path = tmp_path / "grub-password"
    path.write_text(content, encoding="utf-8")
    value, created, error = grub_password.ensure(path)
    assert (value, created) == ("", False)
    assert error and "#2437" in error
    assert path.read_text(encoding="utf-8") == content          # not replaced behind anyone's back


def test_a_directory_that_cannot_be_written_is_an_error_not_an_open_menu(tmp_path):
    blocker = tmp_path / "etc"
    blocker.write_text("a file where the directory should be", encoding="ascii")
    value, created, error = grub_password.ensure(blocker / "imagesrv" / "grub-password")
    assert (value, created) == ("", False) and error


# ---- the static file on the TFTP root ---------------------------------------------------


def test_the_console_writes_the_tftp_bootstrap_with_the_password(tmp_path):
    assert deploy_net.write_grub_cfg(BASE, tmp_path, password_hash=HASH) is None
    text = (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii")
    assert text == render_bootstrap(cfg())


def test_complete_hands_the_password_to_the_default_writer(tmp_path):
    hook = deploy_net.default_hooks(HASH)["write_grub_cfg"]
    assert hook(BASE, tmp_path) is None
    assert PASSWORD_LINE in (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii").splitlines()


def _old_bootstrap(tmp_path: Path, host: str = "10.44.9.10:8080") -> Path:
    """What a server before #2437 left on its TFTP root."""
    text = render_bootstrap(GrubConfig(server_base=f"http://{host}"))
    text = "\n".join(line for line in text.splitlines() if line != LOCK) + "\n"
    path = tmp_path / "grub" / "grub.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="ascii", newline="\n")
    return path


def test_an_upgraded_server_locks_the_bootstrap_it_left_on_the_tftp_root(tmp_path):
    path = _old_bootstrap(tmp_path)
    assert deploy_net.refresh_grub_cfg(tmp_path, HASH) is None
    assert path.read_text(encoding="ascii") == render_bootstrap(
        GrubConfig(server_base="http://10.44.9.10:8080", password_hash=HASH))


def test_a_locked_bootstrap_is_rewritten_with_this_starts_hash(tmp_path):
    """#2491 reversed the old "not rewritten on every start": a locked file kept
    the hash of whatever password was current when it was written."""
    deploy_net.write_grub_cfg(BASE, tmp_path, password_hash=HASH)
    path = tmp_path / "grub" / "grub.cfg"
    fresh = grub_lock.pbkdf2_hash(PASSWORD, iterations=1000)
    assert deploy_net.refresh_grub_cfg(tmp_path, fresh) is None
    assert path.read_text(encoding="ascii") == render_bootstrap(
        GrubConfig(server_base=BASE, password_hash=fresh))


def test_the_installers_lock_without_a_password_gets_one_on_the_first_start(tmp_path):
    """setup-boot-server.sh writes the bootstrap before the server ever ran:
    locked, with no password -- the first start adds the password."""
    deploy_net.write_grub_cfg(BASE, tmp_path)
    assert deploy_net.refresh_grub_cfg(tmp_path, HASH) is None
    assert PASSWORD_LINE in (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii").splitlines()


def test_no_bootstrap_yet_is_nothing_to_refresh(tmp_path):
    assert deploy_net.refresh_grub_cfg(tmp_path, HASH) is None
    assert not (tmp_path / "grub").exists()


def test_a_bootstrap_this_server_did_not_write_is_left_alone_and_said(tmp_path):
    path = tmp_path / "grub" / "grub.cfg"
    path.parent.mkdir(parents=True)
    path.write_text("set timeout=5\n", encoding="ascii")
    problem = deploy_net.refresh_grub_cfg(tmp_path, HASH)
    assert problem and "#2437" in problem
    assert path.read_text(encoding="ascii") == "set timeout=5\n"


# ---- the running server ----------------------------------------------------------------


def test_the_served_menu_carries_the_hash_of_the_password_on_disk(tmp_path, images_root):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.app import create_app

    path = tmp_path / "etc" / "grub-password"
    app = create_app(tmp_path / "data", images_root, BASE,
                     health_hooks={"interfaces": lambda: []}, grub_password_file=path)
    text = TestClient(app).get("/boot/menu?mac=aa:bb:cc:dd:ee:01").text
    lines = text.splitlines()
    assert LOCK in lines
    served = next(line for line in lines if line.startswith("password_pbkdf2 imagesrv "))
    assert grub_check(served.split()[-1], path.read_text(encoding="ascii").strip())
    events = [row["event"] for row in app.state.ctx.conn.execute("SELECT event FROM journal")]
    assert "grub_password_created" in events


def test_tests_and_dev_stations_write_no_password_file(tmp_path, images_root):
    """Like sync_dnsmasq: only server.main (on Linux) hands create_runtime a path."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, BASE,
                     health_hooks={"interfaces": lambda: []})
    text = TestClient(app).get("/boot/menu?mac=aa:bb:cc:dd:ee:01").text
    assert LOCK in text.splitlines()
    assert not any(line.startswith("password_pbkdf2") for line in text.splitlines())


def test_main_hands_the_real_path_on_linux_only(monkeypatch):
    import sys
    import types

    import server.main

    fake = types.ModuleType("sys")
    fake.__dict__.update(vars(sys))
    for platform, expected in (("linux", grub_password.PASSWORD_FILE), ("win32", None)):
        fake.platform = platform
        monkeypatch.setattr(server.main, "sys", fake)
        assert server.main._host_config_writes()["grub_password_file"] == expected
