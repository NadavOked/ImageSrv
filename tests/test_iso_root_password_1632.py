"""#1632 (Nadav 24/10): "root's password -- I set it, as part of the install."

Before, ``tools/iso/build-iso.sh`` demanded ``--root-password`` /
``IMAGESRV_ROOT_PASSWORD``, hashed it into ``imagesrv/root-password.hash`` on the
ISO, and every server installed from that ISO got the same root password. Now
the operator picks it on the install screen, so the build:

* goes on without any root password (it stopped there before);
* refuses ``--root-password`` (both spellings) and ``IMAGESRV_ROOT_PASSWORD`` by
  name, before any work -- nobody should believe they set something there.

The real build-iso.sh runs on a scratch ``--source`` repo up to its first ISO step
(a netinst that does not exist), the way ``test_iso_lab_gate_1412.py`` names the tag.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_iso_lab_gate_1412 import BUILD_ISO, initrd

NEEDS = requires_native("bash", "git", "cpio", "gzip", "zstd", "python3", "sha256sum", "tar", posix=True)


def _build(tmp_path: Path, *extra: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(src)], check=True, stdin=subprocess.DEVNULL)
    (src / "README").write_text("x\n", encoding="utf-8")
    subprocess.run([*git, "add", "README"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run([*git, "tag", "v9.9.9"], check=True, stdin=subprocess.DEVNULL)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in ("xorriso", "dpkg-scanpackages", "dpkg-deb"):
        if not shutil.which(tool):
            (stubs / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (stubs / tool).chmod(0o755)
    args = ["bash", str(BUILD_ISO), "--out", str(tmp_path / "out"), *extra,
            "--source", str(src), "--live-initrd", str(initrd(tmp_path, (), "gzip")),
            "--netinst", str(tmp_path / "no-such-netinst.iso")]
    return subprocess.run(args, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                          env={"PATH": f"{stubs}:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(tmp_path),
                               **(env or {})})


@NEEDS
def test_the_build_needs_no_root_password(tmp_path):
    run = _build(tmp_path)
    # it went past the old password gate, through the code step, and stopped
    # at the missing netinst -- where we meant it to
    assert run.returncode != 0 and "no-such-netinst.iso" in run.stderr, (run.stdout, run.stderr)
    assert "build-iso: קוד: v9.9.9" in run.stdout, run.stdout
    assert "סיסמת root" not in run.stderr, run.stderr


@NEEDS
@pytest.mark.parametrize(("extra", "env"), [
    (("--root-password", "S3cret!x"), None),
    (("--root-password=S3cret!x",), None),
    ((), {"IMAGESRV_ROOT_PASSWORD": "S3cret!x"}),
], ids=["flag", "flag=", "env"])
def test_a_root_password_at_build_time_is_refused_by_name_before_any_work(tmp_path, extra, env):
    run = _build(tmp_path, *extra, env=env)
    assert run.returncode == 2, (run.stdout, run.stderr)
    assert "#1632" in run.stderr and "במסך ההתקנה" in run.stderr, run.stderr
    assert not (tmp_path / "out").exists(), "the build started before refusing"
    assert "S3cret!x" not in run.stdout + run.stderr


def test_the_iso_carries_no_root_password_hash():
    build = BUILD_ISO.read_text(encoding="utf-8")
    assert "root-password.hash" not in build
    assert "openssl passwd" not in build
    finish = (BUILD_ISO.parents[2] / "installer" / "lib" / "finish.sh").read_text(encoding="utf-8")
    assert "root-password.hash" not in finish and "$CDROM/imagesrv/root" not in finish
