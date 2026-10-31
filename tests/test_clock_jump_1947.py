"""#1947 — a server wall-clock step does not bring down a running wave.

Measured on the lab (QA-G16, 22/10 04:17, lab-int3 `6c195e0e`, cloner 2):
120 s into a "pull from server" wave the TA clock was moved +3 h
(`date -s "+3 hours"`). At once: `client_lost`, `session_close`,
`room_failed written=0/2` -- while both machines kept reporting. Every timer
on the round path compared the wall clock with a *stored* stamp, so a step
moved "now" and left the stamps behind (forward: elapsed 3 h > LOST_SECONDS;
backward: elapsed < 0, which #1771 had made "lost at once").

The clock here is faked where the production code reads it -- the module
`datetime` of `server.rounds.room` (both `_mark_lost` and `_utcnow` go through it)
-- and a step moves only the wall clock, never `time.monotonic()`. That is
exactly what a step is, and it is what the server's detector looks for.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from server.rounds import room as room_mod

from test_server_room import (           # noqa: F401 — ‏room_server is a fixture
    CLONER1, CLONER2, cloner_hello, report, room, room_body, room_server,
)

try:                                      # negative control: no module -> the
    from server.system import clockstep          # behaviour is checked, not an import
except ImportError:                       # pragma: no cover
    clockstep = None

pytest.importorskip("fastapi")

LOST = getattr(room_mod, "LOST_SECONDS", 180)
STEP = 3 * 60 * 60                        # the lab step: three hours


class _Clock:
    """Wall clock and monotonic clock of the server under test.

    `advance` is real time passing (both move); `jump` is a clock step
    (only the wall clock moves)."""

    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.mono = time.monotonic()         # same offset as the samples taken so far

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.mono += seconds

    def jump(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def _install(monkeypatch) -> _Clock:
    clock = _Clock()

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):           # noqa: D401 — the faked wall clock
            return clock.now if tz is not None else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(room_mod, "datetime", _FakeDatetime)
    if clockstep is not None:
        monkeypatch.setattr(clockstep, "_mono", lambda: clock.mono)
    return clock


def _events(ctx, event: str) -> list[str]:
    return [r["detail"] for r in ctx.conn.execute(
        "SELECT detail FROM journal WHERE event = ? ORDER BY rowid", (event,))]


def _states(ctx, wave: str) -> dict[str, str]:
    return {r["mac"]: r["state"] for r in ctx.conn.execute(
        "SELECT mac, state FROM session_members WHERE session_id = ?", (wave,))}


def _writing(client, wave: str, mac: str) -> None:
    resp = client.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": mac, "state": "writing",
        "targets": [{"dev": d, "bytes_written": 40, "bytes_total": 100, "state": "writing"}
                    for d in ("sda", "sdb")],
    })
    assert resp.status_code == 200, resp.text


def _running_wave(room_server) -> str:
    """Two cloners, two drawers each, both writing -- the QA-G16 wave."""
    ctx, deploy, anon = room_server["ctx"], room_server["deploy"], room_server["anon"]
    opened = deploy.post("/api/console/room", json=room_body(
        room_server, 4, {CLONER1: ["S1", "S2"], CLONER2: ["S3", "S4"]}))
    assert opened.status_code == 200, opened.text
    wave = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    room_mod.sweep(ctx.conn, ctx.store)
    assert room(deploy)["round"]["wave_state"] == "running"
    _writing(anon, wave, CLONER1)
    _writing(anon, wave, CLONER2)
    return wave


@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_a_clock_step_mid_wave_loses_no_machine(room_server, monkeypatch, step):
    """The bug: a 3 h step marked every machine of a live wave `lost`."""
    ctx, deploy = room_server["ctx"], room_server["deploy"]
    wave = _running_wave(room_server)
    clock = _install(monkeypatch)
    assert room_mod.sweep(ctx.conn, ctx.store) is True     # first sample
    clock.advance(10)
    clock.jump(step)

    assert room_mod.sweep(ctx.conn, ctx.store) is True

    assert _states(ctx, wave) == {CLONER1: "writing", CLONER2: "writing"}, \
        "a clock step marked machines that kept reporting as lost"
    assert not _events(ctx, "client_lost")
    assert not _events(ctx, "room_failed")
    assert room(deploy)["round"]["wave_state"] == "running"
    jumps = _events(ctx, "clock_jump")
    assert len(jumps) == 1 and jumps[0].startswith(f"{step:+d}s") and wave in jumps[0], jumps


@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_after_a_clock_step_a_silent_machine_is_still_lost_on_time(room_server, monkeypatch, step):
    """Guard against the over-fix: the step must not freeze the lost timer.
    After it, LOST_SECONDS of real silence still mark the machines lost."""
    ctx = room_server["ctx"]
    wave = _running_wave(room_server)
    clock = _install(monkeypatch)
    room_mod.sweep(ctx.conn, ctx.store)
    clock.jump(step)
    room_mod.sweep(ctx.conn, ctx.store)
    assert not _events(ctx, "client_lost")

    clock.advance(LOST + 30)
    room_mod.sweep(ctx.conn, ctx.store)

    assert set(_states(ctx, wave).values()) == {"lost"}
    assert len(_events(ctx, "client_lost")) == 2


def test_a_small_wall_clock_correction_is_not_a_jump(room_server, monkeypatch):
    """An NTP slew of a few seconds is not a step: no rebase, no journal noise."""
    ctx = room_server["ctx"]
    _running_wave(room_server)
    clock = _install(monkeypatch)
    room_mod.sweep(ctx.conn, ctx.store)
    clock.advance(5)
    clock.jump(-20)
    room_mod.sweep(ctx.conn, ctx.store)
    assert not _events(ctx, "clock_jump")
    assert not _events(ctx, "client_lost")


@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_a_clock_step_does_not_wake_a_cloner_again(room_server, monkeypatch, step):
    """#1721's re-wake measures the open wave's age from `created_at`. A
    forward step made it 3 h old (a WoL every pulse until the cap failed the
    round); a backward step left it in the future, which #1771 made "due now"."""
    from test_room_rewake_1721 import _packets_to, _wave1_partial   # noqa: PLC0415

    ctx, woken = room_server["ctx"], room_server["woken"]
    clock, wave = _wave1_partial(room_server, monkeypatch)
    skew = time.time() - time.monotonic()
    if clockstep is not None:
        monkeypatch.setattr(clockstep, "_mono", lambda: clock.now.timestamp() - skew)
    room_mod.sweep(ctx.conn, ctx.store)
    sent = _packets_to(woken, CLONER1)
    rewakes = len(_events(ctx, "room_rewake"))

    clock.now += timedelta(seconds=step + 5)
    skew += step
    room_mod.sweep(ctx.conn, ctx.store)

    assert _packets_to(woken, CLONER1) == sent, "a clock step sent a WoL"
    assert len(_events(ctx, "room_rewake")) == rewakes
    assert any(wave in j for j in _events(ctx, "clock_jump"))


