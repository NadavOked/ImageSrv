"""#4045: the error and edge branches that the public gate's patch coverage (codecov/patch, 23/11) found untested.

The gate PR (ImageSrv#108, on `a4cffd3af`) failed only `codecov/patch`: 85.37% of the diff hit, target 90.03%. The
biggest share is `tools/iso/esxi-install-test.py` (its tests are in `test_esxi_install_test_4029.py`). The rest are the
seven small branches below. Each is a path that must NOT take the wave or the round down (principle 5: "not read" is
not "fine", and a brain failure is logged and the round goes on), so each test checks what the caller sees, not that
the line ran.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import db  # noqa: E402
from server.agent import held  # noqa: E402
from server.brain import faultwho, line  # noqa: E402
from server.interserver import api as interserver_api  # noqa: E402
from server.rounds import room as room_mod  # noqa: E402
from test_brain_line_3064 import _c  # noqa: E402
from test_brain_relight_3916 import _mode, _relights, _sweep, _wave_id, _wave_two_open_both_off  # noqa: E402
from test_brain_wave_eta_3295 import _eta  # noqa: E402
from test_room_rewake_1721 import _packets_to  # noqa: E402
from test_server_room import CLONER1, CLONER2, room_server  # noqa: E402,F401

MAC = "aa:bb:cc:00:00:01"
T = datetime(2026, 11, 20, 10, 55, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("INSERT INTO net_devices (mac) VALUES (?)", (MAC,))
    c.commit()
    return c


# --- server/agent/held.py: the clock `clear` is given may be naive ---------------------------------------------------

def test_a_naive_release_clock_is_written_as_utc_and_the_hold_is_gone(conn):
    held.note(conn, MAC, [], now=T)
    assert held.is_held(conn, MAC, T)
    held.clear(conn, MAC, now=datetime(2026, 11, 20, 10, 55, 18))                 # no tzinfo: the room's fake clock may be naive
    row = conn.execute("SELECT detail FROM journal WHERE event = 'held_released' AND machine = ?", (MAC,)).fetchone()
    assert row["detail"] == "2026-11-20T10:55:18+00:00"                          # an offset that `_left_held` can compare
    assert not held.is_held(conn, MAC, T)


# --- server/rounds/room.py: when did the cloner leave the hold of THIS wave -------------------------------------------

def test_a_hold_that_lapsed_without_a_clear_counts_from_its_freshness_limit_inside_the_wave(conn):
    held.note(conn, MAC, [], now=T)                                               # the last held beat; power was pulled, no clear
    now = T + timedelta(seconds=200)
    assert not held.is_held(conn, MAC, now)
    stale = T + timedelta(seconds=held.FRESH_SECONDS)
    assert room_mod._left_held(conn, MAC, T - timedelta(seconds=300), now) == stale
    assert room_mod._left_held(conn, MAC, T + timedelta(seconds=120), now) is None   # it lapsed before this wave opened: not counted
    assert room_mod._left_held(conn, MAC, T - timedelta(seconds=300), T + timedelta(seconds=30)) is None   # not lapsed yet at `now`


def test_the_later_of_a_release_and_a_lapse_wins_and_an_unreadable_release_is_ignored(conn):
    held.note(conn, MAC, [], now=T)
    now, opened = T + timedelta(seconds=300), T - timedelta(seconds=300)
    stale = T + timedelta(seconds=held.FRESH_SECONDS)
    db.journal(conn, "held_released", "not a time", machine=MAC)                  # garbage in `detail`: not a release, no crash
    assert room_mod._left_held(conn, MAC, opened, now) == stale
    later = T + timedelta(seconds=90)
    db.journal(conn, "held_released", later.isoformat(timespec="seconds"), machine=MAC)
    assert room_mod._left_held(conn, MAC, opened, now) == later
    earlier_wave = T - timedelta(seconds=600)                                     # a release of an earlier wave is not this wave's
    db.journal(conn, "held_released", earlier_wave.isoformat(timespec="seconds"), machine=MAC)
    assert room_mod._left_held(conn, MAC, opened, now) == later
    assert room_mod._aware(None) is None
    assert room_mod._aware(datetime(2026, 11, 20, 1, 2, 3)).tzinfo == timezone.utc


# --- the relight of #3916: a brain that fails is logged, and the round goes on ---------------------------------------

def test_a_relight_that_the_brain_cannot_decide_is_logged_and_sends_nothing_and_the_next_call_still_works(
        room_server, monkeypatch, caplog):
    conn = room_server["ctx"].conn
    _mode(conn, "enforce")
    one, two = _c(1), _c(2)

    def broken(*a, **kw):
        raise RuntimeError("memory is not writable")
    with monkeypatch.context() as m:
        m.setattr(line.memory, "record", broken)
        with caplog.at_level(logging.ERROR):
            got = line.relight(conn, round_id="r1", wave_id="wx", remaining=2, cloners=[one, two], off={one.mac, two.mac})
    assert got == [], "a decision that could not be written must not send a WoL"
    assert "relight on wx failed" in caplog.text and ("wx", one.mac) not in line._lit
    again = line.relight(conn, round_id="r1", wave_id="wx", remaining=2, cloners=[one, two], off={one.mac, two.mac})
    assert again == [one.mac], "the failure left no state behind that would block the wake"


def test_the_room_goes_on_when_the_relight_raises(room_server, monkeypatch, caplog):
    ctx, woken = room_server["ctx"], room_server["woken"]
    _wave_two_open_both_off(room_server)
    _mode(ctx.conn, "enforce")                                                    # after the set-up: wave 1 may write a row of its own
    wave = _wave_id(room_server)                                                  # read before the tick: the tick may move on
    before = _packets_to(woken, CLONER2), _packets_to(woken, CLONER1)

    def broken(*a, **kw):
        raise RuntimeError("brain is down")
    monkeypatch.setattr(room_mod.brain_line, "relight", broken)
    with caplog.at_level(logging.ERROR):
        _sweep(room_server)                                                       # must not raise into the room's tick
    assert f"wave {wave}: relight failed" in caplog.text
    assert (_packets_to(woken, CLONER2), _packets_to(woken, CLONER1)) == before
    assert _relights(ctx.conn, wave) == []
    assert ctx.conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'room_relight' AND detail LIKE ?",
                            (f"{wave} %",)).fetchone()[0] == 0
    assert ctx.conn.execute("SELECT state FROM room_rounds").fetchone()["state"] == "active"


# --- server/brain/waveeta.py: a write history that is not a history is not "no history" ------------------------------

A, B, C = "serial-A", "serial-B", "serial-C"


def _gap(conn, hook, behind, serials=(A, B, C)):
    eta = _eta(conn, [0.0])
    eta.chunk.write_history = hook
    return eta._no_history({("m", s): 100 for s in serials}, behind)


def test_a_history_hook_that_raises_is_logged_and_that_drive_is_neither_missing_nor_counted(conn, caplog):
    def hook(serial):
        if serial == B:
            raise OSError("history file unreadable")
        return None                                                               # C: really no history
    with caplog.at_level(logging.ERROR):
        gap = _gap(conn, hook, behind={A: object()})
    assert gap == (C,), "B was not READ, so it is not 'has none'; only C is"
    assert f"the write history of {B} was not read" in caplog.text


@pytest.mark.parametrize("junk", ["banana", (0, "ses_x"), (-5, "ses_x"), (True, "ses_x"), (5,), (5, 7), [5, "ses_x"], {"a": 1}])
def test_an_answer_that_is_not_a_history_is_a_logged_warning_and_not_a_mix(conn, caplog, junk):
    """A (in `chunk_write_behind`) has a history; B answers `junk`. If junk counted as 'no history' K3 would call the
    wave low-confidence on a log line; if it counted as a history, a real gap could hide. It is neither."""
    with caplog.at_level(logging.WARNING):
        gap = _gap(conn, lambda s: junk, behind={A: object()}, serials=(A, B))
    assert gap == ()
    assert f"the write history of {B} was not a history" in caplog.text


def test_a_valid_history_next_to_a_drive_without_one_names_that_drive(conn):
    gap = _gap(conn, lambda s: (248, "ses_6881ed98") if s == A else None, behind={}, serials=(A, B))
    assert gap == (B,)


# --- server/interserver/api.py: the peer's address, when the socket cannot say ---------------------------------------

class _Raw:
    def __init__(self, answer):
        self.answer = answer

    def getpeername(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("answer,host", [
    (("10.1.2.3", 51234), "10.1.2.3"),
    (OSError("not connected"), "?"),                 # the peer is already gone: one shared bucket for the per-source cap
    (OSError(107, "Transport endpoint is not connected"), "?"),
    ((), "?"),                                       # an address family that has no host part
])
def test_the_peer_host_is_the_address_without_the_port_or_a_question_mark(answer, host):
    assert interserver_api._peer_host(_Raw(answer)) == host


# --- server/brain/faultwho.py: the senses a disk reported ------------------------------------------------------------

def test_only_a_well_formed_senses_report_of_a_target_is_kept():
    hot = {"temp": {"before_c": 40, "peak_during_c": 71, "after_c": 65, "samples": 5}}
    got = faultwho._senses({
        ("m1", "D1"): {"senses": hot},
        ("m1", "D2"): {"senses": "not an object"},        # a broken agent report: dropped, not guessed
        ("m1", "D3"): {},                                  # an old agent: no report at all
        ("m2", "D1"): {"senses": {"phy": None}},
    })
    assert set(got) == {("m1", "D1"), ("m2", "D1")}
    assert got[("m1", "D1")]["temp"]["peak_during_c"] == 71 and got[("m1", "D1")]["phy"] is None
    assert got[("m2", "D1")]["temp"] is None
