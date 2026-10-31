"""#1503 (part) -- the native GUI showed the server's failure cause in English.

Metal, 16/10: the cloner's SMART question ("ממתין להחלטה על דיסק 3") printed
``unclassified``. For a ``failed_last`` disk the reason the GUI is given is the
server's cause (``server/disks/ata_cause.py`` CAUSES: cable | disk | unclassified), and
``smart_reason_text`` knew only the agent's SMART codes and passed anything else
through as-is. The console already says them in Hebrew (``FAILURE_CAUSE_HE``);
the screen must use the same words.

The function is compiled on its own (its source is cut out of
``screens_rounds.c``, so no cairo/pango and no drift between test and source) and
asked about every code word.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.disks.ata_cause import CAUSES

REPO = Path(__file__).resolve().parent.parent
ROUNDS = REPO / "native-gui" / "src" / "screens_rounds.c"
CONSOLE = REPO / "server" / "static" / "console.js"
CC = shutil.which("cc") or shutil.which("gcc")
HEBREW = re.compile("[א-ת]")

#: the agent's own codes (smart.sh) that smart_reason_text already words
AGENT_REASONS = ("health_failed", "pending_sectors", "uncorrectable", "no_health",
                 "no_smartctl", "timeout", "pending")


def _function(name: str) -> str:
    text = ROUNDS.read_text(encoding="utf-8")
    start = re.search(r"\nconst char \*" + name + r"\(const char \*\w+\) \{", text)
    assert start, f"{name}() is not defined in screens_rounds.c"
    depth, i = 1, start.end()
    while depth:
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start.start():i]


def _console_words() -> dict[str, str]:
    block = re.search(r"const FAILURE_CAUSE_HE = \{([^}]*)\}", CONSOLE.read_text(encoding="utf-8"))
    assert block, "FAILURE_CAUSE_HE is gone from console.js"
    return dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))


@pytest.fixture(scope="module")
def reason_text(tmp_path_factory):
    work = tmp_path_factory.mktemp("cause")
    (work / "t.c").write_text(
        '#include <stdio.h>\n#include <string.h>\n' + _function("smart_reason_text") +
        '\nint main(int c, char **v) { for (int i = 1; i < c; i++) puts(smart_reason_text(v[i])); }\n',
        encoding="utf-8", newline="\n")
    built = subprocess.run([CC, "-Wall", "-Wextra", "-o", str(work / "t"), str(work / "t.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert built.returncode == 0, built.stderr

    def run(*codes: str) -> list[str]:
        out = subprocess.run([str(work / "t"), *codes], capture_output=True, text=True,
                             encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.splitlines()
    return run


native = requires_native(("cc", CC), why="הפונקציה מקומפלת לבד, בלי cairo")


@native
def test_the_console_and_the_server_agree_on_which_causes_exist():
    assert set(_console_words()) == set(CAUSES)


@native
@pytest.mark.parametrize("cause", CAUSES)
def test_every_server_cause_is_said_in_the_consoles_hebrew_words(reason_text, cause):
    (shown,) = reason_text(cause)
    assert shown != cause and HEBREW.search(shown), f"{cause!r} reached the screen as {shown!r}"
    assert _console_words()[cause] in shown


@native
@pytest.mark.parametrize("code", AGENT_REASONS)
def test_the_agents_smart_codes_are_still_in_hebrew(reason_text, code):
    (shown,) = reason_text(code)
    assert shown != code and HEBREW.search(shown)


@native
def test_no_reason_is_nothing_and_an_unknown_code_is_not_hidden(reason_text):
    assert reason_text("", "ok", "brand_new_code") == ["", "", "brand_new_code"]
