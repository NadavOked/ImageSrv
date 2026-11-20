"""#3268 (B62, Nadav 18/11): the brain decides on a slow disk before the round -- what it did in the last wave AND what the check shows now.

Nadav, on #3650: "the brain decides if it fell behind last time AND if the check showed it falls behind now". Red only when the
check of NOW says slow (the history joins its reason); fell behind and passes now = a neutral line, no red; never behind and
passes = nothing. The brain suggests and never blocks (16/11). The agent reports the check in the hello (`write_check`), the
answer carries the decision (`write_decisions`), the screen shows it, and every decision is a row in `brain_decisions` with both
pieces of evidence.

The four combinations are run through the server's own hello, and the history is a real chunk wave (`test_write_rate_3295._earlier`,
the APS of cloner 2, 14-17/11: it wrote ~61 MB/s in the wave it fell behind in). The agent half is `test_slow_memory_3268.py`.

**The update of 18/11 (#3268, after #3718): the short check stays, and the history exposes what it cannot.** A disk that passes now can be red
when the last wave it fell behind in shows it wrote, all through the wave, under the floor of the check (the APS: 213 now, 43 over the wave).
Four cases, through the hello and a real chunk wave: APS (passes now 213, wrote 43) = RED with the history as the reason; Samsung (fell behind
once, wrote 248) = grey information; slow now = RED; no history = nothing. The floor is the one the agent checked against (`floor_mbps`).
They are the tests under "the history exposes what the short check cannot see" below, and they fail on the code before.

**Negative control** (as run, in the PR): `server/brain/slowdisk.py`, `server/disks/slow_memory.py`, `server/agent/api.py`,
`server/agent/hello.py`, `server/brain/say.py` and `server/brain/catalog.toml` back at the base (`609f2d416`, #3650). The tests that
call the module directly import it inside the test, so on the base only they fail with an ImportError (and they are not the control);
the ones through the hello fail on behaviour: no `write_decisions`, no row in `brain_decisions`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory, say
from server.rounds import chunksend

from conftest import hello_body
from test_server_room import CLONER1, CLONER2, room_server  # noqa: F401 -- room_server is a fixture
from test_write_rate_3295 import DURABLE, SECONDS, _earlier

APS = "t10.ATA     APS-SL3N-512                            SICSD40302WL"   # with its spaces (#3574)
SAMSUNG = "naa.5002538d42a58a2c"
BEHIND = {APS: {"bytes": DURABLE * chunksend.CHUNK_BYTES, "seconds": SECONDS}}   # ses_fae905e4's first pass (#3034): ~61 MB/s
SLOW_NOW = "איטי — 59 MB/s, מתחת לסף 74 MB/s"
WROTE = int(DURABLE * chunksend.CHUNK_BYTES / SECONDS / 10 ** 6)                  # what the history says: 55 <= WROTE <= 65


def _wrote(mbps: int, serial: str = APS) -> dict:
    """`rate_outcome.behind` of a chunk wave in which the drive made `mbps` MB/s durable over its first passes (same bytes, other seconds)."""
    size = DURABLE * chunksend.CHUNK_BYTES
    return {serial: {"bytes": size, "seconds": size / ((mbps + 0.5) * 10 ** 6)}}


def _report(serial: str = APS, state: str = "ok", mbps: int = 120, first: int | None = 200, reason: str = "", floor: object = ...) -> dict:
    """What the agent sends for a drawer. `floor` omitted = the field is absent (an older agent); `None` = null (no floor for this drawer)."""
    out = {"serial": serial, "state": state, "mbps": mbps, "first_mbps": first, "reason": reason}
    if floor is not ...:
        out["floor_mbps"] = floor
    return out


def _slow(serial: str = APS) -> dict:
    return _report(serial, "slow", 59, 61, SLOW_NOW)


def _hello(room_server, reports, serials=(APS,), mac: str = CLONER1, **extra) -> dict:
    """A cloner's hello through the server's own path, with `write_check`. `reports=None` = the field is absent."""
    body = hello_body(mac)
    body["disks"] = [{"dev": f"sd{chr(97 + i)}", "size_bytes": 256060514304, "model": "Drawer SSD", "serial": s,
                      "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1} for i, s in enumerate(serials)]
    if reports is not None:
        body["write_check"] = reports
    body.update(extra)
    response = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _rows(room_server, serial: str = APS) -> list[dict]:
    return memory.decisions(room_server["ctx"].conn, kind="disk", key=serial, rule="B62", limit=50)


def _learn(room_server, on: bool = True, behind: dict | None = BEHIND) -> None:
    conn = room_server["ctx"].conn
    db.set_setting(conn, "learned_rate_enabled", "true" if on else "false")
    if behind is not None:
        _earlier(conn, "ses_earlier", behind)


# --- the four combinations ----------------------------------------------------------------------------------------------

def test_history_and_slow_now_is_red_and_the_history_is_the_evidence(room_server):
    _learn(room_server)
    got = _hello(room_server, [_slow()])["write_decisions"]
    assert [(d["serial"], d["verdict"], d["state"], d["mbps"], d["first_mbps"]) for d in got] == [(APS, "RED", "slow", 59, 61)], got
    reason = got[0]["reason"]
    assert reason.startswith(SLOW_NOW + "; וגם בגל הקודם פיגר (") and f"{WROTE}MB/s" in reason, reason   # the check's own words, then the history
    (row,) = _rows(room_server)
    assert (row["verdict"], row["action"], row["reason"], row["entity_kind"], row["entity_key"]) == ("RED", "SLOW_DISK", reason, "disk", APS)
    assert {"now_state=slow", "now_mbps=59", "now_first_mbps=61", f"now_reason={SLOW_NOW}", "history=found",
            f"history_mbps={WROTE}", "history_wave=ses_earlier"} <= set(row["evidence"]), row["evidence"]   # both pieces of evidence, in one row
    assert any(e.startswith("history_at=20") for e in row["evidence"]), row["evidence"]


def test_history_and_ok_now_is_information_not_red(room_server):
    _learn(room_server)
    got = _hello(room_server, [_report(mbps=120, first=200)])["write_decisions"]
    assert [(d["verdict"], d["state"], d["mbps"], d["first_mbps"]) for d in got] == [("INFO", "ok", 120, 200)], got
    assert got[0]["reason"].startswith("פיגר בגל הקודם (") and got[0]["reason"].endswith("); הבדיקה עכשיו תקינה"), got
    assert "איטי" not in got[0]["reason"], "a disk that passes now is not said to be slow"
    (row,) = _rows(room_server)
    assert row["verdict"] == "INFO" and {"now_state=ok", "now_mbps=120", "history=found", f"history_mbps={WROTE}"} <= set(row["evidence"])


def test_no_history_and_ok_now_is_nothing_said_and_still_remembered(room_server):
    _learn(room_server, behind=None)                                    # the learning is on, and nobody ever fell behind
    answer = _hello(room_server, [_report()])
    assert "write_decisions" not in answer, "nothing to say, nothing is sent"
    (row,) = _rows(room_server)
    assert row["verdict"] == "NONE" and "history=none" in row["evidence"] and "now_state=ok" in row["evidence"], row   # but the brain says it looked


def test_no_history_and_slow_now_is_red_as_it_always_was(room_server):
    _learn(room_server, behind=None)
    got = _hello(room_server, [_slow()])["write_decisions"]
    assert [(d["verdict"], d["reason"]) for d in got] == [("RED", SLOW_NOW)], got
    (row,) = _rows(room_server)
    assert row["verdict"] == "RED" and "history=none" in row["evidence"]


def test_history_alone_decides_nothing(room_server):
    """Nadav: the history alone does not colour a disk. No report of the check of now = no decision and no row."""
    _learn(room_server)
    for reports in (None, [], [{"serial": APS}]):
        assert "write_decisions" not in _hello(room_server, reports)
    assert _rows(room_server) == []


def test_the_history_is_of_the_serial_not_of_another_disk(room_server):
    _learn(room_server)
    got = _hello(room_server, [_report(SAMSUNG, mbps=120), _report(APS, mbps=120)], serials=(SAMSUNG, APS))["write_decisions"]
    assert [d["serial"] for d in got] == [APS], got
    assert [r["verdict"] for r in _rows(room_server, SAMSUNG)] == ["NONE"]
    _learn(room_server, behind=None)
    squeezed = APS.replace("     ", " ")                                # another string is not the same disk (#3574)
    assert "write_decisions" not in _hello(room_server, [_report(squeezed)], serials=(squeezed,))


# --- the history exposes what the short check cannot see (Nadav 18/11) ------------------------------------------------------

FLOOR = 74                                                              # the floor the agent checked against (the console's, LR5)
CACHE = "בגל הקודם כתב 43MB/s לאורך כל הגל"


def test_aps_passes_the_short_check_but_wrote_43_all_wave_long_is_red_and_the_history_is_the_reason(room_server):
    """The APS of cloner 2 (metal 18/11): 213 MB/s now, in its write cache; 43 MB/s over the whole wave it fell behind in."""
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(43))
    got = _hello(room_server, [_report(mbps=216, first=213, floor=FLOOR)])["write_decisions"]
    assert [(d["serial"], d["verdict"], d["state"], d["mbps"], d["first_mbps"]) for d in got] == [(APS, "RED", "ok", 216, 213)], got
    reason = got[0]["reason"]
    assert reason.startswith(CACHE) and f"מתחת לסף {FLOOR} MB/s" in reason and reason.endswith("הבדיקה הקצרה לא מספיק ארוכה כדי לראות את זה"), reason
    assert "הבדיקה עכשיו תקינה" not in reason and "איטי —" not in reason, "the reason is the history, not the check of now"
    (row,) = _rows(room_server)
    assert (row["verdict"], row["reason"]) == ("RED", reason)
    assert {"now_state=ok", "now_mbps=216", f"now_floor_mbps={FLOOR}", "history=found", "history_mbps=43", "history_wave=ses_earlier"} <= set(row["evidence"]), row["evidence"]


def test_samsung_that_fell_behind_once_but_wrote_248_is_grey_information_not_red(room_server):
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(248, SAMSUNG))
    got = _hello(room_server, [_report(SAMSUNG, mbps=250, first=260, floor=FLOOR)], serials=(SAMSUNG,))["write_decisions"]
    assert [(d["verdict"], d["state"]) for d in got] == [("INFO", "ok")], got
    assert got[0]["reason"].startswith("פיגר בגל הקודם (248MB/s, ") and "איטי" not in got[0]["reason"], got
    assert [r["verdict"] for r in _rows(room_server, SAMSUNG)] == ["INFO"]


def test_slow_now_is_red_whatever_the_history_says(room_server):
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(248))        # it wrote 248 when it fell behind: the history does not redeem it
    got = _hello(room_server, [dict(_slow(), floor_mbps=FLOOR)])["write_decisions"]
    assert [(d["verdict"], d["state"]) for d in got] == [("RED", "slow")] and got[0]["reason"].startswith(SLOW_NOW + "; וגם בגל הקודם פיגר ("), got


def test_no_history_says_nothing_even_when_the_check_passes_with_a_floor(room_server):
    _learn(room_server, behind=None)
    assert "write_decisions" not in _hello(room_server, [_report(mbps=216, first=213, floor=FLOOR)])
    assert [r["verdict"] for r in _rows(room_server)] == ["NONE"]


@pytest.mark.parametrize("wrote, floor, verdict", [
    (43, FLOOR, "RED"), (FLOOR - 1, FLOOR, "RED"),
    (FLOOR, FLOOR, "INFO"),                  # the floor itself passes: like the check, which is slow only BELOW it
    (FLOOR + 1, FLOOR, "INFO"), (248, FLOOR, "INFO"),
    (43, 40, "INFO"), (43, 44, "RED"),       # the floor is the agent's, not a number of the server's
    (43, None, "INFO"),                      # null: this drawer has no such floor (a mechanical disk) -- unknown is not slow
])
def test_the_floor_is_the_one_the_agent_checked_against(room_server, wrote, floor, verdict):
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(wrote))
    got = _hello(room_server, [_report(mbps=216, floor=floor)])["write_decisions"]
    assert [d["verdict"] for d in got] == [verdict], got


@pytest.mark.parametrize("field", [{}, {"floor_mbps": None}, {"floor_mbps": 0}, {"floor_mbps": -5}, {"floor_mbps": "74"}, {"floor_mbps": True},
                                   {"floor_mbps": 74.5}, {"floor_mbps": 10 ** 9}], ids=repr)
def test_a_floor_that_is_not_known_never_makes_red_and_the_report_is_still_read(room_server, field):
    """An older agent, or a floor that is not a number: the server invents none (principle 5) -- the history stays information."""
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(43))
    got = _hello(room_server, [{**_report(mbps=216), **field}])["write_decisions"]
    assert [d["verdict"] for d in got] == ["INFO"], got
    assert "now_floor_mbps=null" in _rows(room_server)[0]["evidence"]


def test_the_history_under_the_floor_does_not_colour_when_the_learning_is_off(room_server):
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(43))
    db.set_setting(room_server["ctx"].conn, "learned_rate_enabled", "false")
    assert "write_decisions" not in _hello(room_server, [_report(mbps=216, floor=FLOOR)])
    assert [r["verdict"] for r in _rows(room_server)] == ["UNDECIDED"]


def test_it_is_still_only_a_suggestion_in_every_mode(room_server):
    _learn(room_server, behind=None)
    _earlier(room_server["ctx"].conn, "ses_earlier", _wrote(43))
    db.set_setting(room_server["ctx"].conn, "brain_veto_mode", "off")
    assert "write_decisions" not in _hello(room_server, [_report(mbps=216, floor=FLOOR)]) and _rows(room_server) == []
    db.set_setting(room_server["ctx"].conn, "brain_veto_mode", "shadow")
    assert _hello(room_server, [_report(mbps=216, floor=FLOOR)])["write_decisions"][0]["verdict"] == "RED"
    assert _rows(room_server)[0]["mode"] == "shadow"


# --- principle 5: unknown is not "none" ---------------------------------------------------------------------------------

def test_a_history_that_cannot_be_known_is_undecided_not_none(room_server, monkeypatch):
    _learn(room_server, on=False)                                       # the automatic rate is off: the server learns nothing
    assert "write_decisions" not in _hello(room_server, [_report()])
    assert [(r["verdict"], "history=off" in r["evidence"]) for r in _rows(room_server)] == [("UNDECIDED", True)]
    from server.rounds import chunk_rate
    _learn(room_server, on=True, behind=None)

    def broken(*_a, **_k):
        raise RuntimeError("the wave table cannot be read")
    monkeypatch.setattr(chunk_rate, "sustained", broken)
    assert "write_decisions" not in _hello(room_server, [_report(mbps=121)])
    assert _rows(room_server)[0]["verdict"] == "UNDECIDED" and "history=unread" in _rows(room_server)[0]["evidence"]
    got = _hello(room_server, [_slow()])["write_decisions"]            # but the check of now says slow: red does not wait for the memory
    assert [(d["verdict"], d["reason"]) for d in got] == [("RED", SLOW_NOW)], got


# --- the record ---------------------------------------------------------------------------------------------------------

def test_the_same_evidence_is_one_row_and_a_new_number_is_a_new_row(room_server):
    _learn(room_server)
    for _ in range(3):                                                  # the hello comes every ~2 s
        _hello(room_server, [_report(mbps=120)])
    assert len(_rows(room_server)) == 1
    _hello(room_server, [_report(mbps=121)])
    assert len(_rows(room_server)) == 2
    _hello(room_server, [_slow()])
    assert [r["verdict"] for r in _rows(room_server)] == ["RED", "INFO", "INFO"]


def test_the_console_says_it_in_words(room_server):
    _learn(room_server)
    _hello(room_server, [_slow()])
    line = say.line_he(_rows(room_server)[0])
    assert "SLOW_DISK" not in line and "RED" not in line and "דיסק איטי לפני הסבב" in line and "אדום" in line, line


def test_it_is_off_with_the_brain_switch(room_server):
    _learn(room_server)
    db.set_setting(room_server["ctx"].conn, "brain_veto_mode", "off")
    assert "write_decisions" not in _hello(room_server, [_slow()])
    assert _rows(room_server) == []
    db.set_setting(room_server["ctx"].conn, "brain_veto_mode", "enforce")
    assert _hello(room_server, [_slow()])["write_decisions"][0]["verdict"] == "RED"
    assert _rows(room_server)[0]["mode"] == "enforce"                  # the mode is recorded as it is; it blocks nothing in any


def test_it_never_blocks_a_round(room_server):
    """The brain suggests (Nadav 16/11): no code that decides about a round or writes to a disk reads the decision."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    for path in (*(repo / "server" / "rounds").rglob("*.py"), *(repo / "server" / "disks").glob("write*.py"),
                 *(repo / "agent" / "lib" / "restore").glob("write*.sh"), *(repo / "agent" / "lib" / "restore").glob("drawer*.sh"),
                 repo / "agent" / "lib" / "restore" / "restore.sh"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert not any(word in text for word in ("slowdisk", "write_decisions", "idle_wc_decision")), f"{path.name} reads the decision"


