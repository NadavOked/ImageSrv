"""#3666 (אפיק #3653, הכרעת נדב 18/11): הגואי לוקח את הפסק של הדיסק -- הוא לא מחשב אותו מהעובדות.

שלושה מסכים, ובכל אחד שני כיוונים: **הפסק גובר על העובדות**, ו**בלי פסק אין צבע** ("לא נבדק", לעולם לא ירוק ולא אדום מניחוש).

* מסך ההמתנה של משכפל (``cdisk=`` + ``cdisk_rule=``): עובדות "ירוקות" ופסק אדום -- אדום. עובדות של בעיית SATA בלי פסק -- לא אדום.
* חדר ההפצה (``room_drawer=`` + ``room_drawer_rule=``): בלי פסק, חריץ ש-SMART שלו תקין אינו ירוק (אפור).
* השיכפול המקומי (``lc_disk=`` + ``lc_rule=``): עובדות תקינות ופסק אדום -- דלג, לא יעד; עובדות אדומות בלי פסק -- כמו "לא נבדק".

הטסטים כאן כותבים את המצב ישירות, **בלי** `disk_rules_state.with_rules` (המסייע שמוסיף פסקים לפי העובדות, כמו שהסוכן עושה): מה שנבדק הוא מי
מחליט. רצים על הטסטראנר בלבד (cairo/pango, ``--png``).
"""

from __future__ import annotations

import pytest

from test_disk_red_parity_2848 import NOTE, ROOM, STANDBY
from test_lc_source_target_2848 import LCR_SKIP, LCR_TARGET, WORK, _seq, seq_driver  # noqa: F401 -- fixture
from test_native_local_clone import pytestmark as _native  # noqa: F401
from test_room_precheck_native_432_65 import _GUI, _build_gui, _count, _render

pytestmark = _native


def _px(tmp_path, names, token, theme):
    return {n: sum(_count(f, token, theme) for f in tmp_path.glob(f"{n}-*-{theme}.png")) for n in names}


# --- the cloner's waiting screen -----------------------------------------------------------------------------------

@_GUI
def test_the_standby_bay_follows_the_verdict_not_the_facts(tmp_path):
    """Facts say green (SMART passed, no words); the agent's verdict says red (a SATA problem): the bay is red."""
    binary = _build_gui(tmp_path)
    plain = STANDBY.format(words="")
    red = plain + "cdisk_rule=sdb|red|1|0|sata_issue|בעיית SATA\n"
    _render(binary, plain + "cdisk_rule=sdb|green|1|1||\n", tmp_path / "a", rules=False)
    _render(binary, red, tmp_path / "b", rules=False)
    for theme in ("light", "dark"):
        px = _px(tmp_path, "ab", "--clr-danger", theme)
        assert px["b"] > px["a"] + 200, (theme, px)


@_GUI
def test_the_standby_bay_with_sata_facts_and_no_verdict_is_not_red(tmp_path):
    """The facts say a SATA problem (link_reset), but no verdict arrived: the GUI does not work it out -- no red from the words."""
    binary = _build_gui(tmp_path)
    _render(binary, STANDBY.format(words=""), tmp_path / "a", rules=False)
    _render(binary, STANDBY.format(words="link_reset"), tmp_path / "b", rules=False)
    _render(binary, NOTE.format(hw="מחובר ב-USB 2.0 — איטי; לחבר ליציאת USB 3", words="usb2"), tmp_path / "c", rules=False)
    for theme in ("light", "dark"):
        px = _px(tmp_path, "abc", "--clr-danger", theme)
        assert px["b"] <= px["a"] + 40, (theme, px)       # the words alone paint nothing red (the old C turned +200 px red)


@_GUI
def test_the_standby_bay_is_yellow_by_the_verdict_when_the_facts_say_green(tmp_path):
    """Yellow (failed the last clone / a cut write) is the verdict's: the facts are `passed` and no mark, the agent says yellow."""
    binary = _build_gui(tmp_path)
    plain = STANDBY.format(words="")
    _render(binary, plain + "cdisk_rule=sdb|green|1|1||\n", tmp_path / "a", rules=False)
    _render(binary, plain + "cdisk_rule=sdb|yellow|0|1|failed_last|נכשל בשיכפול הקודם — לא מעתיקים ממנו\n", tmp_path / "b", rules=False)
    for theme in ("light", "dark"):
        px = _px(tmp_path, "ab", "--clr-yellow", theme)
        assert px["b"] > px["a"] + 200, (theme, px)


