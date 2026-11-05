"""#2433 -- bounds in the C code (security scan 04/11, scan9 findings 3-5, and the fuzz).

1. **`fanout --readback`** (agent/fanout.c): the plausibility check
   `(total + 7) / 8 > 2^31` wrapped for `total >= 2^64 - 7` -- the map was
   malloc(0), and the popcount over `total >> 3` bytes read far outside it:
   SIGSEGV (rc -11). A header whose block count needs a map over 2 GiB is
   refused before the arithmetic now (`total > 2^34`), exit 3.
2. **`text_escape`** (native-gui/src/text.c) cut the escaped text with
   snprintf -- in the middle of `&amp;` or of a Hebrew letter, and Pango
   refused the whole markup: the button or the warning drew empty. The cut now
   ends on a whole character and never inside an entity.
3. **`state_parse`** (native-gui/src/state.c): a line with broken UTF-8 (a
   model or a serial from the disk) reached Pango as it came, and Pango drew
   nothing for it. The line is made valid on the way in (U+FFFD for each bad
   byte), so it shows with a replacement character instead of vanishing.
4. **`mem_open`** (native-gui/src/backend.c) truncated the memory framebuffer
   file to its own size: the mirror's file at a larger display size shrank
   under a monitor that maps it -- SIGBUS there. It only ever grows now.

All four are C: gcc (1), and gcc + pango/cairo/libdrm (2-4) -- skipped on
Windows, run on the Testrunner / CI.

Negative control: fanout.c, text.c, state.c and backend.c back to main -- (1)
dies on SIGSEGV instead of exit 3, (2) prints "bad" lines, (3) the
replacement character is missing from what the screen laid out, (4) the file
is shrunk to the GUI's own size.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pytest

import pcl_image as P
from native import requires_native
from test_cloner_gui import BASH, GUI, _build_gui, _pkgconfig, posix
from test_headless_framebuffer import HEADER_BYTES, HEIGHT, MAGIC, STRIDE
from test_readback_1805 import _readback, fanout, needs_gcc  # noqa: F401 -- fanout is the module's build fixture

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native-gui is built on Linux only",
)


# --- 1. fanout --readback: a block count that wraps the map size ------------------------


@needs_gcc
@pytest.mark.parametrize("total", [2**64 - 1, 2**64 - 7, 2**64 - 8, 2**34 + 1])
def test_a_block_count_whose_map_would_wrap_is_refused_not_read(fanout, tmp_path, total):  # noqa: F811
    head = P.header(total, 0, 4096, device_size=0)
    img = head + struct.pack("<I", P.crc(b""))                 # the map malloc(0) took: none, and its CRC
    dev = tmp_path / "sdb3"
    dev.write_bytes(bytes(4096))
    rc, out, err = _readback(fanout, dev, img, "-x", str(dev))
    assert (rc, out) == (3, ""), (rc, err)
    assert "implausible map" in err, err


@needs_gcc
def test_a_plausible_map_still_reads(fanout, tmp_path):  # noqa: F811
    """The guard is not a wall: a real image of 4096 blocks reads back."""
    total, used = 4096, list(range(0, 4096, 3))
    img = P.image(total, used, 4096)
    dev = tmp_path / "sdb3"
    dev.write_bytes(P.device(total, used, 4096))
    rc, out, err = _readback(fanout, dev, P.map_only(img, total), "-x", str(dev))
    assert rc == 0, err


# --- 2. text_escape never cuts inside an entity or a letter -----------------------------

ESCAPE_DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "text.h"
int main(void) {
    static const char *cases[] = {
        "a&b&c&d&e&f&g&h&i&j&k", "<<<<<<<<<<<<", "\"'\"'\"'\"'\"'",
        "\xd7\x90\xd7\x91\xd7\x92\xd7\x93\xd7\x94\xd7\x95\xd7\x96\xd7\x97",   /* אבגדהוזח */
        "x\xd7\x90&\xd7\x91<y>\xe2\x80\x8f\xd7\x92", NULL };
    int bad = 0, cut = 0;
    for (int c = 0; cases[c]; c++)
        for (size_t n = 1; n <= 64; n++) {
            char out[64];
            memset(out, 'Z', sizeof out);
            text_escape(cases[c], out, n);
            size_t len = strlen(out);
            if (len >= n || !g_utf8_validate(out, -1, NULL) ||
                !pango_parse_markup(out, -1, 0, NULL, NULL, NULL, NULL)) {
                printf("bad case=%d n=%zu out=%s\n", c, n, out); bad++;
            }
            if (len < strlen(g_markup_escape_text(cases[c], -1))) cut++;
        }
    printf("cut=%d bad=%d\n", cut, bad);
    return 0;
}
'''


@GUI_BUILD
def test_an_escaped_text_cut_to_its_buffer_is_still_valid_markup(tmp_path):
    src = tmp_path / "escape.c"
    src.write_text(ESCAPE_DRIVER, encoding="utf-8", newline="\n")
    binary = tmp_path / "escape"
    build = subprocess.run(
        [BASH, "-c", 'set -e; cc -O1 -Wall -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo) '
                     '-I "$1/src" -o "$2" "$3" "$1/src/text.c" $(pkg-config --libs pangocairo cairo) -lm',
         "_", posix(GUI), posix(binary), posix(src)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines[-1].startswith("cut=") and not lines[-1].startswith("cut=0 "), lines[-1]   # the cut really happened
    assert lines[-1].endswith(" bad=0"), "\n".join(lines[:20])


# --- 3. state_parse makes a broken line valid instead of losing it ----------------------


@pytest.fixture(scope="module")
def gui_binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui2433"))


IDLE = (b"message=x|y\ndisk_probe=drives\ndrawer_count=2\n"
        b"cdrawer_slot=1|SATA 0|Top\xffRow\ncdrawer_slot=2|SATA 1|\n"
        b"cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed||6.0|6.0||1|SATA 0\n")


@GUI_BUILD
def test_a_name_with_broken_utf8_is_shown_with_a_replacement_character(gui_binary, tmp_path):
    state = tmp_path / "state.txt"
    state.write_bytes(IDLE)
    log = tmp_path / "text.log"
    log.write_bytes(b"")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=posix(log))
    run = subprocess.run([str(gui_binary), "--png", posix(tmp_path / "p"), "--size", "1280x800",
                          "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
                         capture_output=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    raw = log.read_bytes()
    raw.decode("utf-8")                                       # strict: nothing broken reached the screen
    assert "Top�Row".encode() in raw, raw[-2000:]
    assert b"Invalid UTF-8" not in run.stderr, run.stderr[-2000:]


# --- 4. mem_open never shrinks the file a monitor maps ----------------------------------


@GUI_BUILD
def test_a_larger_memory_framebuffer_file_is_not_shrunk(gui_binary, tmp_path):
    fb = tmp_path / "imagesrv-fb"
    big = HEADER_BYTES + 1920 * 4 * 1080                      # the mirror's file at a 1080p display
    assert big > HEADER_BYTES + STRIDE * HEIGHT
    fb.write_bytes(bytes(big))
    state = tmp_path / "state.txt"
    state.write_text("cloner=Office 2024\ndrawer=1|sda|writing|45|100|\n", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    err = tmp_path / "gui.err"
    with err.open("wb") as errf:
        proc = subprocess.Popen(
            [str(gui_binary), "--backend", "mem", "--fb-file", str(fb), "--no-input", "--screen", "cloner",
             "--state", str(state), "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
            stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL, env=env)
        try:
            deadline = time.monotonic() + 20
            framed = False
            while time.monotonic() < deadline and proc.poll() is None:
                data = fb.read_bytes()[:28]
                seq = struct.unpack("<I", data[24:28])[0] if len(data) == 28 else 0
                if data[:8] == MAGIC and seq >= 2 and seq % 2 == 0:
                    framed = True
                    break
                time.sleep(0.2)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    assert framed, err.read_text(encoding="utf-8", errors="replace")
    assert fb.stat().st_size == big, (fb.stat().st_size, big)
