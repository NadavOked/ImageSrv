"""#2957: the live installer from the ISO had no /usr/share/zoneinfo.

The time step decides a zone is known by ``access("/usr/share/zoneinfo/<zone>")``
(native-gui/src/install.c); in the live image that failed, so the default
Asia/Jerusalem was "not known here", the manual-time boxes were UTC (06:41 for
08:41) and "השעה עכשיו בשרת" said UTC. ESXi, v0.92.0, 12/11 (rel09112); the
same in v0.91.11.

build_initramfs.sh --installer now copies the builder's tzdata into the image
and stops the build when a zone the engine accepts (installer/lib/zones.txt) is
not there -- a missing zone is found at build time, not in front of a server.
The installer block is run here against a fake builder: the real script's
lines, with /usr/share/zoneinfo pointed at a directory the test controls.
That a built ISO has the file is checked on the ISO (see the PR).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build_initramfs.sh"
ZONES = REPO / "installer" / "lib" / "zones.txt"
POOL = REPO / "tools" / "iso" / "packages.txt"
BASH = shutil.which("bash")

pytestmark = requires_native(("bash", BASH), posix=True, why="build_initramfs.sh is bash on Linux")


def _zoneinfo_block() -> str:
    text = BUILD.read_text(encoding="utf-8")
    start = text.index("    # #2957: the GUI's time step")
    end = text.index('done < "$INSTALLER_SRC/lib/zones.txt"', start) + len('done < "$INSTALLER_SRC/lib/zones.txt"')
    return text[start:end]


def _run(tmp_path: Path, zones: list[str]) -> subprocess.CompletedProcess:
    builder = tmp_path / "builder-zoneinfo"
    for zone in zones:
        (builder / zone).parent.mkdir(parents=True, exist_ok=True)
        (builder / zone).write_bytes(b"TZif2\0fake")
    root = tmp_path / "root"
    script = "set -e\n" + _zoneinfo_block().replace("/usr/share/zoneinfo", "$BUILDER_ZI")
    script = script.replace('"$ROOT$BUILDER_ZI', '"$ROOT/usr/share/zoneinfo')
    env = {"PATH": "/usr/bin:/bin", "ROOT": str(root), "BUILDER_ZI": str(builder),
           "INSTALLER_SRC": str(REPO / "installer")}
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=env,
                          timeout=120, stdin=subprocess.DEVNULL)


def _accepted() -> list[str]:
    return [z for z in ZONES.read_text(encoding="utf-8").splitlines() if z and not z.startswith("#")]


def test_the_live_image_gets_every_zone_the_engine_accepts(tmp_path):
    got = _run(tmp_path, _accepted())
    assert got.returncode == 0, got.stderr
    assert (tmp_path / "root" / "usr" / "share" / "zoneinfo" / "Asia" / "Jerusalem").is_file()


def test_a_zone_missing_on_the_builder_stops_the_build(tmp_path):
    got = _run(tmp_path, [z for z in _accepted() if z != "Asia/Jerusalem"])
    assert got.returncode != 0
    assert "zone Asia/Jerusalem (zones.txt) is not in the live image's zoneinfo" in got.stderr


def test_tzdata_is_installed_on_the_builder_and_in_the_pool():
    text = BUILD.read_text(encoding="utf-8")
    base = text[text.index("apt-get install -y --no-install-recommends \\"):]
    base = base[:base.index("\n    #")]
    assert "tzdata" in base.split()
    pool = {line.split("#", 1)[0].strip() for line in POOL.read_text(encoding="utf-8").splitlines()}
    assert "tzdata" in pool


@pytest.mark.parametrize("zone", ["Asia/Jerusalem", "UTC"])
def test_the_zones_the_gui_defaults_to_are_accepted(zone):
    assert zone in _accepted()
