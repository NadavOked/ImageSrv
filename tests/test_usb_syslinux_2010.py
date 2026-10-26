"""#2010 -> #2186 -- the syslinux stick (USB_BIOS_LOADER=syslinux): --stupid only when asked.

The field, 22/10 (HP 8000 Elite CMT, BIOS 786G7 v01.02): after the BIOS went from IDE to
AHCI the stick hung right after "SYSLINUX 6.04 EDD ..."; in IDE it booted and cloned.
syslinux(1): --stupid "only read one sector at a time". It was the default from 22/10 and
made the boot slow, without being shown to cure the hang; Nadav 25/10: "it has to be fast"
(#2186). The default is now without it, and USB_SYSLINUX_OPTS=--stupid builds it, to measure.
The stick's legacy BIOS boot is GRUB now (tests/test_usb_bios_grub_2186.py).
"""

from __future__ import annotations

import re
import subprocess

from test_cloner_gui import BASH
from test_usb_clone import REPO

SCRIPT = REPO / "tools" / "build_usb_clone.sh"


def opts(env: str) -> str:
    """SYSLINUX_OPTS as the build script sets it, under <env>."""
    line = next((l for l in SCRIPT.read_text(encoding="utf-8").splitlines() if l.startswith("SYSLINUX_OPTS=")),
                "SYSLINUX_OPTS=")   # none: no options
    out = subprocess.run([BASH, "-c", f'{env} {line}; printf "[%s]" "$SYSLINUX_OPTS"'], capture_output=True, text=True,
                         timeout=30, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_syslinux_is_not_stupid_by_default():
    assert opts("unset USB_SYSLINUX_OPTS;") == "[]"                    # #2186: fast
    assert opts("USB_SYSLINUX_OPTS=--stupid;") == "[--stupid]"         # the measure with it


def test_the_install_takes_the_options():
    text = SCRIPT.read_text(encoding="utf-8")
    installs = re.findall(r"^\s*syslinux --install .*$", text, re.M)
    assert installs and all("$SYSLINUX_OPTS" in i for i in installs), installs
