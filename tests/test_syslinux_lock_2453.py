"""#2453: in BIOS, the ISO (isolinux) and the clone stick (syslinux) let nobody edit the kernel line.

#2450 locked the GRUB menus of the ISO and the stick with no password: nobody
edits. In BIOS GRUB is not on that path. isolinux (the ISO) and syslinux (the
stick's default BIOS loader, #2186) gave the kernel line away three ways:

* Tab in the ISO's menu (vesamenu.c32) edits the entry;
* Esc in the top menu leaves it for the ``boot:`` prompt;
* Shift, Alt, Caps Lock or Scroll Lock held at start-up stop at ``boot:``
  even with ``PROMPT 0`` -- where ``imagesrv init=/bin/sh`` boots.

The cure is two global directives: ``ALLOWOPTIONS 0`` (no options but the
entry's APPEND; the menu takes Tab and the Esc-to-prompt away with it) and
``NOESCAPE 1`` (the start-up keys are ignored). The stick still boots straight
through: neither touches DEFAULT, PROMPT 0 or TIMEOUT 0, and NOESCAPE also
keeps a Caps Lock left on from stopping it at a prompt.

The ISO's menu.cfg is ours; isolinux.cfg is Debian's, read here as the
netinst 13.6.0 ships it (C:/Projects/ImageSrv/lab/iso-tools/other, 05/11).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ISO = REPO / "tools" / "iso"
USB = REPO / "tools" / "usb"
BUILD_ISO = ISO / "build-iso.sh"

# isolinux/isolinux.cfg of debian-13.6.0-amd64-netinst.iso, as it is.
DEBIAN_ISOLINUX_CFG = """# D-I config version 2.0
# search path for the c32 support libraries (libcom32, libutil etc.)
path
prompt 0
timeout 0
include menu.cfg
default vesamenu.c32
"""
# stdmenu.cfg there sets colours and rows only -- no directive that matters here.
DEBIAN_STDMENU_CFG = "menu background splash.png\nmenu tabmsg Press ENTER to boot or TAB to edit a menu entry\n"


def directives(files: dict[str, str], top: str) -> dict[str, str]:
    """The global directives syslinux ends up with: ``include`` read in place,
    keywords in any case, the last one wins. Two-word ``menu master passwd``
    is kept as ``menu master``. A ``menu begin`` block would scope what is in
    it -- our files have none, and the model refuses one."""
    found: dict[str, str] = {}

    def read(name: str) -> None:
        for raw in files[name].splitlines():
            words = raw.split()
            if not words or words[0].startswith("#"):
                continue
            key = words[0].lower()
            if key == "include":
                read(words[1])
                continue
            if key == "menu" and len(words) > 1:
                assert words[1].lower() != "begin", f"{name}: a submenu -- the model does not scope it"
                if words[1].lower() == "master":
                    found["menu master"] = " ".join(words[3:])
                continue
            found[key] = " ".join(words[1:])

    read(top)
    return found


def ways_to_edit(found: dict[str, str]) -> list[str]:
    """Every way a person at the machine reaches a kernel line they wrote.

    ``boot:`` counts whatever the core does with options there: ALLOWOPTIONS is
    the menu's switch for Tab and for Esc (com32/menu, ``allowedit``), and
    NOESCAPE is the core's for the start-up keys -- each one alone leaves a door."""
    menu = found.get("default", "").lower() in ("vesamenu.c32", "menu.c32") or "ui" in found
    allow = found.get("allowoptions", "1").strip() != "0"
    doors = []
    if menu and allow and "menu master" not in found:
        doors += ["Tab edits the menu entry", "Esc leaves the menu for boot:"]
    if found.get("noescape", "0").strip() != "1":
        doors.append("Shift/Alt at start-up stops at boot:")
    if found.get("prompt", "0").strip() != "0":
        doors.append("PROMPT shows boot:")
    return doors


def _iso_files() -> dict[str, str]:
    entry = (ISO / "imagesrv.cfg.in").read_text(encoding="utf-8").replace("@DI_ARGS@", "imagesrv.mode=installer")
    return {"isolinux.cfg": DEBIAN_ISOLINUX_CFG, "stdmenu.cfg": DEBIAN_STDMENU_CFG,
            "menu.cfg": (ISO / "isolinux-menu.cfg.in").read_text(encoding="utf-8"), "imagesrv.cfg": entry}


def test_the_iso_in_bios_gives_no_way_to_edit_the_kernel_line():
    found = directives(_iso_files(), "isolinux.cfg")
    assert found["default"] == "vesamenu.c32"            # the model reads the real menu path
    assert ways_to_edit(found) == [], found


def test_the_stick_in_bios_gives_no_way_to_edit_the_kernel_line():
    text = (USB / "syslinux.cfg.in").read_text(encoding="utf-8").replace("@VERSION@", "v0")
    found = directives({"syslinux.cfg": text}, "syslinux.cfg")
    assert ways_to_edit(found) == [], found


def test_the_stick_still_boots_straight_through():
    """#2453 must not break the old cloners' autoboot (BIOS, USB2)."""
    text = (USB / "syslinux.cfg.in").read_text(encoding="utf-8")
    found = directives({"syslinux.cfg": text}, "syslinux.cfg")
    assert (found["default"], found["prompt"], found["timeout"]) == ("imagesrv", "0", "0")
    labels = re.findall(r"^LABEL (\S+)$", text, re.MULTILINE)
    assert labels == ["imagesrv"], labels
    assert "menu master" not in found, "a password the stick has no one to ask for"


def test_the_iso_still_boots_its_one_entry_straight_through():
    found = directives(_iso_files(), "isolinux.cfg")
    assert (found["prompt"], found["timeout"]) == ("0", "1")   # menu.cfg's timeout 1 wins over Debian's 0
    assert "menu master" not in found, "the ISO is built before any server: no password to ask"


def test_the_iso_build_checks_the_menu_it_installs():
    """build-iso.sh dies unless isolinux/menu.cfg is exactly our template:
    the two must not drift, or every ISO build stops there."""
    build = BUILD_ISO.read_text(encoding="utf-8")
    m = re.search(r'\[\[ "\$\(cat "\$ISO_TREE/isolinux/menu\.cfg"\)" == \$\'(.*?)\' \]\]', build)
    assert m, "the menu.cfg guard in build-iso.sh was not found"
    expected = m.group(1).encode().decode("unicode_escape")
    assert (ISO / "isolinux-menu.cfg.in").read_text(encoding="utf-8").rstrip("\n") == expected
