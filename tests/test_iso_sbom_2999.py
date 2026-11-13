"""#2999 -- tools/iso/sbom.sh: one SBOM per ISO, tied to that ISO's sha256.

The real tool runs on a small real ISO (xorriso builds it here), against a fake
`syft` that writes whatever the case needs. The real syft 1.55.0 was run on the
v0.93.0 ISO on the testrunner (see the PR); a fake is the only way to make
syft misbehave on purpose, which is what most cases below are about.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH

ROOT = Path(__file__).resolve().parents[1]
SBOM = ROOT / "tools" / "iso" / "sbom.sh"

pytestmark = requires_native("xorriso", "cpio", "gzip", ("bash", BASH), posix=True)

N_DEBS = 10


def _cpio(entries: dict[str, bytes], tmp: Path) -> bytes:
    stage = tmp / "initrd-stage"
    for rel, data in entries.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_bytes(data)
    names = "\n".join(entries).encode()
    return subprocess.run(["cpio", "-o", "-H", "newc", "--quiet"], cwd=stage, input=names,
                          capture_output=True, check=True).stdout


def make_iso(tmp: Path, debs: int = N_DEBS) -> Path:
    tree = tmp / "isotree"
    (tree / "pool" / "main" / "imagesrv").mkdir(parents=True)
    (tree / "live").mkdir()
    for i in range(debs):
        (tree / "pool" / "main" / "imagesrv" / f"pkg{i}_1.0_amd64.deb").write_bytes(b"!<arch>\n" + bytes([i]))
    (tree / "live" / "initrd.img").write_bytes(gzip.compress(_cpio({"bin/busybox": b"x" * 64}, tmp)))
    iso = tmp / "imagesrv-v9.9.9-amd64.iso"
    subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(iso), str(tree)],
                   capture_output=True, check=True, stdin=subprocess.DEVNULL)
    (tmp / (iso.name + ".sha256")).write_text(
        hashlib.sha256(iso.read_bytes()).hexdigest() + "  " + iso.name + "\n", encoding="utf-8")
    return iso


FAKE_SYFT = r"""#!/bin/sh
case "$1" in
  version) echo "Application: syft"; echo "Version:       ${FAKE_SYFT_VERSION:-1.55.0}"; exit 0 ;;
