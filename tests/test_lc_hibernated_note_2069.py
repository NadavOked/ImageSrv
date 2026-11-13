"""#2069 / #2084 -- the local clone from a Windows left in Fast Startup, the screen's side.

#2208 made the engine do it (the hibernation is dropped on the source's RAM view, then the
view shrinks). What mockup 09 section 3 (Nadav, 24/10) shows as the final state was not on
the screen: next to the smaller target an ORANGE note, not a refusal --
"המקור במצב שינה — ההשהיה תבוטל בעותק", and in the foot the same in a sentence
("המקור במצב שינה של Windows. בעותק ההשהיה מבוטלת, והמקור עצמו לא משתנה."). The screen cannot
know it by itself, and it must not guess: the measure (lcinv.sh) leaves ``$LC/measure/<src>.hib``
when ``ntfs-3g.probe`` says 14, and the bridge (lcgui.sh) adds ``|hibernated`` to ``lc_src`` only
then. An agent that does not send it says nothing.

#2084 (items 2-3, which the issue's triage found without a test): the reason a refused source
gives is cut by ``lc_state.c cpy`` at the buffer's end -- never in the middle of a letter (it drew
a broken character, ☐) -- and the refusal in the mockup's words, drawn on a card, stays inside it
(#2205 keeps the lines in, here with the real text) and names no device ("sdc") and no capture
("קלטו מחדש").

**Negative control** (Testrunner): ``agent/lib/localclone/lcinv.sh``, ``agent/lib/localclone/lcgui.sh``,
``native-gui/src/lc_state.c``, ``native-gui/src/localclone.h`` and ``native-gui/src/screens_local.c`` at the
base commit -> the #2069 tests here fail; and ``lc_state.c`` at ``f239cb57^`` (before #2084's cut) ->
the UTF-8 test fails.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, posix
from test_lc_card_fit_2205 import FRAME, _cards, fit_driver  # noqa: F401 (fit_driver is a fixture)
from test_lc_faststartup_shrink_2208 import MEASURE
from test_local_clone_agent import run
from test_native_local_clone import pytestmark as native_mark

SHELL = requires_native(("bash", BASH))
NATIVE = native_mark

NOTE = "המקור במצב שינה — ההשהיה תבוטל בעותק"
FOOT = "המקור במצב שינה של Windows. בעותק ההשהיה מבוטלת, והמקור עצמו לא משתנה."
CARD_LINE = "נכנס עם כיווץ זמני של המקור"


# --- the agent: the measure leaves the mark, the bridge carries it -----------------------------

def _measure(tmp_path, rc: int, before: str = "") -> dict[str, str]:
    out = run(tmp_path, f'SRC_RC={rc}; ' + MEASURE.replace('lc_measure sda;', before + 'lc_measure sda;', 1)
              + '\necho "hib=$(cat "$LC/measure/sda.hib" 2>/dev/null)"; echo "hibfile=$([ -e "$LC/measure/sda.hib" ] && echo yes || echo no)"')
    assert out.returncode == 0, out.stderr
    return dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)


@SHELL
def test_a_hibernated_source_leaves_the_mark_and_is_still_measured(tmp_path):
    got = _measure(tmp_path, 14)
    assert got["hibfile"] == "yes" and got["hib"] == "1", got
    assert got["verdict"].split("|")[0] == "shrink", got   # the engine's side (#2208) is unchanged


@SHELL
@pytest.mark.parametrize("rc", [0, 15, 2])
def test_a_source_that_is_not_hibernated_leaves_no_mark(tmp_path, rc):
    """The guards: a clean one, a dirty one (refused by name, #651) and one whose probe did not
    answer never get the orange note -- "not hibernated" is only said on a probe's 14."""
    got = _measure(tmp_path, rc)
    assert got["hibfile"] == "no", got


@SHELL
def test_a_mark_of_an_earlier_measure_does_not_outlive_it(tmp_path):
    """The mark is per measure: the same source measured again, now clean (Windows was shut
    down all the way in between), must not still be called hibernated."""
    got = _measure(tmp_path, 0, before='mkdir -p "$LC/measure"; echo 1 > "$LC/measure/sda.hib"; ')
    assert got["hibfile"] == "no", got


BRIDGE = ('export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC/inv" "$LC/measure"; echo 0 > "$RUN_DIR/min_free_gb"; '
          'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo idle > "$LC/phase"; '
          'echo "3|sdc|C|s3|480000000000|ok|ok|Windows|4|212000000000|480000000000|1|" > "$LC/inv/sdc.line"; '
          'echo sdc > "$LC/source"; echo "done|200000000000|" > "$LC/measure/sdc"; ')


@SHELL
@pytest.mark.parametrize("copy,mark,want", [
    ("123|", True, "lc_src=sdc|done|123||hibernated"),
    ("-1|מחיצה 3: partclone חסר", True, "lc_src=sdc|done|-1|מחיצה 3: partclone חסר|hibernated"),
    (None, True, "lc_src=sdc|done|||hibernated"),
    ("123|", False, "lc_src=sdc|done|123|"),          # as it was: an old screen reads exactly this
    (None, False, "lc_src=sdc|done||"),
])
def test_the_bridge_adds_hibernated_only_for_a_marked_source(tmp_path, copy, mark, want):
    put = f'printf "%s\\n" {copy!r} > "$LC/measure/sdc.copy"; ' if copy else ""
    put += 'echo 1 > "$LC/measure/sdc.hib"; ' if mark else ""
    out = run(tmp_path, BRIDGE + put + 'lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    assert want in out.stdout.splitlines(), out.stdout


# --- the screen ---------------------------------------------------------------------------------

G = 1_000_000_000
#: mockup 09 section 3: drawer 3 (870, 500 GB) is the source, drawer 2 (256 GB) needs the shrink,
#: drawer 4 (500 GB) takes it as it is -- the note belongs to drawer 2 only
SCREEN = ("lc_screen=local\nlc_inv=done\nlc_phase=idle\nlc_usb=0\n"
          f"lc_disk=2|sdb|SAMSUNG MZ7LN256|S2|{256 * G}|ok|ok|Windows|4|{76 * G}|{200 * G}|1||2|SATA||\n"
          f"lc_disk=3|sdc|Samsung SSD 870|S3|{500 * G}|ok|ok|Windows|4|{76 * G}|{480 * G}|1||3|SATA||\n"
          f"lc_disk=4|sdd|TOSHIBA DT01ACA050|S4|{500 * G}|ok|ok|Windows|4|{76 * G}|{480 * G}|1||4|SATA||\n"
          f"lc_src=sdc|done|{76 * G}|{{hib}}\n"
          f"lc_fit=sdb|shrink|{200 * G}|\nlc_fit=sdd|fits|{480 * G}|\n")
SRC = 1   # sdc's place in the disks above


def _state(hibernated: bool) -> str:
    return SCREEN.replace("{hib}", "|hibernated" if hibernated else "")


def _all_text(binary, tmp_path, state, size) -> list[str]:
    w, h = size.split("x")
    path = tmp_path / "all.txt"
    path.write_text(state, encoding="utf-8", newline="\n")
    run_ = subprocess.run([str(binary), posix(path), w, h, posix(tmp_path / "all.png"), str(SRC)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run_.returncode == 0, run_.stderr + run_.stdout
    return [ln.split(" ", 5)[5] for ln in run_.stdout.splitlines() if ln.startswith("T ") and len(ln.split(" ", 5)) > 5]


@NATIVE
@pytest.mark.parametrize("size", ["1280x800", "1024x768"])
def test_the_smaller_target_says_the_source_is_hibernated_and_the_foot_says_it_in_orange(fit_driver, tmp_path, size):
    cards = _cards(fit_driver, tmp_path, _state(True), size, SRC)
    assert sorted(cards) == [0, 1, 2], cards
    on = {i: " ".join(t for _, _, _, t in lines) for i, (_, lines) in cards.items()}
    assert NOTE in on[0] and CARD_LINE in on[0], on        # sdb: drawer 2 -- needs the shrink
    assert "שינה" not in on[2], on                          # sdd: drawer 4 -- takes it whole, no note
    assert NOTE not in on[1], on                            # the source itself
    foot = [t for t in _all_text(fit_driver, tmp_path, _state(True), size) if "ההשהיה מבוטלת" in t]
    assert foot == [FOOT], foot


@NATIVE
def test_without_the_field_the_screen_says_nothing_about_hibernation(fit_driver, tmp_path):
    """An old agent (four fields) or a source that is not hibernated: the shrink line as it was."""
    cards = _cards(fit_driver, tmp_path, _state(False), "1280x800", SRC)
    text = " ".join(t for _, lines in cards.values() for _, _, _, t in lines)
    assert CARD_LINE in text and "שינה" not in text and "השהיה" not in text, text
    assert not [t for t in _all_text(fit_driver, tmp_path, _state(False), "1280x800") if "השהיה" in t]


@NATIVE
@pytest.mark.parametrize("size", ["960x540", "1024x768"])
def test_the_note_keeps_every_line_inside_its_card(fit_driver, tmp_path, size):
    """#2205's rule with the longest line the drawer has: the note in front of the verdict."""
    cards = _cards(fit_driver, tmp_path, _state(True), size, SRC)
    for i, ((x, y, w, h), lines) in cards.items():
        assert len(lines) >= 2, (i, lines)
        for top, height, _, text in lines:
            assert y + FRAME <= top and top + height <= y + h - FRAME, (size, i, (y, h), top, height, text)


# --- #2084 items 2 and 3: the refusal's words, and the cut -------------------------------------

#: lcinv.sh's refusal for a source that cannot be shrunk, as measure() in test_lc_ntfs_target_1937 reads it
REFUSAL = ("המקור (דיסק 3) במצב שינה של Windows — אי אפשר לכווץ אותו לדיסק הזה, שקטן ממנו. "
           "ב-Windows של המקור: \u2066powercfg /h off\u2069, ואז כיבוי")


@NATIVE
@pytest.mark.parametrize("size", ["960x540", "1024x768"])
def test_the_refusal_in_the_mockups_words_stays_inside_its_card_and_names_no_device(fit_driver, tmp_path, size):
    shrink = f"lc_fit=sdb|shrink|{200 * G}|"
    state = SCREEN.replace("{hib}", "").replace(shrink, f"lc_fit=sdb|unchecked||{REFUSAL}")
    assert REFUSAL in state, "the refusal must be in the state, or this tests nothing"
    cards = _cards(fit_driver, tmp_path, state, size, SRC)
    (x, y, w, h), lines = cards[0]
    assert len(lines) >= 2, lines
    for top, height, _, text in lines:
        assert y + FRAME <= top and top + height <= y + h - FRAME, (size, (y, h), top, height, text)
    text = " ".join(t for _, _, _, t in lines)
    assert "במצב שינה" in text and "sdc" not in text and "קלטו" not in text, text


CUT_DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    (void)argc; static State s; FILE *f = fopen(argv[1], "r"); if (!f) return 2;
    state_parse(&s, f); fclose(f);
    if (s.lc.nfits < 1) return 3;
    fwrite(s.lc.fits[0].reason, 1, strlen(s.lc.fits[0].reason), stdout);
    return 0;
}
'''


@pytest.fixture(scope="module")
def cut_driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("cut2084")
    (d / "driver.c").write_text(CUT_DRIVER, encoding="utf-8", newline="\n")
    sources = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI, capture_output=True,
                             text=True, stdin=subprocess.DEVNULL, timeout=30)
    assert sources.returncode == 0, sources.stderr
    srcs = [posix(GUI / s) for s in sources.stdout.split() if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" '
                            '$(pkg-config --cflags pangocairo cairo libdrm) -o "$2" "$3" "${@:4}" '
                            '$(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _parsed_reason(driver, tmp_path, reason: str) -> bytes:
    path = tmp_path / "cut.txt"
    path.write_text(f"lc_fit=sdb|unchecked||{reason}\n", encoding="utf-8", newline="\n")
    r = subprocess.run([str(driver), posix(path)], capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return r.stdout


#: LcFit.reason is 240 bytes: 239 and the NUL. Each letter width, at every alignment of the cut.
@NATIVE
@pytest.mark.parametrize("letter", ["ש", "\u2066", "\U0001F4BF"], ids=["2-byte Hebrew", "3-byte isolate", "4-byte"])
@pytest.mark.parametrize("pad", [0, 1, 2, 3])
def test_a_long_reason_is_cut_between_letters_never_inside_one(cut_driver, tmp_path, letter, pad):
    reason = "a" * pad + letter * 200
    got = _parsed_reason(cut_driver, tmp_path, reason)
    text = got.decode("utf-8")   # strict: a letter cut in the middle raises here -- the ☐ of the issue
    assert reason.startswith(text), "the cut text is the start of the reason, nothing added"
    # and it lost at most the one letter at the cut: cpy cannot tell a last letter that ends exactly at
    # the buffer from one cut in half, so it drops that one too (<= 4 bytes) -- measured, not a bug of #2084
    assert 239 - 4 <= len(got) <= 239, len(got)


@NATIVE
def test_a_reason_that_fits_is_kept_whole(cut_driver, tmp_path):
    assert _parsed_reason(cut_driver, tmp_path, REFUSAL).decode("utf-8") == REFUSAL
