"""#1596 -- fanout's SHA-256 in the kernel (AF_ALG), same digest as before.

The stream hash was ~93% of fanout's CPU on a 2-core cloner. The kernel's
SSSE3/AVX code does the same SHA-256 in fewer cycles. What must not move:

* **The digest.** Byte for byte what the in-process code -- and hashlib --
  gives, at every length: empty, under one block, block edges, and sizes that
  arrive in many reads.
* **Saying which engine ran.** stderr names it; a silent fallback would make
  "the fast path works" unprovable. FANOUT_SHA_ENGINE=kernel refuses to fall
  back, so the test below proves the kernel path is the one exercised.
* **No digest is not a digest.** When the engine cannot give one, no -S file.
* **The build packs and loads the modules** (sha256-generic is built in, so
  the kernel never loads sha256-ssse3 by itself).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_fanout import fanout, needs_fanout  # noqa: F401 -- the compiled binary
from test_initramfs_modules import BUILDER, SHA_LAYOUT, LAYOUT, run_required

SIZES = [0, 1, 55, 56, 63, 64, 65, 4095, 65536, 1048575, 1048576, 1048577,
         3 * 1048576 + 17, 9_999_991]


def _payload(size: int) -> bytes:
    # Not all zeros: a digest that ignored the content would still match zeros.
    return (hashlib.sha256(str(size).encode()).digest() * (size // 32 + 1))[:size]


def _hash_only(binary: Path, data: bytes, engine: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "FANOUT_SHA_ENGINE"}
    if engine is not None:
        env["FANOUT_SHA_ENGINE"] = engine
    return subprocess.run([str(binary), "--sha256"], input=data, capture_output=True,
                          env=env, timeout=60)


@needs_fanout
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("engine", ["kernel", "builtin"])
def test_both_engines_give_hashlibs_digest(fanout, engine, size):
    data = _payload(size)
    done = _hash_only(fanout, data, engine)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode().strip() == hashlib.sha256(data).hexdigest()


@needs_fanout
def test_the_kernel_engine_is_the_one_that_ran(fanout):
    """Not a fallback that happened to give the same digest."""
    done = _hash_only(fanout, b"x", "kernel")
    assert done.returncode == 0, done.stderr
    assert b"sha256 by the kernel (AF_ALG)" in done.stderr, done.stderr


@needs_fanout
def test_the_default_says_which_engine(fanout):
    done = _hash_only(fanout, b"x", None)
    assert done.returncode == 0, done.stderr
    assert (b"sha256 by the kernel (AF_ALG)" in done.stderr
            or b"sha256 in-process (kernel AF_ALG unavailable" in done.stderr), done.stderr


@needs_fanout
@pytest.mark.parametrize("engine", ["kernel", "builtin"])
def test_the_stream_file_matches_across_many_reads(fanout, tmp_path, engine):
    """-S in the real stream path: the pipe hands fanout the bytes in many reads."""
    data = _payload(7 * 1048576 + 333)
    sha_file, out = tmp_path / "sha.out", tmp_path / "target.bin"
    out.write_bytes(b"")                  # fanout opens its targets, it does not create them
    env = {**os.environ, "FANOUT_SHA_ENGINE": engine}
    done = subprocess.run([str(fanout), "-S", str(sha_file), str(4 * 1048576), str(out)],
                          input=data, capture_output=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert sha_file.read_text() == hashlib.sha256(data).hexdigest() + "\n"
    assert out.read_bytes() == data


@needs_fanout
def test_an_engine_that_cannot_be_given_leaves_no_digest(fanout, tmp_path):
    """An unknown FANOUT_SHA_ENGINE is refused, not guessed -> exit 2 and no
    -S file: "not computed" is a state of its own (#73). (The kernel failing
    mid-stream takes the same no-file path; it cannot be injected from here.)"""
    sha_file = tmp_path / "sha.out"
    (tmp_path / "t.bin").write_bytes(b"")
    done = subprocess.run([str(fanout), "-S", str(sha_file), str(4 * 1048576),
                           str(tmp_path / "t.bin")], input=b"abc", capture_output=True,
                          env={**os.environ, "FANOUT_SHA_ENGINE": "kernal"}, timeout=60)
    assert done.returncode == 2, done.stderr
    assert not sha_file.exists()
    assert b"unknown FANOUT_SHA_ENGINE=kernal" in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES is a bash array")
@pytest.mark.parametrize("absent", sorted(SHA_LAYOUT))
def test_the_build_stops_when_a_sha_module_is_missing(tmp_path, absent):
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != absent])
    assert done.returncode != 0, f"{absent} missing and the build passed"
    assert absent in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES is a bash array")
def test_the_sha_modules_are_packed(tmp_path):
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in SHA_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} not packed"


def test_the_sha_modules_are_loaded_at_boot_and_not_required():
    """Loaded by name at boot (the kernel will not ask for sha256-ssse3 itself),
    and not in modules.required: a CPU without SSSE3 is not a broken initramfs."""
    text = BUILDER.read_text(encoding="utf-8")
    boot = text[text.index("printf '%s\\n' usbcore"):text.index('> "$ROOT/etc/imagesrv/modules"')]
    assert "printf '%s\\n' af_alg algif_hash sha256-ssse3" in boot
    required = text.split("REQUIRED_FS_MODULES=(", 1)[1].split(")", 1)[0]
    for mod in SHA_LAYOUT:
        assert mod not in required, mod
