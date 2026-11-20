"""#3663 (אפיק #3653, נדב 18/11) A10 + G2: the brain decides whether to run the live cable test and whether the cloner draws the
"slow link" banner; the agent and the GUI only execute and show (`server/brain/linkverdict.py`, rules K-CABLE and K-NICBANNER).

What is checked:

- **The behaviour is the one from before #3663.** `old_cable_ran` and `old_banner` below are the literal copies of the two decisions as they sat in
  `agent/lib/hw/cablelive.sh` and `native-gui/src/screens_standby.c` at the base (`lab/vm-1114f`, 13828c0e4), as an oracle in the test only: a grid of speeds,
  counters and states, and the verdict must be the same.
- **The decision is recorded** in `brain_decisions` (shadow, enforce; `off` decides and does not record), once per change of a machine's verdict -- the hello comes
  every few seconds -- and the row is closed by what came after it.
- **No decision = nothing is done**: a rule that raised leaves its key out of the answer, one UNDECIDED row; a hello without `nic_link` gets no `link_verdict`.
- **The hello answer carries it**, end to end, with the server's knowledge that the agent does not have: a room round live anywhere is "not now".
- **The agent reports facts and holds no verdict** (`nic_link_json`: `carrier_changes`, `carrier_first`, `cable_tested`; `nic_link_record`: the flag), and
  the GUI draws the flag and nothing else (tests/test_nic_link_banner_3277.py, tests/test_cable_test_1968.py).

**Negative control:** the base has no `linkverdict.py` (`linkverdict = None` below: the behaviour is checked, not the import), no `link_verdict` in the answer,
an agent that decides by itself and a GUI that draws at 100.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from native import requires_native
from server import db
from server.brain import linkwarn, memory, veto

try:                                    # negative control: without the module the BEHAVIOUR is checked, not the import
    from server.brain import linkverdict
except ImportError:                     # pragma: no cover
    linkverdict = None

MAC = "aa:bb:cc:00:00:21"
CLONER = {"iface": "eth0", "speed_mbps": 1000, "max_mbps": 1000, "carrier_changes": 2, "carrier_first": 2, "cable_tested": False}


@pytest.fixture(autouse=True)
def _forget():
    """The verdicts a process remembers (one row per CHANGE, not per hello) must not leak from a test to the next."""
    veto.forget()
    if linkverdict is not None:
        linkverdict.forget()
    yield
    veto.forget()
    if linkverdict is not None:
        linkverdict.forget()


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "brain.db")


def _need() -> None:
    assert linkverdict is not None, "server/brain/linkverdict.py is missing: nothing decides the cable test and the banner"


# --- the oracles: the decisions as they were -----------------------------------------------------------------

def old_cable_ran(*, busy, speed, changes, first, tested) -> bool:
    """cablelive.sh at the base: `[ role cloner ] && [ state != open ] && [ task null ] || return`, `[ -e cable-live.json ] && return`, slow =
    0 < speed < CABLE_TEST_MIN_MBPS (1000), dropped = carrier_changes > the first value this boot read (a first value that could not be stored
    was the current one, so nothing rose)."""
    if busy or tested:
        return False
    slow = speed is not None and 0 < speed < 1000
    base = first if first is not None else changes
    dropped = changes is not None and changes > base
    return slow or dropped


def old_banner(speed, top) -> bool:
    """screens_standby.c sb_nic_banner at the base: `nic_mbps <= 0 || !(nic_mbps <= 100 || (nic_max > 0 && nic_mbps < nic_max))` -> no banner."""
    return speed is not None and speed > 0 and (speed <= 100 or (top is not None and top > 0 and speed < top))


SPEEDS = (None, 0, 10, 100, 101, 999, 1000, 2500)
TOPS = (None, 0, 100, 1000, 2500, 10000)
COUNTS = (None, 0, 1, 2, 7)


def test_the_cable_verdict_is_the_old_agent_decision_over_the_whole_grid():
    _need()
    seen = {"run": 0, "no": 0}
    for busy, speed, changes, first, tested in itertools.product((False, True), SPEEDS, COUNTS, COUNTS, (False, True)):
        v = linkverdict.judge_cable(busy=busy, speed=speed, changes=changes, first=first, tested=tested)
        want = old_cable_ran(busy=busy, speed=speed, changes=changes, first=first, tested=tested)
        assert (v.verdict == linkverdict.RUN) is want, (busy, speed, changes, first, tested, v)
        seen["run" if want else "no"] += 1
    assert seen["run"] > 100 and seen["no"] > 100, seen       # the grid has both answers in it


def test_the_banner_verdict_is_the_old_gui_decision_over_the_whole_grid():
    _need()
    shown = 0
    for speed, top in itertools.product(SPEEDS, TOPS):
        v = linkverdict.judge_banner(speed, top)
        assert (v.verdict == linkverdict.SHOW) is old_banner(speed, top), (speed, top, v)
        shown += v.verdict == linkverdict.SHOW
    assert 5 < shown < len(SPEEDS) * len(TOPS)


def test_the_banner_threshold_is_the_one_of_the_slow_link_warning():
    """One 100 for the banner and for K3's warning before the wave: `linkwarn.SLOW_MBPS`, no copy in the new module."""
    _need()
    assert linkwarn.link_low(linkwarn.SLOW_MBPS, None) and not linkwarn.link_low(linkwarn.SLOW_MBPS + 1, None)
    assert linkwarn.link_low(1000, 2500) and not linkwarn.link_low(1000, 1000) and not linkwarn.link_low(1000, None)
    text = Path(linkverdict.__file__).read_text(encoding="utf-8")
    assert "<= 100" not in text and "SLOW_MBPS = " not in text


