"""#2677 -- a question row names the disk by its drawer, as the cards do, not by its wire port.

Metal 07/11 (cloner 2, v0.88.0, room_82426049): drawer_slots = sata:1,2,3,5, so port 5 is
drawer 4. The split window said "דיסק 5 · מחיצה 2", the title behind it "ממתין להחלטה על
דיסק 4", and the card was numbered 4 -- the operator was asked about a disk the screen does not
have. The row now takes the card's name (cloner_slot_label of the drawer of that port); a row
whose port is not a drawer keeps "דיסק <port>". The real state parser and the real
draw_ask_dialog run (the driver of test_share_words_1898), and the test reads what is drawn.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_share_words_1898 import BASE, driver, drawn, posix, pytestmark  # noqa: F401  (fixture + marks)

#: cloner 2's last drawer: port 5, drawer 4 (port|dev|state|bytes|total|error|rate|eta|drawer|conn)
DRAWER_5 = "drawer=5|sda|waiting|0|500107862016||-1|-1|4|SATA 4\n"


def draw(driver: Path, tmp_path: Path, text: str) -> list[str]:
    state = tmp_path / "state"
    state.write_text(BASE + text, encoding="utf-8", newline="\n")
    out = subprocess.run([str(driver), posix(state)], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(
                             Path(__file__).resolve().parent.parent / "native-gui" / "fonts.conf")))
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


def test_the_split_row_names_the_drawer_not_the_port(driver, tmp_path):
    out = draw(driver, tmp_path, DRAWER_5 + "ask_prompt=7-0|share|1|sda\nask_row=5|2|239868342272|530297487360\n")
    assert out[0] == "pending=1", out
    assert drawn(out, "דיסק 4 · מחיצה 2"), out
    assert not drawn(out, "דיסק 5"), out


def test_the_raid_title_names_the_drawer_not_the_port(driver, tmp_path):
    out = draw(driver, tmp_path, DRAWER_5 + "ask_prompt=7-0|raid|1|sda\nask_row=5|isw_raid_member\n")
    assert drawn(out, "דיסק 4: נמצא על הדיסק מידע של מערך RAID של Intel"), out
    assert not drawn(out, "דיסק 5"), out


def test_a_port_that_is_no_drawer_keeps_its_number(driver, tmp_path):
    out = draw(driver, tmp_path, "ask_prompt=7-0|share|1|sda\nask_row=3|2|21474836480|166429982720\n")
    assert drawn(out, "דיסק 3 · מחיצה 2"), out