esac
[ "$FAKE_SYFT_MODE" = fail ] && { echo "boom" >&2; exit 3; }
spdx=""; cdx=""
for a in "$@"; do
  case "$a" in
    spdx-json=*) spdx=${a#spdx-json=} ;;
    cyclonedx-json=*) cdx=${a#cyclonedx-json=} ;;
  esac
done
n=${FAKE_SYFT_DEBS:-10}
comps=""; i=0
while [ $i -lt "$n" ]; do comps="$comps{\"name\":\"pkg$i\",\"purl\":\"pkg:deb/debian/pkg$i@1.0\"},"; i=$((i+1)); done
comps="${comps%,}"
if [ "$FAKE_SYFT_MODE" = empty ]; then
  echo '{"spdxVersion":"SPDX-2.3","packages":[]}' > "$spdx"
  echo '{"bomFormat":"CycloneDX","components":[]}' > "$cdx"
else
  echo '{"spdxVersion":"SPDX-2.3","packages":[{"name":"x"}]}' > "$spdx"
  echo "{\"bomFormat\":\"CycloneDX\",\"components\":[$comps]}" > "$cdx"
fi
exit 0
"""


def run(tmp: Path, iso: Path, **env_extra: str) -> subprocess.CompletedProcess:
    syft = tmp / "syft"
    syft.write_bytes(FAKE_SYFT.encode())
    syft.chmod(0o755)
    env = dict(os.environ, IMAGESRV_SYFT=syft.as_posix(), TMPDIR=str(tmp), **env_extra)
    return subprocess.run([BASH, SBOM.as_posix(), "--iso", str(iso), "--out", str(tmp / "out")],
                          env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)


def test_an_sbom_is_written_next_to_a_record_of_the_isos_sha256(tmp_path):
    iso = make_iso(tmp_path)
    r = run(tmp_path, iso)
    assert r.returncode == 0, r.stdout + r.stderr
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        "imagesrv-v9.9.9-amd64.cdx.json", "imagesrv-v9.9.9-amd64.sbom.txt", "imagesrv-v9.9.9-amd64.spdx.json"]
    record = (out / "imagesrv-v9.9.9-amd64.sbom.txt").read_text(encoding="utf-8")
    assert f"iso_sha256: {hashlib.sha256(iso.read_bytes()).hexdigest()}" in record
    assert f"deb_files_on_iso: {N_DEBS}" in record and "syft: 1.55.0" in record
    json.loads((out / "imagesrv-v9.9.9-amd64.cdx.json").read_text(encoding="utf-8"))


def test_the_live_initrd_is_unpacked_and_handed_to_syft(tmp_path):
    """syft must be pointed at a tree that holds both the ISO and the unpacked initrd."""
    iso = make_iso(tmp_path)
    log = tmp_path / "syft-args.log"
    wrapper = tmp_path / "syft-real"
    wrapper.write_bytes(FAKE_SYFT.replace('n=${FAKE_SYFT_DEBS:-10}', f'echo "$@" > {log.as_posix()}; ls "${{2#dir:}}/live-initrd/bin" >> {log.as_posix()}\nn=${{FAKE_SYFT_DEBS:-10}}').encode())
    wrapper.chmod(0o755)
    r = subprocess.run([BASH, SBOM.as_posix(), "--iso", str(iso), "--out", str(tmp_path / "out")],
                       env=dict(os.environ, IMAGESRV_SYFT=wrapper.as_posix(), TMPDIR=str(tmp_path)),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    seen = log.read_text(encoding="utf-8")
    assert "dir:" in seen and "busybox" in seen, seen


def test_an_iso_whose_hash_is_not_the_recorded_one_gets_no_sbom(tmp_path):
    """The SBOM would describe a file nobody tested."""
    iso = make_iso(tmp_path)
    (tmp_path / (iso.name + ".sha256")).write_text("0" * 64 + "  " + iso.name + "\n", encoding="utf-8")
    r = run(tmp_path, iso)
    assert r.returncode == 1 and "this is not the ISO that was tested" in r.stderr, r.stderr
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())


@pytest.mark.parametrize("env,message", [
    ({"FAKE_SYFT_MODE": "fail"}, "syft failed"),
    ({"FAKE_SYFT_MODE": "empty"}, "empty SBOM"),
    ({"FAKE_SYFT_DEBS": "2"}, "deb components for"),
    ({"FAKE_SYFT_VERSION": "1.54.1"}, "syft is '1.54.1'"),
], ids=["syft-failed", "empty-sbom", "too-few-debs", "other-syft-version"])
def test_an_sbom_that_does_not_describe_the_iso_is_a_refusal_not_a_file(tmp_path, env, message):
    iso = make_iso(tmp_path)
    r = run(tmp_path, iso, **env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert message in r.stderr, r.stderr
    assert not (tmp_path / "out" / "imagesrv-v9.9.9-amd64.sbom.txt").exists(), "a record was written for a failed run"


def test_a_missing_syft_is_a_refusal(tmp_path):
    iso = make_iso(tmp_path)
    r = subprocess.run([BASH, SBOM.as_posix(), "--iso", str(iso), "--out", str(tmp_path / "out")],
                       env=dict(os.environ, IMAGESRV_SYFT=(tmp_path / "no-such-syft").as_posix()),
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 1 and "syft is not installed" in r.stderr, r.stderr


def test_the_tool_never_swallows_a_failure():
    for no, line in enumerate(SBOM.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for bad in ("|| true", "set +e"):
            assert bad not in line, f"sbom.sh:{no} {bad}"
