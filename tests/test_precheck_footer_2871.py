"""#2871 (fd1111, 11/11) -- the orange box before a wave said "הגל ייכתב גם כך — אזהרה, לא חסימה",
and the wave failed 4-37 s later for the very reason it warned of: the agent's write check
(writecheck.sh, write_speed_verdict -- run by write_check_round in drawers.sh before the branch
between the engines) fails a drawer that writes below the floor ("סף מהירות כתיבה לדיסק" in the
settings, writecheck_min_mbps) or below half of the other drawers of its kind. Nadav 12/11:
"'אזהרה, לא חסימה' יוצא".

The sentence under the warnings is drawn twice -- the console (roomPrecheckHtml, console.js) and
the native GUI of the build machine (screens_room.c). Both now say what the agent does, and say
the same thing.

Static, on purpose: the console's rendering is tests/deploy_room_page.test.cjs (#432), and the
GUI's box is tests/test_room_precheck_native_432_65.py. What is checked here is the sentence.

Negative control: native-gui/src/screens_room.c and server/static/console.js back to the base -- see the PR.
"""

from __future__ import annotations

import re
from pathlib import Path

from console_src import console_js

REPO = Path(__file__).resolve().parent.parent
SCREENS_ROOM = REPO / "native-gui" / "src" / "screens_room.c"
WRITECHECK = REPO / "agent" / "lib" / "restore" / "writecheck.sh"
FLOOR_PHRASE = "מהירות המינימלית לכונן שהשרת קובע"   # נדב 14/11 בערב: הסף אינו בהגדרות, והמוח קובע אותו


def _gui_sentence() -> str:
    """The sentence the GUI draws under the room warnings: the one rtl_block_make after the list
    (the "ועוד N" line) and before the missing machines."""
    c = SCREENS_ROOM.read_text(encoding="utf-8")
    body = c[c.index('"ועוד %d — ראו בקונסולה"'):c.index("if (s->nroom_missing) {")]
    m = re.search(r'Text f = rtl_block_make\(cr, N_SUB, 400, "((?:[^"\\]|\\.)*)", inner \+ 14\);', body)
    assert m, "no sentence under the room warnings in screens_room.c"
    return m.group(1).replace('\\"', '"')


def _console_sentence() -> str:
    js = console_js()
    body = js[js.index("function roomPrecheckHtml("):]
    body = body[:body.index("\n}\n")]
    m = re.search(r'\$\{impactList\(w\.map\(\(x\) => \["w", esc\(x\.text_he\)\]\)\)\}([^`]*)`\);', body)
    assert m, "no sentence under the room warnings in roomPrecheckHtml"
    return m.group(1)


def test_neither_screen_says_a_warning_is_not_a_block():
    """RED: both said "אזהרה, לא חסימה", and a drawer measured slow fails the wave's write."""
    for where, sentence in (("gui", _gui_sentence()), ("console", _console_sentence())):
        assert "לא חסימה" not in sentence, (where, sentence)


def test_the_sentence_says_what_the_agent_does_with_a_slow_drawer():
    """RED: nothing on either screen named the floor, or that a drawer below it fails."""
    for where, sentence in (("gui", _gui_sentence()), ("console", _console_sentence())):
        assert FLOOR_PHRASE in sentence and "בהגדרות" not in sentence and "תיכשל בשמה" in sentence, (where, sentence)
        assert "פחות מחצי מהמגירות האחרות מאותו סוג" in sentence, (where, sentence)


def test_the_gui_and_the_console_say_the_same_sentence():
    """Guard (passes in both states): one sentence, two screens."""
    assert _gui_sentence() == _console_sentence()


def test_the_named_floor_is_not_a_settings_row_and_the_agent_fails_below_it():
    """The floor (writecheck_min_mbps) left the settings screen (Nadav 14/11 evening), so the sentence no longer sends
    the operator to a row; the agent still fails a slow drawer by name (it does not warn)."""
    js = console_js()
    assert "writecheck_min_mbps" not in js
    sh = WRITECHECK.read_text(encoding="utf-8")
    verdict = sh[sh.index("write_speed_verdict() {"):sh.index("write_check_round() {")]
    # #3268: the comparison moved into `_wc_slow_why` (the waiting screen asks the same question); the verdict hands it the floor
    assert '_wc_slow_why "$_sv_d" "$_sv_all" "$WC_FLOOR"' in verdict
    slow_why = sh[sh.index("_wc_slow_why() {"):sh.index("write_speed_verdict() {")]
    assert '_sw_fl=$3' in slow_why and '[ "$_sw_m" -lt "$_sw_fl" ]' in slow_why
    assert 'target_set "$_sv_d" failed "$_sv_why"' in verdict