# --- what a hello may carry ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("junk", [
    "x", 5, {"serial": APS}, [None, 5, "x", []],
    [_report(state="red")], [_report(state="checking")], [_report(state="failed")],                 # only a finished ok / slow check is a report
    [_report(mbps=0)], [_report(mbps=-5)], [_report(mbps=True)], [_report(mbps="120")], [_report(mbps=120.5)], [_report(mbps=10 ** 9)],
    [_report(first=0)], [_report(first="x")], [_report(serial="")], [_report(serial=None)], [_report(serial=APS + "x")],
    [{k: v for k, v in _report().items() if k != "mbps"}],
], ids=repr)
def test_a_report_that_is_not_one_is_dropped_and_the_hello_is_answered(room_server, junk):
    _learn(room_server)
    answer = _hello(room_server, junk)
    assert answer["known"] is True and "write_decisions" not in answer
    assert _rows(room_server) == []


def test_a_serial_twice_is_decided_once(room_server):
    _learn(room_server)
    assert [d["serial"] for d in _hello(room_server, [_slow(), _slow()])["write_decisions"]] == [APS]
    assert len(_rows(room_server)) == 1


def test_at_most_sixteen_reports_are_read():
    from server.brain import slowdisk   # noqa: PLC0415

    disks = [{"serial": f"S{i}"} for i in range(40)]
    got = slowdisk.well_formed([_report(f"S{i}", mbps=100 + i) for i in range(40)], disks)
    assert [n.serial for n in got] == [f"S{i}" for i in range(16)]


