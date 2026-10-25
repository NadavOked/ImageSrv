"""#2081 (mockup 03 section 2, Nadav 24/10: "המשכפל צריך להיראות כמו 1"): the end of a round on
the cloner that stays on because a disk has a problem (#2024) is the drawers' list -- the title
"הסבב הסתיים — X מתוך Y נכתבו", one line of what was written, no summary cards (speed,
received, disks), and "כבה את המחשב" as the screen's own button under the list; the #2024 bar
is words only (one power-off button on the screen). What the screen says is read from the GUI's
text log (IMAGECTL_GUI_TEXTLOG), on the real binary (lab only).
"""

from __future__ import annotations

import os
import subprocess

from test_cloner_gui import GUI, _build_gui, posix
from test_failure_screen_1569 import GUI_BUILD

HELD = """cloner=office365 v4
drawer=0|sda|done|19500000000|19500000000||||1|SATA 0
drawer=1|sdb|done|19500000000|19500000000||||2|SATA 1
drawer=2|sdc|done|19500000000|19500000000||||3|SATA 2
drawer=4|sdd|failed|780000000|19500000000|כתיבה איטית: 41 MB/s, מתחת לסף 50 MB/s|||4|SATA 4
end_poweroff=0|failed|3|4|1
end_problem=sdd|r|כתיבה איטית: 41 MB/s, מתחת לסף 50 MB/s
hello_age=4
hello_rc=200
message=x|y
"""


def _said(tmp_path, card: str) -> list[str]:
    binary = _build_gui(tmp_path)
    st = tmp_path / "held.txt"
    st.write_text(HELD, encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"
    env = dict(os.environ, IMAGECTL_GUI_TEXTLOG=str(log))
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "c"), "--size", "1280x800", "--mac", "3C:52:82:A1:00:1F",
                          "--ip", "10.10.10.31", "--state", posix(st)], capture_output=True, text=True, timeout=120,
                         env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr[-2000:]
    lines = log.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"@@ {card}-light")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("@@ ")), len(lines))
    return lines[start + 1:end]


@GUI_BUILD
def test_the_held_end_of_a_round_is_the_list_with_its_title_and_no_summary_cards(tmp_path):
    said = _said(tmp_path, "cloner-end")
    text = "\n".join(said)
    assert "הסבב הסתיים — 3 מתוך 4 נכתבו" in said, text
    for card in ("מהירות", "התקבלו", "דיסקים תקינים"):
        assert card not in said, (card, text)
    assert any("כבה את המחשב" in s for s in said), text                      # the screen's own button
    assert 'המחשב נשאר דלוק כי יש דיסק עם בעיה. כשתסיימו: "כבה את המחשב".' in said, text   # the bar: words only
    assert "כבה" not in [s.strip() for s in said], text                     # no second "כבה" button
