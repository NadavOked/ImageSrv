"""#3738: the reason on a red drawer of the cloner's waiting screen does not run over the lines beside it.

A drawer's face has the name block at its start (name, sub line, the reason) and the size / model / "תפוס עכשיו" block at its end, with the tag
between them. The reason used to be laid out unbounded, so the write check's "בגל הקודם כתב 48MB/s לאורך כל הגל (18/11)..." ran on past the
tag over the end block -- over the model name on r39, over "תפוס עכשיו" on r40.

The binary logs where each string is drawn (``IMAGESRV_GUI_BOXLOG``, ``text_draw``); the test renders the screen and asserts that no two
strings of it share a pixel -- every line of the drawer, not one. Runs on the Testrunner only (cairo/pango, ``--png``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from disk_rules_state import with_rules
from test_cloner_gui import GUI, _build_gui
from test_native_local_clone import pytestmark as _native  # noqa: F401
from test_room_precheck_native_432_65 import _GUI, C1
from test_agent import posix

pytestmark = _native

SHORT = "בגל הקודם כתב 48MB/s"
# the agent's wtext is at most 160 bytes (state.c, IdleDisk.wtext) -- this is as long as one can arrive
LONG = "בגל הקודם כתב 48MB/s לאורך כל הגל (18/11), מתחת לסף 74 MB/s; הבדיקה עכשיו עברה, אבל הקצב נשאר נמוך"
BIDI_ISOLATES = dict.fromkeys(range(0x2066, 0x206A))

# two drawers: sda plain, sdb (the APS of r39/r40) red by the write check, "תפוס עכשיו" under its model; an F1 mark makes a second sub line
STATE = ("hello_age=4\nhello_rc=200\nmachine_name=CLONE-01\nserver_name=imagesrv-ta\n"
         "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0|\n"
         "cdisk=2|sdb|APS-SL3N-512|512110190592|passed||6.0|6.0|||||||||{words}|{wstate}|{wtext}\n"
         "cused=sdb|512110190592|83103481446\n"
         "message=x|y\n")


def _boxes(binary: Path, tmp_path: Path, state: str, size: str, name: str):
    st = tmp_path / f"{name}.txt"; st.write_text(with_rules(state), encoding="utf-8", newline="\n")
    log = tmp_path / f"{name}.boxes"; log.unlink(missing_ok=True)
    env = dict(os.environ, IMAGESRV_GUI_BOXLOG=str(log))
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(tmp_path / name), "--size", size, "--mac", C1, "--ip", "10.10.10.31", "--state", posix(st)],
                         capture_output=True, text=True, timeout=180, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    cards: dict[str, list] = {}
    cur = None
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@ "):
            cur = cards.setdefault(line[3:], [])
        elif cur is not None and "\t" in line:
            box, text = line.split("\t", 1)
            x0, y0, x1, y1 = (int(v) for v in box.split())
            cur.append((x0, y0, x1, y1, text))
    return cards


def _faces(boxes):
    """The strings of the drawers' faces: between the first and the last "מגירה" label (the number column's own two lines -- a 44px number and
    its label -- are one block whose line boxes touch at 640x480, and the status bar is another card of the screen; neither is a drawer's line)."""
    labels = [b for b in boxes if b[4] in ("מגירה", "לא ממופה")]
    if not labels:
        return []
    top, bottom = min(b[1] for b in labels) - 60, max(b[3] for b in labels) + 60
    return [b for b in boxes if b[1] >= top and b[3] <= bottom and not b[4].isdigit() and b[4] not in ("מגירה", "לא ממופה")]


def _overlaps(boxes):
    """Every pair of strings that share more than a pixel row and column (a one-pixel touch is a line box, not a collision)."""
    boxes = _faces(boxes)
    out = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if min(a[2], b[2]) - max(a[0], b[0]) > 1 and min(a[3], b[3]) - max(a[1], b[1]) > 1:
                out.append((a[4][:40], b[4][:40]))
    return out


@_GUI
@pytest.mark.parametrize("size", ["1280x800", "1024x768", "800x600", "640x480"])
@pytest.mark.parametrize("reason", [SHORT, LONG], ids=["short", "long"])
def test_no_two_lines_of_a_red_drawer_share_a_pixel(tmp_path, size, reason):
    binary = _build_gui(tmp_path)
    state = STATE.format(words="", wstate="slow", wtext=reason)
    cards = _boxes(binary, tmp_path, state, size, "s")
    standby = {k: v for k, v in cards.items() if k in ("standby-light", "standby-dark")}   # not "standby-smart": its table is another card
    assert standby, f"no waiting screen drawn: {sorted(cards)}"
    for card, boxes in standby.items():
        assert any(reason in b[4].translate(BIDI_ISOLATES) for b in boxes), (card, "the reason is not on the screen", [b[4][:30] for b in boxes])   # said in full, not dropped
        assert _overlaps(boxes) == [], (card, size, _overlaps(boxes))


@_GUI
def test_a_long_reason_wraps_inside_the_name_block_and_keeps_all_its_words(tmp_path):
    """On a roomy screen the reason is two lines or more (wrapped), and the log holds all of it: nothing was cut to make room."""
    binary = _build_gui(tmp_path)
    cards = _boxes(binary, tmp_path, STATE.format(words="", wstate="slow", wtext=LONG), "1280x800", "w")
    boxes = cards["standby-light"]
    (why,) = [b for b in boxes if LONG in b[4].translate(BIDI_ISOLATES)]
    assert why[3] - why[1] >= 28, why                       # more than one 12px line: wrapped, not stretched across the row
    model = [b for b in boxes if b[4] == "APS-SL3N-512"]
    assert model and why[0] > model[0][2], (why, model)     # it ends before the end block begins
