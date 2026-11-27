"""#2898 -- on a failure screen whose primary button is "F2 ...", the theme chip does not say F2.

`main.c` presses the failure button on F2 (#1802), but the chip in the header corner drew its own
"F2" next to the moon: two hints for one key, with two meanings. A person who wanted the dark theme
and pressed F2 restarted the machine. The chip stays (a click still turns the theme) and names no
key there.

Pixels, not source text: the chip's right half holds the "F2" glyphs and nothing else (the moon sits
in its left half). On the unknown-machine screen that half is plain header; on an ordinary failure
screen, the control, it is not.
"""

from __future__ import annotations

import shlex

from test_cloner_gui import _build_gui, _png, _png_rgb
from test_failure_screen_1569 import GUI_BUILD, _state_for_png, run_agent
from test_unknown_screen_1802 import STRANGER

#: default --png size is 1280x1024 (>= 1200 wide): the chip is 56 px wide at x = 24 (N_HEADER_PAD),
#: 28 px high, centred in the 48 px header. Its right half, clear of the moon: x 54..78, y 14..34.
X0, X1, Y0, Y1 = 54, 78, 14, 34


def _chip_ink(png) -> int:
    w, h, ch, px = png
    bg = px[(24 * w + 300) * ch:(24 * w + 300) * ch + 3]   # plain header, away from every control
    n = 0
    for y in range(Y0, Y1):
        for x in range(X0, X1):
            p = px[(y * w + x) * ch:(y * w + x) * ch + 3]
            if max(abs(p[i] - bg[i]) for i in range(3)) > 60:
                n += 1
    return n


@GUI_BUILD
def test_the_chip_names_no_key_where_f2_presses_a_failure_button(tmp_path):
    binary = _build_gui(tmp_path)

    plain = tmp_path / "plain"
    plain.mkdir()
    _png(binary, plain / "card", _state_for_png(plain, "capture did not complete"))
    control = _chip_ink(_png_rgb(plain / "card-failure-light.png"))
    assert control > 15, f"the control failure screen draws no F2 on the chip: {control}"

    unknown = tmp_path / "unknown"
    unknown.mkdir()
    r = run_agent(unknown, f'ui_unknown_records {shlex.quote(STRANGER)} 0 > "$RUN_DIR/fail.state"')
    state = unknown / "state.txt"
    state.write_text((r["run"] / "fail.state").read_text(encoding="utf-8")
                     + "message=x|y\nhello_age=4\nhello_rc=200\n", encoding="utf-8", newline="\n")
    _png(binary, unknown / "card", state)
    shown = _chip_ink(_png_rgb(unknown / "card-failure-light.png"))
    assert shown < control / 5, f"F2 is drawn on the chip next to a button that owns F2: {shown} (control {control})"
