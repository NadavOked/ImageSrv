"""#2980 -- one name for a disk on every screen, and "זמן שעבר" on the cloner.

Nadav, 12/11 (v0.92.0, metal at work): one round on two screens. The build machine said "דיסק 1" / "דיסק 3"
and "זמן שעבר 07:13"; the cloner said "SATA 0" / "SATA 2" and had no time-passed tile at all. A restore on
the build machine said "SATA 3 (לא ממופה)" -- its one disk sits on ata4, outside the default mapping.
mockup-gui-unified-1112 (approved 12/11, decisions 1, 3 and 8): a drawer is "דיסק N"; the build machine's
one disk is "הדיסק של המחשב" (Nadav 04/11: build and student machines have one disk); the cloner has a
fourth tile, "זמן שעבר", in the room screen's mm:ss.

The time passed counts from the same start on both screens: the wave's started_at on the server, which is
what the room screen's round.elapsed_s counts from (room._wave_pace).

Layers, each against the real thing:

* the server: a running room wave's hello carries session.started_at (an epoch);
* the agent: clonergui.sh turns it into wave_elapsed=; on a direct send directgui.sh takes the room's
  round.elapsed_s; srgui.sh names the build machine's disk;
* the GUI (C, built on the Testrunner): every word the cloner round, the cloner's wait and the build
  machine's capture picker lay out.

Negative control: with the production files of this change put back as they are on origin/main, every
test here fails (see the PR).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, PORTS, PRELUDE, _build_gui, _pkgconfig, env, posix, sh, target
from test_drawer_card_2030 import IDLE, words
from test_hello_eta_1843 import _open, _running, _started, clocked  # noqa: F401 -- clocked is a fixture

AGENT = Path(__file__).resolve().parent.parent / "agent"
JQ = requires_native(("bash", BASH), ("jq", shutil.which("jq")), why="the agent reads the hello with jq")
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui is built on Linux only",
)


# --- the server: session.started_at -----------------------------------------------------------------


def test_a_running_wave_tells_the_cloner_when_it_started(clocked):
    """The start the room screen counts its elapsed_s from -- the same wave, the same second."""
    session = _running(clocked)["session"]
    assert session["started_at"] == _started(clocked), session
    assert isinstance(session["started_at"], int)


def test_an_open_wave_has_no_start_to_tell(clocked):
    session = _open(clocked, 4)["session"]
    assert session["state"] == "open" and "started_at" not in session, session


def test_a_start_that_cannot_be_read_never_breaks_the_hello(clocked, monkeypatch):
    """A number on a screen: the machine that is about to write gets its round all the same."""
    from server.agent import hello_eta
    monkeypatch.setattr(hello_eta, "_epoch", lambda _stamp: 1 / 0)
    session = _running(clocked)["session"]
    assert session["state"] == "running" and session["id"] and "started_at" not in session, session


# --- the agent: wave_elapsed= -----------------------------------------------------------------------


def _cloner_records(tmp_path: Path, hello: dict) -> list[str]:
    run, gui = tmp_path / "run", tmp_path / "gui"
    run.mkdir(); gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (run / "response.json").write_text(json.dumps(hello), newline="\n")
    target(run, "sda", "writing", 100, 40, "5")
    out = sh(env(run, gui) + PRELUDE + f". {posix(AGENT)}/lib/gui/directgui.sh; " + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("wave_elapsed=")]


@JQ
def test_the_cloner_counts_from_the_waves_start(tmp_path):
    lines = _cloner_records(tmp_path, {"schema": 1, "session": {"id": "ses_1", "state": "running",
                                                                "started_at": int(time.time()) - 336}})
    assert len(lines) == 1, lines
    assert 336 <= int(lines[0].split("=", 1)[1]) <= 346, lines     # the seconds this run took on top


@JQ
@pytest.mark.parametrize("session", [
    {"id": "ses_1", "state": "running"},                            # an older server: no field
    {"id": "ses_1", "state": "running", "started_at": None},
    {"id": "ses_1", "state": "running", "started_at": "13:58"},     # not an epoch
    {"id": "ses_1", "state": "running", "started_at": 4102444800},  # ahead of this clock (2100): never a negative time
    None,
])
def test_no_start_is_no_record(tmp_path, session):
    """Not said = no line: the screen says "—", never 00:00 (principle 5)."""
    assert _cloner_records(tmp_path, {"schema": 1, "session": session}) == []


def _direct_records(tmp_path: Path, round_: dict) -> list[str]:
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "station.json").write_text(json.dumps({"known": True, "task": {"type": "direct_send", "state": "running"}}),
                                      encoding="utf-8", newline="\n")
    (gui / "mode").write_text("direct\n", newline="\n")
    (gui / "room.json").write_text(json.dumps({"machines": [], "round": round_}), encoding="utf-8", newline="\n")
    out = sh(f"export GUI_DIR={posix(gui)!r}; gui_get() {{ return 0; }}; "
             f". {posix(AGENT)}/lib/gui/directgui.sh; direct_gui_records")
    assert out.returncode == 0, out.stderr
    return [l for l in out.stdout.splitlines() if l.startswith("wave_elapsed=")]


@JQ
def test_the_direct_send_takes_the_room_screens_own_number(tmp_path):
    """On a direct send the build machine draws the cloner's screen: the same tile, from round.elapsed_s."""
    assert _direct_records(tmp_path, {"image_name": "Win11-Lab", "elapsed_s": 433}) == ["wave_elapsed=433"]


@JQ
@pytest.mark.parametrize("elapsed", [None, "07:13", -5])
def test_the_direct_send_says_nothing_it_was_not_told(tmp_path, elapsed):
    assert _direct_records(tmp_path, {"image_name": "Win11-Lab", "elapsed_s": elapsed}) == []


