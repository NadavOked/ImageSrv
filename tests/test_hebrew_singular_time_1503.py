"""#1503 -- the waiting screen said "השרת ענה hello 200 · לפני 1 שניות".

Hebrew has a singular for one second / minute / hour / day. A count printed with
``%d שניות`` is wrong exactly at 1, and a countdown passes through 1 every time.
The words now come from one place (``native-gui/src/he_time.c``, compiled on its own
here); the screens that counted seconds themselves use it, and the console's two
relative-time helpers say "לפני שעה" / "לפני יום" instead of "לפני 1 ימים".
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
STATIC = REPO / "server" / "static"
CC = shutil.which("cc") or shutil.which("gcc")

native = requires_native(("cc", CC), why="he_time.c מקומפל לבד, בלי cairo")


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    work = tmp_path_factory.mktemp("hetime")
    (work / "t.c").write_text(
        '#include <stdio.h>\n#include <stdlib.h>\n#include "he_time.h"\n'
        'int main(int c, char **v) { char b[64];\n'
        '  for (int i = 2; i < c; i++) { long long n = atoll(v[i]);\n'
        '    if (v[1][0] == \'s\') he_secs(n, b, sizeof b); else he_ago(n, b, sizeof b);\n'
        '    puts(b); } }\n', encoding="utf-8", newline="\n")
    built = subprocess.run([CC, "-Wall", "-Wextra", "-I", str(SRC), "-o", str(work / "t"),
                            str(work / "t.c"), str(SRC / "he_time.c")],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    assert built.returncode == 0, built.stderr

    def run(kind: str, *nums: int) -> list[str]:
        out = subprocess.run([str(work / "t"), kind, *map(str, nums)], capture_output=True,
                             text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.splitlines()
    return run


@native
def test_ago_says_singular_at_one_and_plural_at_two(probe):
    got = probe("a", 0, 1, 2, 59, 60, 119, 120, 3599, 3600, 7199, 7200, 10800)
    assert got == ["לפני 0 שניות", "לפני שנייה", "לפני 2 שניות", "לפני 59 שניות",
                   "לפני דקה", "לפני דקה", "לפני 2 דקות", "לפני 59 דקות",
                   "לפני שעה", "לפני שעה", "לפני 2 שעות", "לפני 3 שעות"]


@native
def test_ago_never_prints_a_one_with_a_plural_noun_or_a_negative_age(probe):
    assert probe("a", -5) == ["לפני 0 שניות"]
    for n in range(0, 20000, 7):
        (s,) = probe("a", n)
        assert not re.search(r"\b1 (שניות|דקות|שעות)", s), (n, s)


@native
def test_secs_for_a_countdown(probe):
    assert probe("s", 0, 1, 2, 30) == ["0 שניות", "שנייה", "2 שניות", "30 שניות"]


def _c(name: str) -> str:
    return re.sub(r"/\*.*?\*/", "", (SRC / name).read_text(encoding="utf-8"), flags=re.S)


def test_no_screen_counts_seconds_with_a_bare_percent_d():
    """pace_age and he_time.c handle 1 before they print a plural; nothing else may."""
    allowed = {"he_time.c", "screens_rounds.c"}
    bad = [f"{p.name}: {m.group(0)}" for p in sorted(SRC.glob("*.c")) if p.name not in allowed
           for m in re.finditer(r'%l*d שניות', _c(p.name))]
    assert not bad, bad


def test_the_hello_lines_and_the_server_pill_use_the_shared_words():
    assert "he_ago(s->hello_age" in _c("screens.c")
    assert "ענה לפני" not in _c("screens.c") and "שנ'" not in _c("screens.c")
    assert "he_ago(s, out, n)" in _c("screens_standby.c")
    assert "src/he_time.c" in (REPO / "native-gui" / "Makefile").read_text(encoding="utf-8")


def test_console_relative_times_have_a_singular():
    net = (STATIC / "net.js").read_text(encoding="utf-8")
    assert '"לפני שעה"' in net and '"לפני יום"' in net and '"לפני שנייה"' in net
    assert "ימים`" in net and "${Math.round(seconds / 86400)} ימים" not in net
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    skew = js[js.index("function probeSkewText"):js.index("function machineVerdictsHtml")]
    assert '"יום"' in skew and '"שעה"' in skew and '"שנייה"' in skew
