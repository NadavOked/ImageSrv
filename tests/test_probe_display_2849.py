"""#2849: the screen the GUI draws on, in the hello's probe.

The work's cloner sat behind a KVM switch (no EDID, a fallback mode) and
nothing on the server said what resolution it had. ``probe_display`` reads
fb0's driver and size and each DRM connector's status, EDID size and first
mode -- from a fake sysfs here, never the host's.
"""

from __future__ import annotations

import json
from pathlib import Path

import sizelimit
from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

LIBS = (f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/hw/probe.sh; . {posix(AGENT)}/lib/hw/probedisplay.sh; ")


def display(root: Path) -> object:
    return json.loads(sh(f"export SYSROOT={posix(root)!r}; " + LIBS + "probe_display"))


def kvm_machine(root: Path) -> None:
    fb = root / "sys/class/graphics/fb0"; fb.mkdir(parents=True)
    (fb / "name").write_text("i915drmfb\n"); (fb / "virtual_size").write_text("800,600\n")
    vga = root / "sys/class/drm/card0-VGA-1"; vga.mkdir(parents=True)
    (vga / "status").write_text("connected\n"); (vga / "edid").write_bytes(b"")
    (vga / "modes").write_text("1024x768\n800x600\n640x480\n")
    dp = root / "sys/class/drm/card0-DP-1"; dp.mkdir(parents=True)
    (dp / "status").write_text("disconnected\n"); (dp / "edid").write_bytes(b"\0" * 128)
    (dp / "modes").write_text("")


def test_a_kvm_switch_reads_as_no_edid_and_the_fallback_size(tmp_path):
    kvm_machine(tmp_path)
    got = display(tmp_path)
    assert got["fb"] == "i915drmfb" and got["size"] == "800x600"
    by = {c["name"]: c for c in got["connectors"]}
    assert by["card0-VGA-1"] == {"name": "card0-VGA-1", "status": "connected", "edid_bytes": 0, "mode": "1024x768"}
    assert by["card0-DP-1"]["edid_bytes"] == 128 and by["card0-DP-1"]["mode"] is None


def test_no_framebuffer_and_no_connector_is_null_not_a_guess(tmp_path):
    (tmp_path / "sys").mkdir()
    assert display(tmp_path) is None


def test_the_hello_probe_carries_it_and_the_server_keeps_it(tmp_path):
    kvm_machine(tmp_path)
    out = sh(f"export SYSROOT={posix(tmp_path)!r}; " + LIBS + r'printf "{\"x\":0%s}" "$(_probe_fresh)"')
    assert json.loads(out)["probe"]["display"]["size"] == "800x600"
    from server.agent import probe
    assert "display" in probe.KNOWN_KEYS
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/probedisplay.sh"' in text
    sizelimit.assert_within_limit(AGENT / "lib" / "hw" / "probedisplay.sh")