def test_the_reason_the_agent_sends_is_one_line_without_the_separator(room_server):
    _learn(room_server, behind=None)
    nasty = "איטי — 59 MB/s\n| " + "x" * 400 + "\x00"
    got = _hello(room_server, [_report(state="slow", mbps=59, reason=nasty)])["write_decisions"][0]["reason"]
    assert "\n" not in got and "|" not in got and "\x00" not in got and len(got) <= 300, got
    assert _hello(room_server, [_report(state="slow", mbps=58, reason="")])["write_decisions"][0]["reason"] == "איטי — 58 MB/s"   # no reason: the number


def test_only_a_registered_cloner_is_decided_for(room_server):
    _learn(room_server)
    answer = _hello(room_server, [_slow()], mac="aa:bb:cc:00:00:99")      # not registered
    assert answer["known"] is False and "write_decisions" not in answer
    assert _rows(room_server) == []


def test_the_hello_answer_no_longer_carries_the_history_itself(room_server):
    """One source of the decision: the brain reads the history, the agent does not (the field of #3650 is gone)."""
    _learn(room_server)
    assert "write_history" not in _hello(room_server, [_slow()])
    assert "write_history" not in _hello(room_server, None, mac=CLONER2)


# --- the module, directly -----------------------------------------------------------------------------------------------

