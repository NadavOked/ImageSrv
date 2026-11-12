"""#2848, Nadav's follow-up decisions (11/11), on the cloner's local clone screen.

1. "SMART חצי תקין" (orange) starts as a TARGET, like green -- not "דלג", and no SMART window
   stands between the click on "יעד" and the target.
2. A bad SATA link (``link_slow``, ``link_reset``, ``drawer_gone`` -- hwissues.sh's words) is RED:
   never a target, the reason on the card, still a source. The agent carries the words in
   ``lc_disk=`` field 19 (lcgui.sh); field 17 stays the sentence.

The screen through the production sources (the drivers of ``test_lc_source_target_2848``):
roles after a reload and after clicks, the click targets drawn, and the words on the card.
"""

import json

from test_cloner_gui import AGENT, PRELUDE, env, posix
from test_drawer_map_gui_1495 import JQ
from test_lc_card_fit_2205 import ROLE_BASE, ROLE_END, fit_driver  # noqa: F401 -- fixture
from test_lc_source_target_2848 import LCR_SKIP, LCR_SOURCE, LCR_TARGET, WORK, _draw, _seq, seq_driver  # noqa: F401
from test_native_local_clone import pytestmark  # noqa: F401
from test_slot_names_704_gui import sh

SATA_RED = "בעיית SATA"   # #2848: the rule (disk_rule.c) -- on the chip and in the sentence


def _with(disk: int, smart="ok", reason="ok", words=""):
    """WORK with drawer ``disk`` (1-3) changed: its SMART and its hw words (field 19)."""
    out = []
    for line in WORK.splitlines():
        if line.startswith(f"lc_disk={disk}|"):
            f = line.split("|")
            f[5], f[6] = smart, reason
            line = "|".join(f) + f"||{words}"   # WORK ends at field 17 (the hw note): + name "" + words
        out.append(line)
    return "\n".join(out) + "\n"


def test_an_orange_drawer_starts_as_a_target_and_takes_the_click(seq_driver, tmp_path):
    state = _with(1, "warn", "wear_leveling")
    got, out = _seq(seq_driver, tmp_path, state, (0, LCR_SKIP), state, (0, LCR_TARGET), state)
    assert got[0]["0"] == str(LCR_TARGET), out          # the proposal: a target, like green
    assert got[-1]["0"] == str(LCR_TARGET), out          # "יעד" again after "דלג": no window in between


def test_a_red_smart_drawer_still_starts_skipped(seq_driver, tmp_path):
    """The guard: only orange moved -- red (SMART failed) is never a target."""
    state = _with(1, "fail", "health_failed")
    got, out = _seq(seq_driver, tmp_path, state, (0, LCR_TARGET), state)
    assert got[0]["0"] == got[-1]["0"] == str(LCR_SKIP), out


def test_a_slow_sata_link_is_red_skipped_and_still_a_source(seq_driver, tmp_path):
    state = _with(1, words="link_slow")
    got, out = _seq(seq_driver, tmp_path, state, (0, LCR_TARGET), state, (0, LCR_SOURCE), state)
    assert got[0]["0"] == str(LCR_SKIP), out            # never proposed as a target
    assert got[1]["0"] == str(LCR_SKIP), out            # "יעד" does nothing
    assert got[-1]["0"] == str(LCR_SOURCE), out          # ...and it may be the source


def test_the_red_drawer_greys_target_and_writes_the_reason(fit_driver, tmp_path, monkeypatch):
    import test_lc_source_target_2848 as base
    monkeypatch.setattr(base, "WORK", _with(1, words="link_reset drawer_gone"))
    roles, texts = _draw(fit_driver, tmp_path)
    assert LCR_TARGET not in roles[0] and LCR_SOURCE in roles[0], roles
    assert any(SATA_RED in t for t in texts), texts


