"""#4011 -- a binary packed under /usr/bin whose host twin lives in /usr/sbin
(Debian 13: partclone, sgdisk) must still bring its package copyright.

The old license pass did `[ -e "$_host_file" ] || continue` on the PACKED path,
so those binaries were skipped silently and the initrd shipped GPL code without
its notice. The test runs the real license block from the builder on a fake
tree: the packed file is in usr/bin, its recorded source is in usr/sbin, and a
stubbed dpkg-query says an owner package with no copyright file owns that
source. The build must stop and name the package.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BUILDER = REPO / "tools" / "build_initramfs.sh"
BASH = shutil.which("bash")
OWNER = "zz-imagesrv-4011-pkg"

pytestmark = requires_native("bash", why="the builder blocks are bash")


def bash_path(path: Path) -> str:
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _blocks() -> tuple[str, str]:
    text = BUILDER.read_text(encoding="utf-8")
    decl = text[text.index("declare -A PACKED_FROM"):text.index("copy_bin() {")]
    lic = text[text.index("mapfile -d '' -t PACKED_FILES"):text.index("# --- pack", text.index("# --- third-party licenses"))]
    return decl, lic


def _run(tmp: Path, record_source: bool) -> subprocess.CompletedProcess:
    root, host, stub = tmp / "root", tmp / "host", tmp / "stub"
    for d in (root / "usr/bin", host / "usr/sbin", stub):
        d.mkdir(parents=True)
    (root / "usr/bin/zz-lic4011-tool").write_text("x")
    (host / "usr/sbin/zz-lic4011-tool").write_text("x")
    src = bash_path(host / "usr/sbin/zz-lic4011-tool")
    dq = stub / "dpkg-query"
    dq.write_text(f'#!/bin/sh\n[ "$2" = "{src}" ] && {{ echo "{OWNER}: $2"; exit 0; }}\nexit 1\n',
                  newline="\n")
    dq.chmod(0o755)
    decl, lic = _blocks()
    record = f'PACKED_FROM["$ROOT/usr/bin/zz-lic4011-tool"]={src}\n' if record_source else ""
    script = (f'set -euo pipefail\nROOT={bash_path(root)}\nPATH={bash_path(stub)}:$PATH\n'
              + decl + record + lic + "echo LICENSES_DONE\n")
    f = tmp / "snippet.sh"
    f.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, bash_path(f)], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def test_usr_sbin_binary_packed_in_usr_bin_stops_the_build_naming_its_package(tmp_path: Path) -> None:
    r = _run(tmp_path, record_source=True)
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"package {OWNER} owns packed file" in r.stderr
    assert "LICENSES_DONE" not in r.stdout


def test_copy_bin_records_where_it_copied_from() -> None:
    text = BUILDER.read_text(encoding="utf-8")
    body = text[text.index("copy_bin() {"):text.index('for b in "${BINARIES[@]}"')]
    assert re.search(r'PACKED_FROM\["\$dst"\]=\$src', body)
