"""#2426 -- two low-severity leaks (security scan 04/11, F5 and F6).

F5: ``installer/lib/answers.sh`` exported ``ANSWER_ADMIN_PASS``, so the console admin's password
sat in ``/proc/<pid>/environ`` of every process the installer started -- against #1131, which
``setup-boot-server.sh`` keeps with fd 3. Nothing needs it in an environment: ``finish.sh`` is
sourced and passes it to ``write_target_secret`` (a shell function, ``printf`` is a builtin).

F6: "הורד גיבוי הגדרות" followed a symlink to a *file* in ``data_dir`` and packed content from
outside it. (A symlink to a directory is not recursed into by ``rglob`` on 3.12 or 3.13.)

Both need POSIX: an executable shim and ``sh`` for F5, a symlink for F6 -- Linux testrunner / CI.
"""

from __future__ import annotations

import io
import sqlite3
import sys
import tarfile

import pytest

from server.system import backup
from test_installer_engine import _answers, _command_shims, _machine, _run, _shim

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sh shims and symlinks: Linux testrunner / CI")

ADMIN_PW = "correct horse battery staple"     # _answers' admin_pass


def test_the_admin_password_is_not_in_the_environment_of_the_installers_children(tmp_path):
    """``openssl`` is the first child after ``validate_answers`` (``root_password_hash``): a shim records
    the environment it was given. The hostname is there -- the record works and exports still reach
    children -- and the admin password is not."""
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    seen = tmp_path / "openssl-env"
    _shim(tmp_path / "bin" / "openssl", f"cat > /dev/null\nenv >> '{seen.as_posix()}'\n"
                                        "echo '$6$salt$hash'\n")
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert seen.is_file(), (got.returncode, got.stderr)
    environment = seen.read_text(encoding="utf-8")
    assert "ANSWER_HOSTNAME=imagesrv-ta" in environment, environment
    assert ADMIN_PW not in environment, "the admin password is in a child's environment"


def test_the_settings_download_does_not_follow_a_symlink_out_of_the_data_dir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "shadow").write_bytes(b"root:$6$OUTSIDE-THE-DATA-DIR")
    data = tmp_path / "data"
    data.mkdir()
    (data / "netcfg.conf").write_text("iface=ens33\n", encoding="utf-8")
    (data / "looks-like-config").symlink_to(outside / "shadow")
    (data / "dir-link").symlink_to(outside, target_is_directory=True)
    conn = sqlite3.connect(data / "imagesrv.db")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    payload, _name = backup.build_archive(conn, data, "v0.82.0")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        members = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    assert "data/netcfg.conf" in members                              # the real file is still there
    assert not [n for n in members if "looks-like-config" in n or "dir-link" in n], sorted(members)
    assert all(b"OUTSIDE-THE-DATA-DIR" not in body for body in members.values())
