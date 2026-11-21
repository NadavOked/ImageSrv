"""#2787 -- the restored Linux's /etc/hosts keeps its inode (and so its SELinux label).

`_write_linux_files` built `hosts.new` and `mv`d it over `hosts`: a new inode, made from the
agent, with no `security.selinux` xattr. On Fedora (Enforcing) chronyd and systemd-resolved were
then denied the read of `/etc/hosts` (`unlabeled_t`). `/etc/hostname` was fine: it is written
into the inode that is there.

The label itself needs SELinux; the property that keeps it is portable and is what is pinned here:
a hard link made to `hosts` BEFORE the write still points at the file after it, and shows the new
content. An inode that was replaced would leave the link on the old content.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

HOSTS = "127.0.0.1\tlocalhost\n127.0.1.1\tfedora-build\n::1 ip6-localhost\n"


def _write(etc: Path, name: str = "LAB2-INS") -> None:
    sh(f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/postdeploy/hostname_linux.sh; '
       f'_write_linux_files {posix(etc)!r} {name}; echo "rc=$?" > {posix(etc.parent / "rc")!r}')


def _etc(tmp_path: Path) -> Path:
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hostname").write_text("fedora-build\n", encoding="utf-8")
    (etc / "hosts").write_text(HOSTS, encoding="utf-8")
    return etc


def test_hosts_is_written_into_the_inode_that_was_there(tmp_path):
    etc = _etc(tmp_path)
    link = tmp_path / "hosts.keep"
    try:
        os.link(etc / "hosts", link)
    except (OSError, NotImplementedError):
        pytest.skip("no hard links here -- runs on the Testrunner")
    inode = (etc / "hosts").stat().st_ino
    _write(etc)
    assert (tmp_path / "rc").read_text() == "rc=0\n"
    assert (etc / "hosts").stat().st_ino == inode, "the inode was replaced: its label is gone"
    kept = link.read_text(encoding="utf-8").splitlines()
    assert "127.0.1.1\tLAB2-INS" in kept, kept
    assert not any("fedora-build" in line for line in kept), kept
    assert "::1 ip6-localhost" in kept and "127.0.0.1\tlocalhost" in kept


def test_no_hosts_new_is_left_behind(tmp_path):
    etc = _etc(tmp_path)
    _write(etc)
    assert not (etc / "hosts.new").exists()


def test_a_hosts_write_that_does_not_land_is_a_failure(tmp_path):
    """Principle 5: `cat` into a read-only hosts file fails, and the function says so."""
    etc = _etc(tmp_path)
    (etc / "hosts").chmod(0o444)
    if os.access(etc / "hosts", os.W_OK):
        pytest.skip("running as a user that ignores file modes (root / Windows)")
    _write(etc)
    assert (tmp_path / "rc").read_text() == "rc=1\n"
    assert not (etc / "hosts.new").exists()