def _restore_label(tmp_path: Path, role: str, drawer: str) -> str:
    """sr_disk_label, the start of the build machine's restore line ("<disk> · מחיצה 2 מתוך 4 · ...")."""
    run = tmp_path / f"run-{role}"; run.mkdir(parents=True, exist_ok=True)
    got = run / "label"
    out = sh(f"export RUN_DIR={posix(run)!r} D_ROLE={role}; . {posix(AGENT)}/lib/core/common.sh; "
             f". {posix(AGENT)}/lib/gui/srgui.sh; disk_port() {{ echo 4; }}; drawer_slots_refresh() {{ :; }}; "
             f"disk_drawer() {{ echo {drawer!r}; }}; sr_disk_label sda > {posix(got)!r}")
    assert out.returncode == 0, out.stderr
    return got.read_text(encoding="utf-8")


@requires_native(("bash", BASH))
@pytest.mark.parametrize("role", ["build", "classroom"])
@pytest.mark.parametrize("drawer", ["0|SATA 3", "1|SATA 0"])
def test_a_build_or_student_machines_disk_is_the_computers_disk(tmp_path, role, drawer):
    """12/11, HP-BUILD-1: its disk on ata4 is outside the default mapping -- "SATA 3 (לא ממופה)". One disk,
    no drawer: "הדיסק של המחשב", mapped or not."""
    assert _restore_label(tmp_path, role, drawer) == "הדיסק של המחשב"


@requires_native(("bash", BASH))
def test_a_cloner_still_names_the_drawer_by_the_mapping(tmp_path):
    assert _restore_label(tmp_path, "cloner", "2|SATA 1") == "דיסק 2"
    assert _restore_label(tmp_path / "x", "cloner", "0|SATA 3") == "SATA 3 (לא ממופה)"


# --- the GUI: every word it lays out -----------------------------------------------------------------

G = 1_000_000_000
#: the round Nadav photographed: one cloner writing drawers 1 and 3 (SATA 0 and SATA 2)
ROUND = (
    "cloner=Office 2024\n"
    "drawer_count=3\n"
    "cdrawer_slot=1|SATA 0|\ncdrawer_slot=2|SATA 1|\ncdrawer_slot=3|SATA 2|\n"
    f"drawer=1|sda|writing|{int(22.2 * G)}|{83 * G}||67000000|900|1|SATA 0\n"
    f"drawer=3|sdc|writing|{int(22.2 * G)}|{83 * G}||67000000|900|3|SATA 2\n"
    "cused=sda|256060514304|-1\ncused=sdc|512110190592|-1\n"
    "message=x|y\n"   # the --png run's "message" card needs one, as test_drawer_card_2030's ROUND
)
DONE = ROUND.replace("|writing|", "|done|").replace(f"|{int(22.2 * G)}|", f"|{83 * G}|")


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui2980"))


@GUI_BUILD
def test_the_cloner_round_names_its_drawers_as_the_build_machine_and_shows_the_time_passed(binary, tmp_path):
    w = words(binary, tmp_path, ROUND + "wave_elapsed=336\nupdated=1792249500\n", "cloner")
    assert "דיסק 1" in w and "דיסק 3" in w, w
    assert not [l for l in w if l in ("SATA 0", "SATA 2")], "the connection as the drawer's name"
    assert "זמן שעבר" in w and "05:36" in w, w                     # 336 s, the room screen's "%02d:%02d"
    for tile in ("מהירות", "התקבלו"):                               # the three tiles it had stay
        assert tile in w, (tile, w)


@GUI_BUILD
def test_a_time_not_said_is_a_dash_never_zero(binary, tmp_path):
    w = words(binary, tmp_path, ROUND + "updated=1792249500\n", "cloner")
    assert "זמן שעבר" in w and "—" in w, w
    assert "00:00" not in w, w


@GUI_BUILD
def test_the_time_does_not_keep_counting_after_the_write(binary, tmp_path):
    """The hello's start stays in response.json after the end: the tile says "—", as the room's once the wave
    stops running -- never a number that grows while the machine waits to power off."""
    w = words(binary, tmp_path, DONE + "wave_elapsed=900\nupdated=1792249500\n", "cloner")
    assert "זמן שעבר" in w and "15:00" not in w, w


@GUI_BUILD
def test_the_cloners_wait_names_the_drawers_as_the_round(binary, tmp_path):
    w = words(binary, tmp_path, IDLE, "standby")
    assert "דיסק 1" in w and "דיסק 2" in w and "דיסק 3" in w, w
    assert not [l for l in w if l in ("SATA 0", "SATA 1", "SATA 2")], w


@GUI_BUILD
def test_the_build_machines_capture_picker_says_the_computers_disk(binary, tmp_path):
    """The picker's sample disks (no --state): never "דיסק N" or "<conn> (לא ממופה)" for the build machine."""
    w = words(binary, tmp_path, None, "pick")
    assert [l for l in w if l.startswith("הדיסק של המחשב · ")], w
    assert not [l for l in w if l.startswith("דיסק ") or "(לא ממופה)" in l], w


@GUI_BUILD
@pytest.mark.parametrize("card", ["pick-smart", "restore-smart"])
def test_the_smart_window_names_the_computers_disk(binary, tmp_path, card):
    w = words(binary, tmp_path, None, card)
    assert "הדיסק של המחשב" in w, w                                # the window's "דיסק" row, isolates stripped
    assert not [l for l in w if l == "דיסק 1" or "(לא ממופה)" in l], w


@GUI_BUILD
def test_the_restore_screen_says_the_computers_disk(binary, tmp_path):
    w = words(binary, tmp_path, None, "restore")
    assert "הדיסק של המחשב" in w and "הדיסק המקומי" not in w, w