def test_what_was_not_read_is_not_healthy():
    """Principle 5: no counter / no speed is `NOT_MEASURED` (a drop that was not checked), never `HEALTHY` -- and a slow link is still tested without it."""
    _need()
    ok = dict(busy=False, tested=False)
    assert linkverdict.judge_cable(speed=1000, changes=None, first=None, **ok).verdict == linkverdict.NOT_MEASURED
    assert linkverdict.judge_cable(speed=1000, changes=3, first=None, **ok).verdict == linkverdict.NOT_MEASURED   # the baseline could not be stored
    assert linkverdict.judge_cable(speed=None, changes=3, first=3, **ok).verdict == linkverdict.NOT_MEASURED     # the link is down
    assert linkverdict.judge_cable(speed=1000, changes=3, first=3, **ok).verdict == linkverdict.HEALTHY
    assert linkverdict.judge_cable(speed=100, changes=None, first=None, **ok).verdict == linkverdict.RUN
    assert linkverdict.judge_banner(None, 1000).verdict == linkverdict.NOT_MEASURED
    assert linkverdict.judge_banner(-1, 1000).verdict == linkverdict.NOT_MEASURED


def test_busy_wins_over_everything_else():
    _need()
    v = linkverdict.judge_cable(busy=True, speed=10, changes=9, first=1, tested=False)
    assert v.verdict == linkverdict.NOT_NOW and "busy=true" in v.evidence


# --- decide: the facts of a hello -> the answer's field --------------------------------------------------------

def rows(conn, rule):
    return memory.decisions(conn, rule=rule, limit=64)


def test_no_facts_no_verdict(conn):
    _need()
    for bad in (None, "eth0", 100, [], {"x": 1}.keys()):
        assert linkverdict.decide(conn, MAC, bad, {}) is None
    assert rows(conn, "K-CABLE") == [] and rows(conn, "K-NICBANNER") == []


def test_a_slow_dropped_link_is_run_and_show_with_the_reason(conn):
    _need()
    out = linkverdict.decide(conn, MAC, {**CLONER, "speed_mbps": 100, "carrier_changes": 5}, {"task": None, "session": None})
    assert out["banner"] == "SHOW" and out["cable_test"] == "RUN"
    assert "100Mb/s" in out["cable_why"] and "2 -> 5" in out["cable_why"], out


def test_a_healthy_link_says_none_and_healthy_and_no_reason(conn):
    _need()
    out = linkverdict.decide(conn, MAC, CLONER, {"task": None, "session": None})
    assert out == {"banner": "NONE", "cable_test": "HEALTHY"}


@pytest.mark.parametrize("answer", [{"task": {"type": "restore"}}, {"session": {"id": "S-1", "state": "running"}}])
def test_a_task_or_a_session_in_the_answer_is_not_now(conn, answer):
    _need()
    out = linkverdict.decide(conn, MAC, {**CLONER, "speed_mbps": 100}, {"task": None, "session": None, **answer})
    assert out["cable_test"] == "NOT_NOW" and "cable_why" not in out, out