def test_usb2_and_icrc_stay_notes(seq_driver, tmp_path):
    """The guard: words that are not a SATA link problem do not make it red."""
    state = _with(1, words="usb2 icrc_logged")
    got, out = _seq(seq_driver, tmp_path, state)
    assert got[0]["0"] == str(LCR_TARGET), out


@JQ
def test_the_agent_sends_the_hw_words_as_field_19(tmp_path):
    run = tmp_path / "run"; (run / "smart").mkdir(parents=True)
    gui = tmp_path / "gui"; gui.mkdir()
    lc = tmp_path / "lc"; (lc / "inv").mkdir(parents=True)
    (lc / "screen").write_text("local\n", newline="\n")
    (run / "response.json").write_text(json.dumps({}), encoding="utf-8", newline="\n")
    for port, dev in ((1, "sda"), (2, "sdb")):
        (lc / "inv" / f"{dev}.line").write_text(
            f"{port}|{dev}|M|S{port}|1000|ok|ok|Windows|1|1|1000|1|\n", encoding="utf-8", newline="\n")
    (run / "smart" / "sda.hw").write_text("link_slow\nlink_reset\n", newline="\n")
    out = sh(env(run, gui) + f"export LC={posix(lc)!r} SMART_KB={posix(AGENT / 'lib' / 'data')!r}; " + PRELUDE
             + f". {posix(AGENT)}/lib/smart/smartjudge.sh; . {posix(AGENT)}/lib/smart/hwissues.sh; "
             + f". {posix(AGENT)}/lib/localclone/lcgui.sh; lc_gui_state")
    assert out.returncode == 0, out.stderr
    disks = {l.split("|")[1]: l.split("|") for l in (gui / "state").read_text(encoding="utf-8").splitlines()
             if l.startswith("lc_disk=")}
    assert len(disks["sda"]) == 19 and disks["sda"][18].split() == ["link_slow", "link_reset"], disks["sda"]
    assert "כבל או מגירה" in disks["sda"][16], disks["sda"]   # field 17: still the sentence
    assert disks["sdb"][18].strip() == "", disks["sdb"]


# --- #2848 (Nadav 11/11, the table's yellow row): failed the last clone / a cut write -- a TARGET, never a SOURCE

def _mark(disk: int, mark: str) -> str:
    """WORK with drawer ``disk``'s field 16 (the #1798 write mark) set."""
    out = []
    for line in WORK.splitlines():
        if line.startswith(f"lc_disk={disk}|"):
            f = line.split("|"); f[15] = mark; line = "|".join(f)
        out.append(line)
    return "\n".join(out) + "\n"


def test_a_yellow_drawer_is_a_target_and_never_the_source(seq_driver, tmp_path):
    """Drawer 3 -- the proposed source -- failed the last clone: no source is proposed in its place (#1874),
    it starts as a TARGET, and "מקור" on it does nothing."""
    state = _with(3, "failed_last", "cable")
    got, out = _seq(seq_driver, tmp_path, state, (2, LCR_SOURCE), state)
    assert got[0]["2"] == str(LCR_TARGET), out           # yellow: a target from the start
    assert got[-1]["2"] == str(LCR_TARGET), out          # the source click did nothing
    assert str(LCR_SOURCE) not in got[-1].values(), out


def test_a_cut_write_is_a_target_and_never_the_source(seq_driver, tmp_path):
    state = _mark(3, "incomplete")
    got, out = _seq(seq_driver, tmp_path, state, (2, LCR_SOURCE), state)
    assert got[0]["2"] == got[-1]["2"] == str(LCR_TARGET), out
    assert str(LCR_SOURCE) not in got[-1].values(), out


def test_the_yellow_drawer_greys_source_and_keeps_target(fit_driver, tmp_path, monkeypatch):
    import test_lc_source_target_2848 as base
    monkeypatch.setattr(base, "WORK", _with(1, "failed_last", "cable"))
    roles, _texts = _draw(fit_driver, tmp_path)
    assert LCR_SOURCE not in roles[0] and LCR_TARGET in roles[0], roles
