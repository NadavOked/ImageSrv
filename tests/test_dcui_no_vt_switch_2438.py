"""#2438: Alt+Fn on the server screen switched to tty2-6 and an OS login.

#1362 made the graphical DCUI ask the kernel for the VT switch itself
(`VT_ACTIVATE`): its keyboard grab hides Alt+Fn from the kernel, and the
switch was meant as a way out. On a server with SSH closed (#1266) that way
out is the one local login surface -- anyone at the keyboard got a getty and
could guess local passwords.

The fix, and what is kept:

* **The graphical screen ignores Alt+Fn.** The chord still never reaches the
  screen as an F key (F2 is the settings dialog -- #1362), and the GUI no
  longer asks for any switch. The only `VT_ACTIVATE` left brings the
  screen's own tty1 to the front before the modeset.
* **The recovery entry keeps its login.** The server's GRUB has
  "ImageSrv recovery (text console)" (installer/lib/finish.sh) -- the
  technician's deliberate way in. Booted from it, the DCUI shows the text
  screen, which grabs nothing: Alt+F2 is the kernel's, and tty2 has its getty
  as before. The DCUI tells that boot by the entry's own kernel line.

The switch itself (signals, DRM master, fbcon) is a VM/metal check -- see the
PR. Here: what the source asks the kernel for, and which screen each boot gets.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from server.dcui import app as dcui_app
from server.dcui import gui as dcui_gui

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
FINISH = REPO / "installer" / "lib" / "finish.sh"


def _vt_branch(dcui_c: str) -> str:
    """The body of the main loop's `if (e.key == KEYSYM_VT ...)` branch."""
    start = dcui_c.index("e.key == KEYSYM_VT")
    end = dcui_c.index("continue;", start)
    return dcui_c[start:end]


def test_alt_fn_on_the_graphical_screen_asks_the_kernel_for_nothing():
    branch = _vt_branch((SRC / "dcui.c").read_text(encoding="utf-8"))
    assert "activate" not in branch.lower(), branch
    assert "ioctl" not in branch and "chvt" not in branch, branch


def test_the_only_vt_switch_left_brings_the_screen_s_own_tty_forward():
    """vt_front(): tty1 to the front before the modeset (#1362) -- the one switch kept."""
    calls = {}
    for path in sorted(SRC.glob("*.[ch]")):
        hits = re.findall(r"\bVT_ACTIVATE\b|\bbackend_vt_activate\b", path.read_text(encoding="utf-8"))
        if hits:
            calls[path.name] = hits
    assert calls == {"dcui.c": ["VT_ACTIVATE"]}, calls
    dcui_c = (SRC / "dcui.c").read_text(encoding="utf-8")
    front = dcui_c[dcui_c.index("static int vt_front("):dcui_c.index("int dcui_main(")]
    assert "VT_ACTIVATE" in front


def test_alt_fn_still_never_reaches_the_screen_as_an_f_key():
    """input.c keeps turning Alt+Fn into KEYSYM_VT: Alt+F2 is not F2 (settings)."""
    input_c = (SRC / "input.c").read_text(encoding="utf-8")
    assert "e.key = KEYSYM_VT; e.vt = fn;" in input_c
    # ...and dcui.c drops it before the token printer, not after
    dcui_c = (SRC / "dcui.c").read_text(encoding="utf-8")
    assert dcui_c.index("e.key == KEYSYM_VT") < dcui_c.index("dcui_token(&e, e.caps")


# ---- which screen each boot gets ---------------------------------------------------------


def _recovery_cmdline() -> str:
    """The kernel line of the recovery entry, as finish.sh writes it."""
    text = FINISH.read_text(encoding="utf-8")
    entry = text[text.index('menuentry "ImageSrv recovery (text console)"'):]
    line = next(row for row in entry.splitlines() if row.strip().startswith("linux "))
    return " ".join(line.split()[1:]).replace("$root_uuid", "1234-ABCD")


NORMAL = "BOOT_IMAGE=/vmlinuz-6.12.43+deb13-amd64 root=UUID=1234-ABCD ro quiet"


@pytest.fixture()
def screens(monkeypatch):
    """What run() picked: the GUI attempt, and the text screen's reason. A
    stand-in curses: Windows has none, and the real one would take the tty."""
    seen: dict = {"gui": 0}

    def fake_gui(data_dir):
        seen["gui"] += 1
        return "gui stopped"

    curses = types.ModuleType("curses")
    curses.wrapper = lambda fn, data_dir, why: seen.update(why=why)
    monkeypatch.setitem(sys.modules, "curses", curses)
    monkeypatch.setattr(dcui_gui, "run", fake_gui)
    return seen


def _boot(tmp_path, monkeypatch, line: str | None) -> None:
    """run() as systemd starts it, on a machine whose /proc/cmdline is `line`."""
    cmdline = tmp_path / "cmdline"
    if line is not None:
        cmdline.write_text(line + "\n", encoding="ascii")
    # raising=False: the negative control runs this against the code before
    # #2438, which has no CMDLINE -- it must fail on the screen it picks.
    monkeypatch.setattr(dcui_app, "CMDLINE", cmdline, raising=False)
    dcui_app.run(tmp_path)


def test_the_recovery_entry_gets_the_text_screen_so_alt_f2_reaches_tty2(tmp_path, monkeypatch, screens):
    _boot(tmp_path, monkeypatch, _recovery_cmdline())
    assert screens["gui"] == 0
    assert "recovery" in screens["why"] and "#2438" in screens["why"]


@pytest.mark.parametrize("line", [
    NORMAL,
    NORMAL + " nomodeset",                              # a driver workaround is not recovery
    NORMAL + " systemd.unit=multi-user.target",         # nor a target on its own
    NORMAL + " xsystemd.unit=multi-user.target nomodeset",
])
def test_every_other_boot_gets_the_graphical_screen(tmp_path, monkeypatch, screens, line):
    _boot(tmp_path, monkeypatch, line)
    assert screens["gui"] == 1 and screens["why"] == "gui stopped"


def test_an_unreadable_cmdline_is_a_normal_boot_and_said(tmp_path, monkeypatch, screens, capsys):
    _boot(tmp_path, monkeypatch, None)
    assert screens["gui"] == 1
    assert "cmdline" in capsys.readouterr().err


def test_the_text_flag_still_wins(tmp_path, screens):
    dcui_app.run(tmp_path, text_only=True)
    assert screens["gui"] == 0 and "--text" in screens["why"]