def test_the_module_never_raises_on_what_a_hello_may_carry(room_server):
    from server.brain import slowdisk   # noqa: PLC0415 -- lazily: on the base this is the only test that cannot even import it

    conn = room_server["ctx"].conn
    for raw in (None, "x", 5, {}, [None], [{}], [[]], [{"serial": 5}]):
        for disks in (None, "x", [None, 5, {}, {"serial": 7}], [{"serial": APS}]):
            assert slowdisk.decide(conn, CLONER1, raw, disks) == []
    assert slowdisk.well_formed([_report()], None) == []                     # no disks in the hello: nothing to match


@pytest.mark.parametrize("state, mem, verdict", [
    ("slow", "found", "RED"), ("slow", "none", "RED"), ("slow", "off", "RED"), ("slow", "unread", "RED"),
    ("ok", "found", "INFO"), ("ok", "none", "NONE"), ("ok", "off", "UNDECIDED"), ("ok", "unread", "UNDECIDED"),   # 55 MB/s history, no floor reported
])
def test_judge_is_the_table_of_the_docstring(state, mem, verdict):
    from server.brain import slowdisk   # noqa: PLC0415
    from server.disks import slow_memory   # noqa: PLC0415

    was = slow_memory.Memory(mem, 55 if mem == "found" else None, "2026-11-17T06:57:39+00:00" if mem == "found" else None, "ses_1")
    got, reason = slowdisk.judge(slowdisk.Now(APS, state, 59 if state == "slow" else 120, None, SLOW_NOW if state == "slow" else ""), was)
    assert got == verdict
    assert ("(55MB/s, 17/11)" in reason) == (mem == "found"), reason