def test_a_room_round_live_on_the_server_is_not_now_even_when_the_answer_has_none(conn, monkeypatch):
    """The agent knew only about itself; the server knows a stream is running for the others, and a link that drops for seconds hurts it."""
    _need()
    monkeypatch.setattr(linkverdict.room_state, "active_round", lambda c: {"id": "R-1"})
    out = linkverdict.decide(conn, MAC, {**CLONER, "speed_mbps": 100}, {"task": None, "session": None})
    assert out["cable_test"] == "NOT_NOW", out
    assert out["banner"] == "SHOW", "the banner is information: a live round does not hide it"


def test_the_test_already_run_is_tested(conn):
    _need()
    out = linkverdict.decide(conn, MAC, {**CLONER, "speed_mbps": 100, "cable_tested": True}, {"task": None, "session": None})
    assert out["cable_test"] == "TESTED"


# --- recorded: once per change, closed by what came after -----------------------------------------------------

SLOW = {**CLONER, "speed_mbps": 100, "max_mbps": 1000}


def test_the_decision_is_recorded_once_with_its_evidence_and_the_next_hello_adds_no_row(conn):
    _need()
    for _ in range(5):                                      # a hello every few seconds
        linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
    (cable,), (banner,) = rows(conn, "K-CABLE"), rows(conn, "K-NICBANNER")
    assert (cable["point"], cable["action"], cable["verdict"], cable["mode"]) == ("A10", "CABLE_TEST", "RUN", "shadow")
    assert (banner["point"], banner["action"], banner["verdict"], banner["mode"]) == ("G2", "LINK_BANNER", "SHOW", "shadow")
    assert (cable["entity_kind"], cable["entity_key"]) == ("machine", MAC)
    assert "speed_mbps=100" in cable["evidence"] and "floor_mbps=1000" in cable["evidence"], cable["evidence"]
    assert "threshold_mbps=100" in banner["evidence"] and "max_mbps=1000" in banner["evidence"], banner["evidence"]


def test_a_change_closes_the_open_row_with_what_came_after(conn):
    _need()
    linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
    linkverdict.decide(conn, MAC, {**SLOW, "cable_tested": True}, {"task": None, "session": None})   # the agent ran it
    linkverdict.decide(conn, MAC, CLONER, {"task": None, "session": None})                           # the link is back
    (cable,), (banner,) = rows(conn, "K-CABLE"), rows(conn, "K-NICBANNER")
    assert "TESTED" in cable["outcome"], cable
    assert "NONE" in banner["outcome"], banner
    assert len(rows(conn, "K-CABLE")) == 1, "TESTED and HEALTHY are 'all in order', not a decision somebody has to see"


def test_a_slow_link_again_after_it_recovered_is_a_new_decision(conn):
    _need()
    for facts in (SLOW, CLONER, SLOW):
        linkverdict.decide(conn, MAC, facts, {"task": None, "session": None})
    assert [r["verdict"] for r in rows(conn, "K-NICBANNER")] == ["SHOW", "SHOW"]


def test_not_measured_is_recorded_as_such_and_needs_no_action(conn):
    _need()
    linkverdict.decide(conn, MAC, {**CLONER, "carrier_changes": None, "carrier_first": None}, {"task": None, "session": None})
    (row,) = rows(conn, "K-CABLE")
    assert row["verdict"] == "NOT_MEASURED" and row["outcome"], row


def test_off_decides_and_does_not_record(conn):
    _need()
    from server.db import set_setting
    set_setting(conn, veto.SETTING, "off")
    out = linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
    assert out["banner"] == "SHOW" and out["cable_test"] == "RUN"
    assert rows(conn, "K-CABLE") == [] and rows(conn, "K-NICBANNER") == []


def test_enforce_records_too(conn):
    _need()
    from server.db import set_setting
    set_setting(conn, veto.SETTING, "enforce")
    linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
    assert [r["mode"] for r in rows(conn, "K-CABLE")] == ["enforce"]


# --- no decision = nothing is done ---------------------------------------------------------------------------------

