"""#1412 -- a lab initrd (dropbear, authorized_keys) does not reach a product ISO unmarked.

``tools/iso/build-iso.sh`` hands the live installer's initrd to
``initrd-lab-gate.sh``: lab SSH material without ``--lab`` stops the build; with
``--lab`` the build goes on, and the tag -- the ISO's name and the manifest's
``tag`` -- ends in ``-lab``.

``test_iso_build.py`` had one case (authorized_keys, the gate alone) and checked the
``-lab`` name by grepping the script. Here, behaviourally:

* dropbear alone, authorized_keys alone, both -- each refused without ``--lab``,
  in a gzip and in a zstd initrd (the two the gate decompresses);
* a clean initrd passes without ``--lab`` and is not marked (the guard: a gate that
  refuses everything would pass the refusal tests);
* the real ``build-iso.sh``, run on a scratch repo up to its first network/ISO step,
  names the build ``<tag>-lab`` with ``--lab`` and ``<tag>`` without.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
ISO_DIR = REPO / "tools" / "iso"
BUILD_ISO = ISO_DIR / "build-iso.sh"
LAB_GATE = ISO_DIR / "initrd-lab-gate.sh"

LAB_FILES = {
    "dropbear": ("usr/sbin/dropbear",),
    "authorized_keys": ("etc/imagectl/authorized_keys",),
    "both": ("usr/sbin/dropbear", "etc/imagectl/authorized_keys"),
}


def initrd(tmp_path: Path, files: tuple[str, ...], comp: str) -> Path:
    root = tmp_path / f"root-{comp}"
    for rel in ("init", "lib/modules/6.12.0/modules.dep", *files):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
    out = tmp_path / f"initrd-{comp}.img"
    pack = {"gzip": "gzip -n", "zstd": "zstd -q -c"}[comp]
    subprocess.run(["bash", "-c", f'find . -print | cpio -o -H newc 2>/dev/null | {pack} > "$1"', "_", str(out)],
                   cwd=root, check=True, stdin=subprocess.DEVNULL)
    return out


def gate(img: Path, *flag: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(LAB_GATE), str(img), *flag], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


@requires_native("bash", "cpio", "gzip", "zstd")
@pytest.mark.parametrize("comp", ["gzip", "zstd"])
@pytest.mark.parametrize("what", sorted(LAB_FILES))
def test_lab_access_without_the_flag_is_refused_and_with_it_marked(tmp_path, what, comp):
    img = initrd(tmp_path, LAB_FILES[what], comp)
    refused = gate(img)
    assert refused.returncode != 0, (refused.stdout, refused.stderr)
    assert "--lab" in refused.stderr, refused.stderr
    allowed = gate(img, "--lab")
    assert allowed.returncode == 0 and allowed.stdout.strip() == "true", (allowed.stdout, allowed.stderr)


@requires_native("bash", "cpio", "gzip", "zstd")
@pytest.mark.parametrize("comp", ["gzip", "zstd"])
def test_a_clean_initrd_passes_unmarked(tmp_path, comp):
    img = initrd(tmp_path, (), comp)
    clean = gate(img)
    assert clean.returncode == 0 and clean.stdout.strip() == "false", (clean.stdout, clean.stderr)


# --- the name: the real build-iso.sh up to its first ISO step -------------------------

def _build_iso_tag(tmp_path: Path, lab: bool) -> str:
    """Runs build-iso.sh on a scratch --source repo tagged v9.9.9, with a netinst that
    does not exist: it prints its "קוד: <tag> (...)" line and stops there, before any
    download or xorriso. Tools it only checks for are stubbed when absent."""
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
    for tool in ("xorriso", "dpkg-scanpackages", "dpkg-deb", "openssl"):
        if not shutil.which(tool):
            (stubs / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (stubs / tool).chmod(0o755)
    args = ["bash", str(BUILD_ISO), "--out", str(tmp_path / "out"),   # no root password: #1632
            "--source", str(src), "--live-initrd", str(initrd(tmp_path, (), "gzip")),
            "--netinst", str(tmp_path / "no-such-netinst.iso")] + (["--lab"] if lab else [])
    run = subprocess.run(args, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                         env={"PATH": f"{stubs}:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(tmp_path)})
    assert run.returncode != 0 and "no-such-netinst.iso" in run.stderr, (run.stdout, run.stderr)   # stopped where we meant
    line = next((l for l in run.stdout.splitlines() if l.startswith("build-iso: קוד: ")), None)
    assert line, (run.stdout, run.stderr)
    return line.split("קוד: ", 1)[1].split(" (", 1)[0]


@requires_native("bash", "git", "cpio", "gzip", "zstd", "python3", "sha256sum", "tar", posix=True)
def test_with_lab_the_build_is_named_lab_and_without_it_is_not(tmp_path):
    assert _build_iso_tag(tmp_path / "lab", lab=True) == "v9.9.9-lab"
    assert _build_iso_tag(tmp_path / "prod", lab=False) == "v9.9.9"
