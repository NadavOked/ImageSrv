"""#3756 -- metal 21/11 (`892e891fc`), cloner 2: a typed `reboot` over ssh exited 0 and did nothing.

Same boot_id, uptime 220 s three minutes later, ping never dropped. PID 1 is `/bin/sh /init`; busybox `reboot` and `poweroff`
WITHOUT `-f` only signal PID 1, the signal is dropped, the exit code is 0 (#1483 had found the same for the agent's own
`poweroff`, so every call the agent makes says `-f`). What was left was the hand on the keyboard: ssh, the lab.

The fix is the file the initramfs installs as `/bin/reboot` and `/bin/poweroff` (`agent/imagesrv-power`):
  * `-f` among the arguments: the caller meant it (agent/init, common.sh, monitor.c) -- straight to busybox;
  * `reboot`: sync, then busybox `reboot -f`;
  * `poweroff`: `safe_poweroff` -- the agent's own path, Wake-on-LAN armed first (a plain `poweroff -f` skips it and the machine
    does not wake, #587); a Wake-on-LAN that cannot be armed means nothing is powered off, and it says so;
  * anything that returns is a failure: rc != 0, never "it went down".

Behavioural: the real script, with busybox / sync / ethtool replaced by recorders on PATH -- what busybox was asked is the
positive evidence (the machine itself is the lab's check: a changed boot_id, or no answer).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

pytestmark = requires_native("sh", "bash", why="the initramfs scripts are POSIX sh")

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "agent" / "imagesrv-power"
COMMON = REPO / "agent" / "lib" / "core" / "common.sh"
BUILDER = REPO / "tools" / "build_initramfs.sh"


class Box:
    """A directory standing in for the initramfs: recorders first on PATH, a copy of the wrapper under each name."""

    def __init__(self, tmp: Path, nic: bool = True):
        self.tmp = tmp
        self.stubs = tmp / "stubs"
        self.stubs.mkdir()
        self.log = tmp / "calls.log"
        self.log.touch()
        lib = tmp / "lib"
        lib.mkdir()
        shutil.copy(COMMON, lib / "common.sh")
        (tmp / "run").mkdir()
        if nic:
            (tmp / "sys" / "class" / "net" / "eth0").mkdir(parents=True)
        self._tool("busybox", 'echo "busybox $*" >> "$LOGF"')
        self._tool("sync", 'echo sync >> "$LOGF"')
        # arm_wol (common.sh) reads capabilities, sets `wol g`, reads it back: a NIC that takes it.
        self._tool("ethtool", '[ "$1" = -s ] && exit 0; printf "Settings for %s:\\n\\tSupports Wake-on: pumbg\\n\\tWake-on: g\\n" "$1"')
        for name in ("reboot", "poweroff"):   # /bin/reboot, /bin/poweroff of the initramfs: the same file under two names
            shutil.copy(WRAPPER, self.stubs / name)
            (self.stubs / name).chmod(0o755)

    def _tool(self, name: str, body: str) -> None:
        path = self.stubs / name
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def run(self, name: str, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": f"{self.stubs}:/usr/bin:/bin", "LOGF": str(self.log), "IMAGESRV_BUSYBOX": str(self.stubs / "busybox"),
               "IMAGESRV_LIB": str(self.tmp / "lib"), "RUN_DIR": str(self.tmp / "run"), "SYSROOT": str(self.tmp),
               "IMAGESRV_TEST": "0"}
        return subprocess.run(["sh", str(self.stubs / name), *args], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60, check=False)

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()


@pytest.fixture()
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def test_a_typed_reboot_is_forced_after_a_sync(box):
    """The 21/11 bug: `reboot` reached busybox without -f, and nothing happened. The fake busybox returns, as the real one did."""
    proc = box.run("reboot")
    assert box.calls() == ["sync", "busybox reboot -f"], (box.calls(), proc.stderr)
    assert proc.returncode != 0, "busybox came back: the machine is on, and rc 0 would say it went down"
    assert "still on" in proc.stderr


@pytest.mark.parametrize("name,args", [("reboot", ["-f"]), ("poweroff", ["-f"]), ("reboot", ["-nf"])])
def test_an_explicit_f_goes_straight_to_busybox(box, name, args):
    """agent/init, common.sh and monitor.c all say -f: no sync of ours, no Wake-on-LAN of ours, no second -f."""
    box.run(name, *args)
    assert box.calls() == [f"busybox {name} {' '.join(args)}"]


def test_a_typed_poweroff_arms_wake_on_lan_then_forces_it(box):
    """The agent's own path: `safe_poweroff` -> arm_wol, sync, `poweroff -f`, which is this same file with -f."""
    proc = box.run("poweroff")
    assert "powering off with wake-on-LAN armed" in proc.stdout, (proc.stdout, proc.stderr)
    assert box.calls() == ["sync", "busybox poweroff -f"], box.calls()
    assert proc.returncode != 0, "the (fake) busybox returned: the machine is on"