def test_a_rule_that_raised_leaves_its_key_out_and_says_so_once(conn, monkeypatch):
    _need()
    def boom(**_):
        raise RuntimeError("the rule fell")
    monkeypatch.setattr(linkverdict, "judge_cable", boom)
    for _ in range(3):
        out = linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
        assert out == {"banner": "SHOW"}, out                # the other rule still answers; the cable test is not 'HEALTHY', it is not there
    (row,) = rows(conn, "K-CABLE")
    assert row["verdict"] == "UNDECIDED" and "RuntimeError" in row["reason"], row


def test_both_rules_raising_is_no_field_at_all(conn, monkeypatch):
    _need()
    monkeypatch.setattr(linkverdict, "judge_cable", lambda **_: 1 / 0)
    monkeypatch.setattr(linkverdict, "judge_banner", lambda *_: 1 / 0)
    assert linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None}) is None


def test_a_round_that_cannot_be_read_is_not_a_free_link(conn, monkeypatch):
    """The busy check failing is not 'not busy': no decision, so no test (a link dropped in the middle of a round)."""
    _need()
    def boom(_conn):
        raise OSError("db")
    monkeypatch.setattr(linkverdict.room_state, "active_round", boom)
    out = linkverdict.decide(conn, MAC, SLOW, {"task": None, "session": None})
    assert "cable_test" not in out and out["banner"] == "SHOW", out


# --- the hello answer, end to end ------------------------------------------------------------------------------

pytest.importorskip("fastapi")
from test_nic_live_3277 import BOOT_A, _hello  # noqa: E402
from test_server_room import CLONER1, room_body, room_server  # noqa: E402,F401 -- fixture


def test_the_hello_answer_carries_the_verdict(room_server):
    anon = room_server["anon"]
    out = _hello(anon, ["S1"], nic_link={**SLOW, "carrier_changes": 4}, boot_id=BOOT_A)
    assert out["link_verdict"]["banner"] == "SHOW" and out["link_verdict"]["cable_test"] == "RUN", out.get("link_verdict")
    assert "100Mb/s" in out["link_verdict"]["cable_why"]
    healthy = _hello(anon, ["S1"], nic_link=CLONER, boot_id=BOOT_A)
    assert healthy["link_verdict"] == {"banner": "NONE", "cable_test": "HEALTHY"}


def test_a_hello_without_nic_link_gets_no_verdict(room_server):
    out = _hello(room_server["anon"], ["S1"])
    assert "link_verdict" not in out, "no facts, no decision"
    garbage = _hello(room_server["anon"], ["S1"], nic_link="slow")
    assert "link_verdict" not in garbage


def test_an_old_agent_that_sends_only_the_speed_is_tested_if_slow_and_not_measured_if_not(room_server):
    anon = room_server["anon"]
    slow = _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 100}, boot_id=BOOT_A)["link_verdict"]
    assert (slow["banner"], slow["cable_test"]) == ("SHOW", "RUN")
    fast = _hello(anon, ["S1"], nic_link={"iface": "eth0", "speed_mbps": 1000}, boot_id=BOOT_A)["link_verdict"]
    assert (fast["banner"], fast["cable_test"]) == ("NONE", "NOT_MEASURED")


def test_a_round_opened_in_the_room_stops_the_cable_test_but_not_the_banner(room_server):
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    assert _hello(anon, ["S1"], nic_link=SLOW, boot_id=BOOT_A)["link_verdict"]["cable_test"] == "RUN"
    assert deploy.post("/api/console/room", json=room_body(room_server, 3, {CLONER1: ["S1", "S2"]})).status_code == 200
    out = _hello(anon, ["S1", "S2"], nic_link=SLOW, boot_id=BOOT_A)["link_verdict"]
    assert (out["banner"], out["cable_test"]) == ("SHOW", "NOT_NOW"), out
    (row,) = rows(ctx.conn, "K-CABLE")
    assert row["verdict"] == "RUN" and "NOT_NOW" in (row["outcome"] or ""), f"the RUN row was not closed by what came after it: {row}"


def test_the_decisions_of_a_hello_reach_brain_decisions(room_server):
    ctx, anon = room_server["ctx"], room_server["anon"]
    for _ in range(3):
        _hello(anon, ["S1"], nic_link=SLOW, boot_id=BOOT_A)
    assert [r["verdict"] for r in rows(ctx.conn, "K-CABLE")] == ["RUN"]
    assert [r["verdict"] for r in rows(ctx.conn, "K-NICBANNER")] == ["SHOW"]


