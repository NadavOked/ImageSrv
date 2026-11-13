"""#2382: the SMB share scan in storage locations runs ``smbclient -L``, and
smbclient was in no install list -- cifs-utils only *Suggests* it, so it was
missing on every installed server even before #1987. The scan answered
"smbclient לא נמצא במערכת" (visible, principle 5 kept), never a share list.

A command the server code runs is a package the server installs: in PKGS of
deploy/setup-boot-server.sh, and in the ISO's offline pool list (the
installer from the ISO has no internet).
"""
from __future__ import annotations

import re
from pathlib import Path

from test_installer_packages import installer_pkgs

REPO = Path(__file__).resolve().parent.parent
POOL = REPO / "tools" / "iso" / "packages.txt"


def _pool() -> set[str]:
    return {line.split("#", 1)[0].strip() for line in POOL.read_text(encoding="utf-8").splitlines()} - {""}


def _server_runs_smbclient() -> list[str]:
    hits = []
    for path in sorted((REPO / "server").rglob("*.py")):
        if re.search(r'\[\s*"smbclient"', path.read_text(encoding="utf-8")):
            hits.append(path.relative_to(REPO).as_posix())
    return hits


def test_the_server_still_runs_smbclient():
    """The guard below means something only while the scan runs it."""
    assert "server/storage/locations.py" in _server_runs_smbclient()


def test_smbclient_is_installed_by_the_installer():
    assert "smbclient" in installer_pkgs()


def test_smbclient_is_in_the_iso_pool():
    assert "smbclient" in _pool()
