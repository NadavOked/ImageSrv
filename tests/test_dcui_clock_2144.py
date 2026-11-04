"""#2144 (mockup 2144-time, Nadav 04/11): the server screen says when its
clock is not synchronised.

* ``data.clock_state`` -- chrony's own verdict (the console's, ``timesync.status``)
  through the screen's runner: synced / unsynced / **unchecked** -- a chronyc
  that does not answer is never "synced" (principle 5).
* ``view`` -- an orange problem card and an orange tag beside the header clock,
  after a boot grace; and the rule Nadav set: **the header is red only for a
  blocking problem** (a red card). Orange cards alone leave it orange.
* ``native-gui`` -- the ``hdr`` record carries the tag, and the hero draws an
  orange alert with its problems (the rendering itself: Linux lab).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from server.db import connect, set_setting
from server.dcui import samples, view
from server.dcui.data import (AUTO_TIME_SOURCE, CLOCK_SYNCED, CLOCK_UNCHECKED, CLOCK_UNSYNCED,
                              CommandResult, ServiceState, clock_state)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

TRACKING_OK = ("Reference ID    : 0A2C00FA (10.44.0.250)\nStratum         : 3\n"
               "System time     : 0.000003127 seconds fast of NTP time\n"
               "Leap status     : Normal\n")
SOURCES_OK = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
              "===============================================================\n"
              "^* 10.44.0.250                   2   6   377    12   +3us[ +5us] +/-  20ms\n")
TRACKING_NONE = ("Reference ID    : 00000000 ()\nStratum         : 0\n"
                 "System time     : 0.000000000 seconds fast of NTP time\n"
                 "Leap status     : Not synchronised\n")
SOURCES_NONE = ("MS Name/IP address         Stratum Poll Reach LastRx Last sample\n"
                "===============================================================\n"
                "^? 10.44.0.250                   0   6     0     -     +0ns[   +0ns] +/-    0ns\n")


def _conn(tmp_path: Path, servers: str | None = None) -> sqlite3.Connection:
    conn = connect(tmp_path / "imagesrv.db")
    if servers is not None:
        set_setting(conn, "ntp_servers", servers)
    return conn


def _chronyc(tracking: CommandResult, sources: CommandResult):
    calls = []

    def run(argv):
        calls.append(argv)
        if argv == ["chronyc", "-n", "tracking"]:
            return tracking
        if argv == ["chronyc", "-n", "sources"]:
            return sources
        return CommandResult(1, "", "unexpected")
    return run, calls


# ---- the data: chrony's answer, three states -------------------------------------

def test_a_selected_source_and_a_normal_leap_is_synced_with_the_configured_server(tmp_path):
    run, calls = _chronyc(CommandResult(0, TRACKING_OK), CommandResult(0, SOURCES_OK))
    assert clock_state(run, _conn(tmp_path, "10.44.0.250")) == (CLOCK_SYNCED, "10.44.0.250")
    assert ["chronyc", "-n", "tracking"] in calls and ["chronyc", "-n", "sources"] in calls


def test_no_selected_source_is_unsynced_and_automatic_names_the_debian_pool(tmp_path):
    run, _ = _chronyc(CommandResult(0, TRACKING_NONE), CommandResult(0, SOURCES_NONE))
    assert clock_state(run, _conn(tmp_path)) == (CLOCK_UNSYNCED, AUTO_TIME_SOURCE)


@pytest.mark.parametrize("tracking,sources", [
    (CommandResult(None, "", "No such file or directory: 'chronyc'"), CommandResult(0, SOURCES_OK)),
    (CommandResult(0, TRACKING_OK), CommandResult(1, "", "506 Cannot talk to daemon")),
    (CommandResult(1, "", "506 Cannot talk to daemon"), CommandResult(1, "", "506")),
])
def test_a_chronyc_that_does_not_answer_is_unchecked_never_synced(tmp_path, tracking, sources):
    """Principle 5: "could not check" is its own state -- not "synced", and not
    "unsynced" either (nobody saw the clock)."""
    run, _ = _chronyc(tracking, sources)
    assert clock_state(run, _conn(tmp_path, "10.44.0.250"))[0] == CLOCK_UNCHECKED


def test_an_invalid_stored_setting_is_shown_as_unknown_not_as_automatic(tmp_path):
    run, _ = _chronyc(CommandResult(0, TRACKING_NONE), CommandResult(0, SOURCES_NONE))
    assert clock_state(run, _conn(tmp_path, "bad host!")) == (CLOCK_UNSYNCED, "unknown")


# ---- the view: the card, the tag, the grace --------------------------------------

def _frame(snapshot, extras) -> str:
    runtime = samples._runtime(snapshot, extras)
    return view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, samples.TODAY, 47)


def _records(frame: str) -> list[list[str]]:
    return [line.split("\t") for line in frame.rstrip("\n").split("\n")]


def _first(frame: str, kind: str) -> list[str]:
    return next(rec for rec in _records(frame) if rec[0] == kind)


def _probs(frame: str) -> list[list[str]]:
    return [rec for rec in _records(frame) if rec[0] == "prob"]


UNSYNCED = replace(samples.PRIMARY, clock=CLOCK_UNSYNCED, clock_server="10.44.0.250")
LATER = view.Extras(uptime_seconds=240, secondaries=("HAIFA",))


def test_an_unsynced_clock_is_an_orange_card_with_the_mockups_words():
    frame = _frame(UNSYNCED, LATER)
    (prob,) = _probs(frame)
    assert prob[1] == "warn" and prob[3] == "השעון של השרת לא מסונכרן"
    assert prob[4] == ("שרת הזמן (⁨10.44.0.250⁩) לא ענה מאז האתחול. השעה עלולה להיות "
                       "שגויה ביומן, בזמני הסבבים ובבדיקת תוקף התעודה.")
    assert prob[5] == ("<b>מה לעשות:</b> לבדוק שהרשת מאפשרת NTP (UDP 123) אל שרת הזמן, או לשנות "
                       "בקונסולה: הגדרות ← מקור השעה.")
    hero = _first(frame, "hero")
    assert hero[2] == "השרת דורש טיפול · בעיה אחת"
    assert hero[3] == "השעה בשרת <b>עלולה להיות שגויה</b> עד שהשעון יסתנכרן."
    assert _first(frame, "hdr")[7] == "השעון לא מסונכרן"


def test_an_unchecked_clock_has_its_own_card_and_tag():
    frame = _frame(replace(UNSYNCED, clock=CLOCK_UNCHECKED), LATER)
    (prob,) = _probs(frame)
    assert prob[1] == "warn" and prob[3] == "לא הצלחנו לבדוק את סנכרון השעון"
    assert prob[4] == "chrony לא החזיר תשובה — לא ידוע אם השעה בשרת נכונה."
    assert prob[5] == ("<b>מה לעשות:</b> רענון (F5); אם זה חוזר — בקונסולה: הגדרות ← מצב השעון "
                       "← בדוק עכשיו.")
    assert _first(frame, "hero")[3] == "הבעיות מסודרות לפי חומרה — מתחילים מהראשונה."
    assert _first(frame, "hdr")[7] == "השעון לא נבדק"


def test_a_synced_clock_has_no_card_and_no_tag():
    frame = _frame(replace(UNSYNCED, clock=CLOCK_SYNCED), LATER)
    assert not _probs(frame)
    assert _first(frame, "hero")[1:3] == ["ok", "השרת תקין"]
    assert _first(frame, "hdr")[7] == ""


@pytest.mark.parametrize("state", [CLOCK_UNSYNCED, CLOCK_UNCHECKED])
def test_within_the_boot_grace_the_clock_is_not_yet_a_problem(state):
    """chrony takes seconds after boot; #2155's precedent -- no orange flash on
    every boot. After the grace, it is a problem; unknown uptime has none."""
    snapshot = replace(UNSYNCED, clock=state)
    early = view.Extras(uptime_seconds=view.CLOCK_SYNC_GRACE_S - 1)
    assert not _probs(_frame(snapshot, early)) and _first(_frame(snapshot, early), "hdr")[7] == ""
    due = view.Extras(uptime_seconds=view.CLOCK_SYNC_GRACE_S)
    assert len(_probs(_frame(snapshot, due))) == 1
    unknown = view.Extras(uptime_seconds=None)
    assert len(_probs(_frame(snapshot, unknown))) == 1


def test_a_frame_without_a_clock_shows_none():
    """"" = this snapshot carries no clock (pre-install, hand-made samples):
    not a problem and not a tag. collect_snapshot always sets one of three."""
    frame = _frame(samples.PRIMARY, LATER)
    assert not _probs(frame) and _first(frame, "hdr")[7] == ""


def test_collect_snapshot_always_carries_the_clock():
    text = (REPO / "server" / "dcui" / "data.py").read_text(encoding="utf-8")
    body = text[text.index("def collect_snapshot("):text.index("def demo_snapshot(")]
    assert "clock, clock_server = clock_state(runner, conn)" in body
    assert "clock=clock, clock_server=clock_server," in body


# ---- Nadav 04/11: orange header unless a problem blocks --------------------------

def test_only_non_blocking_problems_make_the_header_and_status_orange():
    frame = _frame(UNSYNCED, LATER)
    assert _first(frame, "hero")[1] == "warn"
    assert _first(frame, "sb")[1:3] == ["warn", "דורש טיפול · 1"]


def test_a_blocking_problem_beside_the_clock_keeps_the_header_red_and_first():
    down = replace(UNSYNCED, services=dict(samples.UP) | {"dnsmasq": ServiceState("failed", "enabled")})
    frame = _frame(down, LATER)
    assert _first(frame, "hero")[1] == "bad"
    assert _first(frame, "sb")[1:3] == ["bad", "דורש טיפול · 2"]
    assert [p[1] for p in _probs(frame)] == ["bad", "warn"]
    assert _probs(frame)[1][3] == "השעון של השרת לא מסונכרן"


def test_the_existing_orange_problems_alone_are_orange_too():
    """The rule is the cards' own colour, not a list kept beside them: a
    certificate that expires in 9 days is orange, so the header is orange."""
    soon = replace(samples.PRIMARY, certificate_not_after="Oct 21 12:00:00 2026 GMT")
    frame = _frame(soon, LATER)
    assert [p[1] for p in _probs(frame)] == ["warn"]
    assert _first(frame, "hero")[1] == "warn"


@pytest.mark.parametrize("sev,expected", [("bad", True), ("warn", False)])
def test_blocking_is_exactly_a_red_card(sev, expected):
    assert view.blocking([view.Problem(sev, "t", "w", "")]) is expected
    assert view.blocking([]) is False


def test_every_red_card_says_what_it_stops_or_that_it_could_not_be_read():
    """The definition, in the code: every "bad" Problem in problems() either
    carries a consequence (what does not work until it is fixed) or is a
    certificate the browser refuses / that could not be read."""
    text = (REPO / "server" / "dcui" / "view.py").read_text(encoding="utf-8")
    body = text[text.index("def problems("):text.index("def uptime_text(")]
    reds = re.findall(r'Problem\(\s*"bad", (f?"[^"]+")', body)
    assert len(reds) == 9, reds   # the nine red cards of 04/11
    for title in reds:
        block = body[body.index(title):][:900].split("found.append", 1)[0]
        assert ("לא תיפתח" in block or "לא נגישה" in block or "לא יעבוד" in block
                or "תעודת האבטחה" in title), title


# ---- the graphical screen: the record and its drawing -----------------------------

def test_the_mockup_states_are_samples_that_the_native_tests_render():
    frames = samples.frames()
    unsynced, unchecked = frames["16-clock-unsynced"], frames["16b-clock-unchecked"]
    assert _first(unsynced, "hdr")[7] == "השעון לא מסונכרן"
    assert _first(unchecked, "hdr")[7] == "השעון לא נבדק"
    assert _first(unsynced, "hero")[1] == "warn" and _first(unchecked, "hero")[1] == "warn"


def test_the_gui_reads_the_tag_and_draws_it_beside_the_clock():
    dcui_c = (SRC / "dcui.c").read_text(encoding="utf-8")
    assert "COPY(f->clock_chip, c[7]);" in dcui_c
    assert "char clock_chip[64];" in (SRC / "dcui.h").read_text(encoding="utf-8")
    draw = (SRC / "screens_dcui.c").read_text(encoding="utf-8")
    header = draw[draw.index("static void header("):draw.index("static Rgb dot_color(")]
    assert "if (f->clock_chip[0])" in header and "g->t->warn" in header
    # after the clock, before the version -- the mockup's order
    assert header.index("f->clock_chip[0]") > header.index("Text clock = mono(")
    assert header.index("f->clock_chip[0]") < header.index('rtl(g, 12, 400, "גרסה", 0)')


def test_an_orange_hero_with_problems_draws_them_in_the_alert_layout():
    draw = (SRC / "screens_dcui.c").read_text(encoding="utf-8")
    hero = draw[draw.index("static double hero("):draw.index("#define TILE_PAD_B")]
    assert 'const int bad = red || (!strcmp(f->hero_kind, "warn") && f->nprobs);' in hero
    assert "red ? t->danger : bad ? t->warn : t->hair" in hero
    assert "red ? t->danger_soft : bad ? t->warning_soft : t->surface" in hero
    assert 'bad && !red ? "alert" : f->hero_kind' in hero


def test_the_contract_documents_the_tag_and_the_orange_hero():
    doc = (REPO / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    assert "| `hdr` | role, host, url, version, clock, admin (ריק = אין שבב מנהל), clock_chip" in doc
    assert "| `hero` | kind (`ok`\\|`bad`\\|`warn`\\|`wait`)" in doc