# --- the distribution room ---------------------------------------------------------------------------------------------

@_GUI
def test_a_room_slot_with_no_verdict_is_grey_even_when_its_smart_is_passed(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, ROOM.format(red=""), tmp_path / "a", rules=False)                                       # no verdict at all
    _render(binary, ROOM.format(red="room_drawer_rule=0|S2|green|1|1||\n"), tmp_path / "b", rules=False)      # the server said green
    for theme in ("light", "dark"):
        px = _px(tmp_path, "ab", "--clr-success", theme)
        assert px["b"] > px["a"] + 20, (theme, px)          # green only where the server said so


@_GUI
def test_a_room_slot_is_red_only_because_the_server_said_red(tmp_path):
    """The facts are `passed`; the server's verdict says red (SMART failed): the slot is red. A slot with no verdict is not."""
    binary = _build_gui(tmp_path)
    _render(binary, ROOM.format(red=""), tmp_path / "a", rules=False)
    _render(binary, ROOM.format(red="room_drawer_rule=0|S2|red|0|1|smart_fail|SMART נכשל — לא כותבים על הדיסק הזה\n"), tmp_path / "b", rules=False)
    for theme in ("light", "dark"):
        px = {n: sum(_count(f, "--clr-danger", theme) for f in tmp_path.glob(f"{n}-room*-{theme}.png")) for n in "ab"}
        assert px["b"] > px["a"] + 100, (theme, px)


@_GUI
def test_a_room_slot_says_the_servers_sentence_as_it_came(tmp_path):
    """The sentence comes with the verdict (field 7); the GUI holds no copy -- a sentence it never heard of is still drawn."""
    binary = _build_gui(tmp_path)
    _render(binary, ROOM.format(red=""), tmp_path / "a", rules=False)
    _render(binary, ROOM.format(red="room_drawer_rule=0|S2|yellow|1|0|failed_last|משפט שהשרת אמר ואף אחד אחר לא מכיר\n"),
            tmp_path / "b", rules=False)
    for theme in ("light", "dark"):
        px = _px(tmp_path, "ab", "--clr-yellow", theme)
        assert px["b"] > px["a"] + 100, (theme, px)


# --- the local clone (no server, no brain) ---------------------------------------------------------------------------

def _lc(rule_lines: str, smart="ok", reason="ok") -> str:
    """WORK with drawer 1's SMART set, and ONLY the verdicts given (a lc_rule= for a drive nobody has = "a state that carries verdicts")."""
    out = []
    for line in WORK.splitlines():
        if line.startswith("lc_disk=1|"):
            f = line.split("|"); f[5], f[6] = smart, reason; line = "|".join(f)
        out.append(line)
    return "\n".join(out) + "\n" + rule_lines


def test_the_local_clone_follows_the_agents_verdict_not_the_facts(seq_driver, tmp_path):
    """Drawer 1's SMART is ok -- and the agent says red: it starts skipped, and "יעד" does nothing."""
    state = _lc("lc_rule=sda|red|1|0|smart_fail|SMART נכשל\n")
    got, out = _seq(seq_driver, tmp_path, state, (0, LCR_TARGET), state)
    assert got[0]["0"] == got[-1]["0"] == str(LCR_SKIP), out


def test_the_local_clone_with_red_facts_and_no_verdict_is_not_red(seq_driver, tmp_path):
    """SMART failed in the facts, but no verdict was delivered: the GUI works nothing out -- "not checked" is a target, as the table's own row says."""
    state = _lc("lc_rule=nowhere|grey|1|1||\n", smart="fail", reason="health_failed")
    got, out = _seq(seq_driver, tmp_path, state)
    assert got[0]["0"] == str(LCR_TARGET), out
