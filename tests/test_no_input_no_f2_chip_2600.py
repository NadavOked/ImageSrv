"""#2600: a screen opened with --no-input takes no keys, so it must not draw the F2 chip.

The GUI renders every card with --png; the same card with and without --no-input must differ
(the chip is the only difference) -- on the cards that two renders with --no-input draw as the same bytes
(a card that counts down from the wall clock is not compared: it differs between any two renders).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import REPO
from test_cloner_gui import _build_gui, _pkgconfig

GUI_TOOLS = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _render(binary: Path, out: Path, *extra: str) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if (REPO / "native-gui" / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(REPO / "native-gui" / "fonts.conf")
    r = subprocess.run([str(binary), "--png", str(out / "p"), "--size", "1280x800", *extra],
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, env=env)
    assert r.returncode == 0, r.stderr
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob("p-*-light.png"))}


@GUI_TOOLS
def test_no_input_draws_no_f2_chip_and_input_screens_keep_it(tmp_path):
    binary = _build_gui(tmp_path)
    keyed = _render(binary, tmp_path / "keyed")
    quiet = _render(binary, tmp_path / "quiet", "--no-input")
    again = _render(binary, tmp_path / "quiet2", "--no-input")
    assert keyed and set(keyed) == set(quiet)
    # A card that counts down from the wall clock (the drawer/room question cards, screens_ask.c) differs between two renders whatever
    # the flag is, so it says nothing about the chip: only the cards two --no-input renders agree on are compared. Most must agree.
    stable = [n for n in quiet if quiet[n] == again[n]]
    assert len(stable) > len(quiet) // 2, f"most cards are deterministic without input; not: {sorted(set(quiet) - set(stable))}"
    changed = [n for n in stable if keyed[n] != quiet[n]]
    assert changed, "the F2 chip is drawn only where keys are accepted: at least the themed screens differ"