# --- the agent: facts, a flag, no verdict -------------------------------------------------------------------------

from test_agent import AGENT, BASH, posix, sh  # noqa: E402

_BASH = requires_native(("bash", BASH))


def _facts(tmp_path: Path, script: str, *, changes: str | None = "3", speed: str = "100\n", run: bool = True, extra: str = "") -> str:
    root = tmp_path / "root"
    nic = root / "sys/class/net/eth0"
    nic.mkdir(parents=True, exist_ok=True)
    (nic / "speed").write_text(speed, newline="\n")
    if changes is not None:
        (nic / "carrier_changes").write_text(changes + "\n", newline="\n")
    runs = tmp_path / "run"
    runs.mkdir(parents=True, exist_ok=True)
    rd = posix(runs) if run else "/nonexistent/run"
    return sh(f"export SYSROOT={posix(root)!r} RUN_DIR={rd!r} IFACE=eth0 ETHTOOL=/nonexistent/ethtool; {extra} "
              f". {posix(AGENT)}/lib/hw/sysinfo.sh; {script}")


@_BASH
def test_the_agent_reports_the_counter_now_and_the_first_one_this_boot(tmp_path):
    first = _facts(tmp_path, "nic_link_json", changes="3")
    assert '"carrier_changes":3,"carrier_first":3,"cable_tested":false' in first, first
    assert (tmp_path / "run" / "carrier0.eth0").read_text().strip() == "3"
    later = _facts(tmp_path, "nic_link_json", changes="5")      # the link dropped and came back: the first value stays
    assert '"carrier_changes":5,"carrier_first":3' in later, later


@_BASH
def test_an_unread_counter_is_null_and_so_is_a_baseline_that_could_not_be_stored(tmp_path):
    none = _facts(tmp_path / "a", "nic_link_json", changes=None)
    assert '"carrier_changes":null,"carrier_first":null' in none, none
    junk = _facts(tmp_path / "b", "nic_link_json", changes="not-a-number")
    assert '"carrier_changes":null,"carrier_first":null' in junk, junk
    unwritable = _facts(tmp_path / "c", "nic_link_json", changes="3", run=False)
    assert '"carrier_changes":3,"carrier_first":null' in unwritable, unwritable   # never 'no drop': the brain says not measured


@_BASH
def test_the_agent_says_when_the_cable_test_ran(tmp_path):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "cable-live.json").write_text('{"status":"ok"}', newline="\n")
    assert '"cable_tested":true' in _facts(tmp_path, "nic_link_json")


@_BASH
@pytest.mark.parametrize("answer, flag", [
    ('{"link_verdict":{"banner":"SHOW"}}', "1"), ('{"link_verdict":{"banner":"NONE"}}', "0"),
    ('{"link_verdict":{"banner":"NOT_MEASURED"}}', "0"),
    ('{"link_verdict":{"banner":"show"}}', ""), ('{"link_verdict":{"cable_test":"RUN"}}', ""), ('{"schema":1}', ""),
    ('{"link_verdict":null}', ""), ("{not json", ""),
])
def test_the_screen_flag_is_the_brains_banner_verdict_and_empty_without_one(tmp_path, answer, flag):
    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "response.json").write_text(answer, newline="\n")
    out = _facts(tmp_path, "nic_link_record", speed="100\n")
    assert out == f"nic_link=eth0|100||{flag}\n", out


@_BASH
def test_no_answer_yet_is_an_empty_flag(tmp_path):
    assert _facts(tmp_path, "nic_link_record") == "nic_link=eth0|100||\n"


def test_neither_the_agent_nor_the_gui_holds_the_threshold():
    """The 100 and the 1000 are in one place: server/brain."""
    gui = (Path(__file__).resolve().parent.parent / "native-gui/src/screens_standby.c").read_text(encoding="utf-8")
    body = gui[gui.index("double sb_nic_banner"):]
    body = body[:body.index("\n}\n")]
    assert "<= 100" not in body and "nic_max" not in body.split("char now[24]")[0], "the banner decides again"
    assert "nic_warn" in body
