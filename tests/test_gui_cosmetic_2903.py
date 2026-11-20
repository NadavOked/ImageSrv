"""#2903 (the native GUI's part of the cosmetic audit of 11/11): one case for the MAC on a screen, no link to an empty
SMART table, and a partition card that says its line whole instead of "… בשימוש מתוך"."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from test_cloner_gui import GUI, _build_gui, posix
from test_native_smart_table_1859 import DISKS, GUI_BUILD, HEAD, render, state


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui2903"))


@GUI_BUILD
def test_a_drawer_with_no_attributes_is_not_a_link_to_an_empty_table(binary, tmp_path):
    with_rows = render(binary, tmp_path, state())["standby-light"]
    assert any("כל מאפייני ה-SMART (21)" in l for l in with_rows), "the link of a disk with a table stays"
    empty = render(binary, tmp_path, HEAD + DISKS + "csmart=sdb|0|0\n")["standby-light"]
    assert not [l for l in empty if "כל מאפייני ה-SMART" in l], "the agent wrote 0 attributes: no link to nothing"


@GUI_BUILD
def test_the_mac_is_in_capitals_whatever_case_it_arrives_in(binary, tmp_path):
    sf = tmp_path / "state.txt"; sf.write_text(HEAD + DISKS, encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"; log.write_text("")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=posix(log))
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "p"), "--size", "1024x768", "--mac", "3c:52:82:a1:00:1f",
                          "--ip", "10.10.10.31", "--state", posix(sf)],
                         capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    text = log.read_text(encoding="utf-8")
    assert "3C:52:82:A1:00:1F" in text and "3c:52:82:a1:00:1f" not in text


@GUI_BUILD
def test_a_narrow_partition_card_says_its_short_line_whole(binary, tmp_path):
    # the sample of the local clone, at 1024 wide: four partitions in a row, each card ~180 px
    sf = tmp_path / "state.txt"; sf.write_text("message=x|y\n", encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"; log.write_text("")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=posix(log))
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "p"), "--size", "1024x768", "--mac", "3C:52:82:A1:00:1F",
                          "--ip", "10.10.10.31"], capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    lines = set(re.sub("[\u200e\u200f\u2066-\u2069]", "", l) for l in log.read_text(encoding="utf-8").splitlines())
    assert "ללא מ״ק · 17 MB · מועתקת מלאה" in lines            # the long line was made first ...
    assert "17 MB · מועתקת מלאה" in lines                      # ... and, as it did not fit, the whole short one after it
    assert "NTFS · 610 MB מתוך 650 MB" in lines
