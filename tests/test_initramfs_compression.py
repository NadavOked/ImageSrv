"""Compression selection follows the target kernel; inspection follows magic."""

from __future__ import annotations

import gzip
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, posix

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "tools" / "initramfs-format.sh"
pytestmark = requires_native(("bash", BASH))


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


@pytest.mark.parametrize(
    ("config", "expected", "message"),
    [("CONFIG_RD_ZSTD=y\n", "zstd", "zstd -19 -T0"),
     ("# CONFIG_RD_ZSTD is not set\n", "gzip", "does not enable")],
)
def test_build_compression_follows_target_kernel_config(tmp_path, config, expected, message):
    (tmp_path / "config-test").write_text(config, encoding="ascii")
    done = sh(f'. {posix(HELPER)!r}; initramfs_compressor test {posix(tmp_path)!r}')
    assert done.returncode == 0
    assert done.stdout.strip() == expected
    assert message in done.stderr


def test_unreadable_kernel_config_visibly_falls_back_to_gzip(tmp_path):
    done = sh(f'. {posix(HELPER)!r}; initramfs_compressor missing {posix(tmp_path)!r}')
    assert done.returncode == 0
    assert done.stdout.strip() == "gzip"
    assert "cannot read" in done.stderr


def test_inspector_decompresses_gzip_and_zstd_by_magic(tmp_path):
    raw = b"070701initramfs-test\n"
    gz = tmp_path / "gzip.img"; gz.write_bytes(gzip.compress(raw, mtime=0))
    zstd = shutil.which("zstd")
    if not zstd:
        pytest.skip("zstd executable is required to produce the zstd fixture")
    zs = tmp_path / "zstd.img"
    made = subprocess.run([zstd, "-q", "-19", "-o", str(zs)], input=raw,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=20)
    assert made.returncode == 0, made.stderr
    for image, expected in ((gz, "gzip"), (zs, "zstd")):
        done = sh(f'. {posix(HELPER)!r}; initramfs_format {posix(image)!r}; '
                  f'initramfs_decompress {posix(image)!r}')
        assert done.returncode == 0, done.stderr
        assert done.stdout.encode().endswith(raw)
        assert done.stdout.splitlines()[0] == expected


def test_inspector_identifies_both_magic_values_without_guessing(tmp_path):
    gz = tmp_path / "gzip.img"; gz.write_bytes(bytes.fromhex("1f8b0800"))
    zs = tmp_path / "zstd.img"; zs.write_bytes(bytes.fromhex("28b52ffd"))
    for image, expected in ((gz, "gzip"), (zs, "zstd")):
        done = sh(f'. {posix(HELPER)!r}; initramfs_format {posix(image)!r}')
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == expected


def test_inspector_rejects_unknown_magic(tmp_path):
    bad = tmp_path / "plain.img"; bad.write_bytes(b"070701not-compressed")
    done = sh(f'. {posix(HELPER)!r}; initramfs_decompress {posix(bad)!r}')
    assert done.returncode != 0
    assert "unknown compression magic" in done.stderr