def test_a_typed_poweroff_that_cannot_arm_wake_on_lan_powers_nothing_off(tmp_path):
    """No NIC to arm: `safe_poweroff` refuses, and the wrapper does not fall back to a bare `poweroff -f` (a machine that
    cannot be woken is exactly what the operator is not told about)."""
    box = Box(tmp_path, nic=False)
    proc = box.run("poweroff")
    assert box.calls() == [], box.calls()
    assert proc.returncode != 0
    assert "still on" in proc.stderr


def _block(text: str, start: str, end: str) -> str:
    assert start in text, f"build_initramfs.sh has no line starting {start!r}"
    i = text.index(start)
    assert end in text[i:], f"build_initramfs.sh: {start!r} is never closed by {end!r}"
    return text[i:text.index(end, i) + len(end)]


def test_the_builder_installs_the_wrapper_over_the_busybox_links(tmp_path):
    """build_initramfs.sh links every busybox applet into /bin; the two names must be replaced AFTER that loop (`ln -sf` again
    would put busybox back). Both snippets are the builder's own lines, run in order."""
    text = BUILDER.read_text(encoding="utf-8")
    applets = _block(text, 'for applet in $("$BUSYBOX" --list); do', "\ndone\n")
    wrapper = _block(text, 'install -m 0755 "$AGENT_DIR/imagesrv-power"', 'do ln -sf ../usr/bin/imagesrv-power "$ROOT/bin/$_pw"; done')
    assert text.index(applets) < text.index(wrapper), "the applet links would overwrite the wrapper"

    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    fake_bb = tmp_path / "busybox"
    fake_bb.write_text("#!/bin/sh\nprintf 'busybox\\nreboot\\npoweroff\\nls\\n'\n", encoding="utf-8", newline="\n")
    fake_bb.chmod(0o755)
    script = f'set -eu\nROOT="{root}"\nBUSYBOX="{fake_bb}"\nAGENT_DIR="{REPO / "agent"}"\n' \
             f'ln -s "$BUSYBOX" "$ROOT/bin/busybox"\n{applets}\n{wrapper}\n'
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    installed = root / "usr" / "bin" / "imagesrv-power"
    assert installed.read_bytes() == WRAPPER.read_bytes()
    for name in ("reboot", "poweroff"):
        assert (root / "bin" / name).resolve() == installed.resolve(), f"/bin/{name} is still the busybox applet"
    assert (root / "bin" / "ls").resolve() == (root / "bin" / "busybox").resolve(), "the other applets stay busybox"


def test_the_wrapper_is_only_for_the_two_names_it_serves(box):
    """Linked under any other name it refuses by name instead of guessing (rc 2) -- and never reaches busybox."""
    shutil.copy(WRAPPER, box.stubs / "halt")
    proc = box.run("halt")
    assert proc.returncode == 2 and re.search(r"not reboot or poweroff: halt", proc.stderr), proc.stderr
    assert box.calls() == []