@pytest.mark.usefixtures("classrooms_on")      # the helpers make a classroom (v1.2)
@pytest.mark.parametrize("step", [STEP, -STEP], ids=["forward", "backward"])
def test_a_clock_step_does_not_fail_a_running_pull(server, monkeypatch, step):
    """A unicast pull closes as failed after SILENT_SECONDS without a report
    (`pulls._failure`, measured from `updated_at`). A forward step made a pull
    that was reporting look silent for 3 h -- `pull_failed` at once."""
    from server.rounds import pulls                                   # noqa: PLC0415
    from test_server_streams import (                          # noqa: PLC0415
        journal_of, pull, report as pull_report, second_class, session_row,
    )

    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert pull_report(server, pull_id, lab2["mac1"], "writing").json() == {"ok": True}
    clock = _Clock()

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is not None else clock.now.replace(tzinfo=None)

    monkeypatch.setattr(pulls, "datetime", _FakeDatetime)
    if clockstep is not None:
        monkeypatch.setattr(clockstep, "_mono", lambda: clock.mono)
    store = server["ctx"].store
    pulls.sweep(store)                                         # first sample
    clock.advance(10)
    clock.jump(step)

    pulls.sweep(store)

    assert session_row(server, pull_id)["state"] == "running", "a clock step failed a live pull"
    assert not journal_of(server, "pull_failed")
