"""#3173: the general claims of the old sender's tests, on the engine that is left.

`tests/test_sender*.py` tested `udp-sender` (`server/rounds/sender.py`) and go with it. Most of what they
held was that program's own command line, its port check and its log. The claims here are not: they are
about the server, and until this file they had no twin on the chunk engine (`server/rounds/chunksend.py`).
Each test says which old test its claim comes from, and what in the production code would have to break
for it to fail -- the planned negative control (written from reading the code, not yet run).

Two rigs: `rig` (`test_chunksend_2533`: the engine alone, a fake `chunkcast`, and members a/b/c that speak
only when the test says so), and `room_server` (the whole app, `fakesend.Recorder` as the sender, and the
cloners speaking HTTP through `fakesend.Cloners`).
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import hygiene
from native import requires_native

pytest.importorskip("fastapi")

from server.core import ports as product_ports
from server.db import connect, now_iso
from server.library.images import ImageLibrary
from server.rounds import chunkctx, chunklog, chunksend
from server.rounds import room as rooms
from server.rounds.sessions import SessionStore

import chunkcast_rig as C
from conftest import HANG_S, INDEX_CHUNKS, MANIFEST_256, write_image
from fakesend import Cloners, Recorder, arg
from test_chunksend_2533 import ALL, SID, Lib, Runner, _TwoParts, rig, wait_for  # noqa: F401 -- `rig` is a fixture
from test_server_room import (  # noqa: F401 -- `room_server` is a fixture
    CLONER1, CLONER2, cloner_hello, report, room, room_body, room_server,
)

PARTS = [p["index"] for p in MANIFEST_256["partitions"] if p.get("file")]
FIRST_PART, LATER_PART = PARTS[0], PARTS[1]

#: The distribution ports. This is the only place in `tests/` they may appear, and the scan below skips
#: the line by the mark at its end.
#: Trident's data and control ports in production (#3871); udpcast's 9000/9001 went with it (#3173).
PRODUCTION_PORTS = (product_ports.TRIDENT_DATA, product_ports.TRIDENT_CONTROL)

#: From here up a portbase cannot meet a port anything real listens on.
HIGH_PORT_FLOOR = 20000


def _bare_engine(tmp_path, **kw):
    """An engine that is built and never started."""
    return chunksend.ChunkEngine(Lib(tmp_path), live_members=lambda sid: [], lost_wait=lambda: 1,
                                 write_floor=lambda: 50, ledger=C.MemLedger(), **kw)


def _parts_sent(rig) -> list[str]:
    return [arg(cmd, "--part") for cmd in rig.runner.commands]


def _sending(rig, engine):
    """Every member listens to the first partition; the process of its first pass, once the engine ran it."""
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.processes) == 1), engine.status()
    return rig.runner.processes[0]


def _one_partition(rig, engine, part: int) -> None:
    """`part` on the air once and ended whole by every member (pass 1, nothing to repair)."""
    assert wait_for(lambda: (engine.status() or {}).get("partition") == part), engine.status()
    before = len(rig.runner.commands)
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, part=part)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == before + 1), rig.runner.commands
    for mac in rig.members[:-1]:
        rig.tell(mac, 1, "ended", {mac.upper() + "1": ALL}, part=part)
    for mac in reversed(rig.members):
        assert rig.ask_part(mac, part, {mac.upper() + "1": ALL})["action"] == "done"


class _FiveThenThree(_TwoParts):
    """The two partitions of `_TwoParts`, with 5 before 3 in the manifest."""

    def get(self, image_id):
        got = super().get(image_id)
        got["partitions"] = list(reversed(got["partitions"]))
        return got


class _NoImage(Lib):
    def get(self, image_id):
        return None


class _FileGone(Lib):
    """Partitions 3 and 5; the index of 5 is in the library, its file is not."""

    def __init__(self, root):
        super().__init__(root)
        (root / "p5.gone.pcl.zst.idx").write_text((root / "p3.windows.pcl.zst.idx").read_text(), encoding="ascii")

    def get(self, image_id):
        got = super().get(image_id)
        got["partitions"] = [got["partitions"][0], {**got["partitions"][0], "index": 5, "file": "p5.gone.pcl.zst"}]
        return got


# --- the distribution ports (test_sender_ports, #156) ------------------------------------------------------------


def test_the_run_assigns_a_high_random_portbase_to_every_engine(tmp_path):
    """From test_sender_ports: an engine built without asking for a portbase gets the run's, never the
    distribution's -- the path that left an orphan sender holding the server's ports for a day.

    Breaks when: `hygiene.assign_test_portbase` sets another module's default (the silent no-op #3173 warned
    of), or `ChunkEngine.__init__` stops reading `chunkctx.DEFAULT_PORTBASE`."""
    assigned = getattr(hygiene, "test_portbase", None)
    assert assigned is not None, "the run assigned no portbase to the tests (#156)"
    assert assigned >= HIGH_PORT_FLOOR and assigned not in PRODUCTION_PORTS
    engine = _bare_engine(tmp_path)
    assert engine.port == assigned + chunksend.PORT_OFFSET
    distribution = range(chunkctx.PRODUCTION_PORTBASE, chunkctx.PRODUCTION_PORTBASE + chunksend.PORT_OFFSET + 2)
    assert engine.port not in distribution and engine.port + 1 not in distribution    # the data and the control port


def test_the_engine_that_create_app_builds_also_gets_the_run_portbase(server):
    """From test_sender_ports: `create_app` builds an engine of its own without asking -- the engine of #156.

    Breaks when: `create_app` hands the chunk engine a fixed portbase."""
    assigned = getattr(hygiene, "test_portbase", None)
    assert assigned is not None, "the run assigned no portbase to the tests (#156)"
    assert server["ctx"].chunk.port == assigned + chunksend.PORT_OFFSET


def test_no_python_file_under_tests_asks_for_a_production_port():
    """From test_sender_ports, as it was: a source scan -- no line of Python in the suite names the
    distribution ports, unless it is marked as meaning to. Positive evidence, not a promise: a test that
    names the port tomorrow is caught here, and not in front of a classroom.

    The boundary is exact: every `*.py` under `tests/`, sub-folders included, and nothing else.

    Breaks when: any test file names one of the ports without the mark."""
    pattern = re.compile(r"\b(?:%s)\b" % "|".join(str(p) for p in PRODUCTION_PORTS))
    root = Path(__file__).parent
    scanned, offenders = 0, []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line) and "פורט-הפצה-מכוון" not in line:
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    # a scanner that scanned nothing looks exactly like a scanner with nothing to find
    assert scanned > 50, f"the scan covered only {scanned} files"
    assert not offenders, "tests that name the distribution port (#156):\n" + "\n".join(offenders)


# --- the waits and the cap ---------------------------------------------------------------------------------------


def test_a_later_partition_never_waits_less_than_the_first():
    """From test_sender::test_a_first_wait_longer_than_ten_minutes_is_not_shortened_for_later_partitions (#957):
    ten minutes at least for a later partition, and never less than the first's; the settling wait holds it.

    Breaks when: `chunkctx.later_max_wait` returns `LATER_MAX_WAIT` alone (900 becomes 600)."""
    assert (chunkctx.later_max_wait(120), chunkctx.later_start_timeout(120, 180)) == (600, 780)
    assert (chunkctx.later_max_wait(900), chunkctx.later_start_timeout(900, 180)) == (900, 1080)


def test_the_operators_rate_cap_is_held_from_the_builder_or_the_environment(tmp_path, monkeypatch):
    """From test_sender::test_the_bitrate_cap_reaches_the_command_when_configured and ::..._is_tunable_by_env:
    the cap is there only when it was set, and `IMAGESRV_MAX_BITRATE` sets it without a rebuild.

    Breaks when: `ChunkEngine.__init__` drops `or chunkctx.env_max_bitrate()`, or sets a cap of its own."""
    monkeypatch.delenv("IMAGESRV_MAX_BITRATE", raising=False)
    assert _bare_engine(tmp_path).max_bitrate is None
    assert _bare_engine(tmp_path, max_bitrate="200m").max_bitrate == "200m"
    monkeypatch.setenv("IMAGESRV_MAX_BITRATE", "150m")
    assert _bare_engine(tmp_path).max_bitrate == "150m"
    assert _bare_engine(tmp_path, max_bitrate="200m").max_bitrate == "200m"      # the builder's wins


# --- the partitions ----------------------------------------------------------------------------------------------


def test_one_pass_a_partition_in_the_order_of_the_manifest(rig, tmp_path):
    """From test_sender::test_one_transmission_per_partition_in_manifest_order. Here 5 is before 3 in the
    manifest, so the order is the manifest's and not the partition numbers'.

    Breaks when: `_send` sorts the partitions, or sends a partition twice."""
    engine = rig.make(lib=_FiveThenThree(tmp_path))
    _one_partition(rig, engine, 5)
    _one_partition(rig, engine, 3)
    assert wait_for(lambda: engine.status()["state"] == "done"), engine.status()
    assert _parts_sent(rig) == ["5", "3"]
    assert [arg(cmd, "--pass") for cmd in rig.runner.commands] == ["1", "1"]
    assert [Path(arg(cmd, "--file")).name for cmd in rig.runner.commands] == ["p5.data.pcl.zst", "p3.windows.pcl.zst"]


def test_a_partition_whose_sender_failed_is_the_last_one_sent(rig, tmp_path):
    """From test_sender::test_a_failed_partition_stops_the_round: no partition after a failure, the code is
    named, and a fast failure is not blamed on nobody lining up.

    Breaks when: `_send_pass` returns True for a non-zero code."""
    rig.runner = Runner(code=3)
    engine = rig.make(lib=_TwoParts(tmp_path))
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: engine.status()["state"] == "failed"), engine.status()
    error = engine.status()["error"]
    assert "קוד 3" in error and "p3.windows.pcl.zst" in error, error
    assert "אף מחשב לא התייצב" not in error, error
    time.sleep(0.3)
    assert _parts_sent(rig) == ["3"] and rig.named("send_done") == []


def test_an_image_that_is_not_in_the_library_fails_before_anything_is_sent(rig, tmp_path):
    """From test_sender::test_a_missing_image_fails_before_spawning_anything.

    Breaks when: `_send` stops checking `self.library.get(...) is None`."""
    engine = rig.make(lib=_NoImage(tmp_path))
    assert wait_for(lambda: engine.status()["state"] == "failed"), engine.status()
    assert "img_1" in engine.status()["error"] and "לא נמצא בספרייה" in engine.status()["error"]
    assert rig.runner.commands == [] and rig.named("send_start") == []


def test_a_partition_file_missing_from_the_library_fails_by_its_name_before_a_byte(rig, tmp_path):
    """From test_sender::test_a_missing_partition_file_fails_loudly. The old sender sent the partitions
    before it; the chunk engine checks every partition before the first byte, and its index is not enough.

    Breaks when: `chunkformat.read_index` vouches for an index whose file is gone."""
    engine = rig.make(lib=_FileGone(tmp_path))
    assert wait_for(lambda: engine.status()["state"] == "failed"), engine.status()
    error = engine.status()["error"]
    assert "p5.gone.pcl.zst" in error and "דבר לא שודר" in error, error
    assert rig.runner.commands == []


# --- stopping (#439, #773, #2427) --------------------------------------------------------------------------------


def test_a_stale_stop_does_not_stop_the_wave_that_is_on_the_air(rig):
    """From test_sender::test_a_stale_stop_does_not_kill_a_newer_session (#773): a late close of another wave
    is a no-op, and the right stop still works.

    Breaks when: `ChunkEngine.stop` drops the `wave.session_id != session_id` guard."""
    rig.runner = Runner(block=True)
    engine = rig.make()
    process = _sending(rig, engine)
    assert engine.stop("S-older") is None
    time.sleep(0.2)
    assert process.poll() is None, "a stop of another wave killed the one on the air"
    assert not engine._stop.is_set(), "a stop of another wave raised this wave's stop flag"
    assert engine.status()["state"] == "sending" and engine.status()["session_id"] == SID
    assert engine.stop(SID) is None
    assert process.poll() == -15
    assert wait_for(lambda: engine.status()["state"] == "stopped")


def test_a_stop_that_names_no_wave_stops_whatever_is_on_the_air(rig):
    """From test_sender::test_stop_with_no_session_id_still_stops_unconditionally: the guard of #773 blocks
    another wave's id, not `None` (an explicit close, `close()`).

    Breaks when: the guard compares `None` too (`session_id is not None and` dropped)."""
    rig.runner = Runner(block=True)
    engine = rig.make()
    process = _sending(rig, engine)
    assert engine.stop() is None
    assert process.poll() == -15
    assert wait_for(lambda: engine.status()["state"] == "stopped")
    assert rig.named("send_stopped") and rig.named("send_failed") == []


def test_a_stop_mid_pass_ends_the_wave_as_stopped_with_no_later_partition(rig, tmp_path):
    """From test_sender::test_stopping_terminates_the_running_transmission: the pass is terminated, the
    wave is `stopped` (not failed), and the next partition never goes out.

    Breaks when: `stop` only terminates the process and does not raise `_stop` (the pass then ends with -15
    and the wave is `failed`)."""
    rig.runner = Runner(block=True)
    engine = rig.make(lib=_TwoParts(tmp_path))
    process = _sending(rig, engine)
    assert engine.stop(SID) is None
    assert process.poll() == -15
    assert wait_for(lambda: engine.status()["state"] == "stopped"), engine.status()
    assert wait_for(lambda: not engine._thread.is_alive())
    assert _parts_sent(rig) == ["3"]


def test_a_stop_between_partitions_launches_nothing_after_it_and_frees_the_engine(rig, tmp_path):
    """From test_sender_races_2427::test_a_stop_between_partitions_launches_nothing_after_it: `stop` while
    the engine waits for the next partition's listeners -- nothing alive, so None -- launches nothing, and
    the next wave starts.

    Breaks when: `_wait_marks_inner` stops checking `_stop` (the engine waits on, and the slot stays taken)."""
    engine = rig.make(lib=_TwoParts(tmp_path))
    _one_partition(rig, engine, 3)
    assert wait_for(lambda: engine.status()["partition"] == 5), engine.status()
    assert engine.stop(SID) is None
    assert wait_for(lambda: not engine._thread.is_alive()), engine.status()
    assert engine.status()["state"] == "stopped"
    assert _parts_sent(rig) == ["3"], "a partition was launched after stop() said nothing is alive"
    assert engine.start({"id": "S-2", "image_id": "img_1"}) is None
    assert engine.status()["session_id"] == "S-2"


def test_a_sender_that_ignores_sigterm_is_killed_and_stop_has_the_evidence(rig, monkeypatch):
    """From test_sender::test_stop_escalates_to_sigkill_when_sigterm_is_ignored (#439): TERM alone leaves an
    orphan; KILL follows, and None only once the process is seen dead.

    Breaks when: `ChunkEngine.stop` drops the `process.kill()` escalation."""
    monkeypatch.setattr(chunkctx, "STOP_TERM_WAIT", 0.05)
    rig.runner = Recorder(block=True, ignore_term=True)
    engine = rig.make()
    process = _sending(rig, engine)
    assert engine.stop(SID) is None
    assert process.terminated and process.killed
    assert process.poll() is not None


def test_a_sender_that_survives_sigkill_is_named_by_its_pid_not_reported_stopped(rig, monkeypatch):
    """From test_sender::test_stop_does_not_claim_success_when_the_pid_is_still_alive (#439): "we sent
    SIGKILL" is not "it died".

    Breaks when: `ChunkEngine.stop` returns None after the kill without the evidence of `chunkctx.reaped`."""
    monkeypatch.setattr(chunkctx, "STOP_TERM_WAIT", 0.05)
    monkeypatch.setattr(chunkctx, "STOP_KILL_WAIT", 0.05)
    rig.runner = Recorder(block=True, ignore_term=True, ignore_kill=True)
    engine = rig.make()
    process = _sending(rig, engine)
    try:
        assert engine.stop(SID) == process.pid
        assert process.killed and process.poll() is None
    finally:
        process.release()
    assert wait_for(lambda: engine.status()["state"] == "stopped"), engine.status()


def test_a_second_wave_is_refused_by_both_names_while_the_first_is_on_the_air(rig):
    """From test_sender_races_2427::test_start_while_another_sender_is_alive_returns_the_reason.

    Breaks when: `ChunkEngine.start` stops checking that its thread is alive (it would replace the wave)."""
    rig.runner = Runner(block=True)
    engine = rig.make()
    _sending(rig, engine)
    refused = engine.start({"id": "S-2", "image_id": "img_1"})
    assert refused and SID in refused and "S-2" in refused, refused
    assert engine.status()["session_id"] == SID


def test_an_exception_in_the_sending_thread_is_a_failure_by_name(tmp_path):
    """From test_sender_races_2427::test_an_exception_in_the_sender_thread_is_a_named_failure: not a wave
    that says `starting` forever.

    Breaks when: `ChunkEngine._run` loses its `except` (the thread dies and nobody says why)."""
    events: list[str] = []

    def on_event(event, detail, sid):
        if event == "send_start":
            raise sqlite3.OperationalError("database is locked")
        events.append(event)

    runner = Runner(block=True)
    engine = chunksend.ChunkEngine(
        Lib(tmp_path), live_members=lambda sid: ["a"], lost_wait=lambda: 1, write_floor=lambda: 50,
        runner=runner, max_wait=1, start_timeout=1, wave_bitrate=lambda s: "700m", ledger=C.MemLedger(),
        on_event=on_event)
    engine.note_agent("a", ["chunk"])
    try:
        assert engine.start({"id": SID, "image_id": "img_1"}) is None
        assert wait_for(lambda: not engine._thread.is_alive())
        status = engine.status()
        assert status["state"] == "failed", status
        assert "database is locked" in (status["error"] or ""), status
        assert "send_failed" in events and runner.processes == []
    finally:
        engine.close()


def test_a_crash_after_chunkcast_started_stops_it(rig):
    """From test_sender_races_2427::test_a_crash_after_udp_sender_started_reaps_it: an exception after the
    process is up leaves no orphan sender.

    Breaks when: `ChunkEngine._run` drops `self.stop(w.session_id)` from its `except`."""
    rig.runner = Runner(block=True)
    engine = rig.make()

    def boom(*_args, **_kwargs):
        raise RuntimeError("sampling broke")

    engine._wait_pass = boom
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: not engine._thread.is_alive()), engine.status()
    assert engine.status()["state"] == "failed" and "sampling broke" in engine.status()["error"]
    assert rig.runner.processes and rig.runner.processes[0].poll() == -15


# --- a question to the operator (#2202) --------------------------------------------------------------------------


def test_closing_the_wave_while_it_is_held_for_a_question_sends_nothing(rig):
    """From test_sender::test_closing_the_round_during_the_hold_never_starts_udp_sender: `send_stopped`, and no
    process. (c is the machine at the question; a and b already listen.)

    Breaks when: `_send` goes on to `_send_pass` when the listening wait answers None."""
    engine = rig.make(pending_prompts=lambda sid: ["c"])
    for mac in ("a", "b"):
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_held_prompt")), rig.events
    engine.stop(SID)
    assert wait_for(lambda: engine.status()["state"] == "stopped"), engine.status()
    assert rig.runner.commands == [] and rig.named("send_failed") == []


def test_a_question_after_the_pass_went_on_the_air_does_not_stop_it(rig):
    """From test_sender::test_a_question_after_the_transfer_started_does_not_kill_it: the cloners already
    write, and another machine's question is no reason to cut them. A guard: it holds on both sides.

    Breaks when: the engine starts asking `pending_prompts` during a pass and stops the sender for it."""
    asks: list[str] = []
    rig.runner = Recorder(block=True)
    engine = rig.make(pending_prompts=lambda sid: list(asks))
    process = _sending(rig, engine)
    asks.append("c")
    time.sleep(0.3)
    assert not process.terminated, "a question stopped a pass that was already on the air"
    asks.clear()
    process.release()
    for mac in rig.members[:-1]:
        rig.tell(mac, 1, "ended", {mac.upper() + "1": ALL})
    for mac in reversed(rig.members):
        assert rig.ask(mac, 1, {mac.upper() + "1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done"), engine.status()


# --- who is waited for (#957, #1430) -----------------------------------------------------------------------------


def _later_partition_with_c_missing(rig, tmp_path):
    """Partition 3 done by all three; for 5, a and b listen and c (alive) has not yet."""
    engine = rig.make(lib=_TwoParts(tmp_path))
    _one_partition(rig, engine, 3)
    assert wait_for(lambda: engine.status()["partition"] == 5), engine.status()
    for mac in ("a", "b"):
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, part=5)
    return engine


def test_a_live_cloner_that_has_not_lined_up_yet_holds_a_later_partition(rig, tmp_path):
    """From test_sender_quiet_member_1430::test_a_member_that_reports_is_counted_and_waited_for_600s (#957):
    a slow drawer reaches the next partition when it finished writing the last one; the partition does not
    leave without it. Here `max_wait` is 1 s and a later partition waits ten minutes.

    Breaks when: a later partition waits `max_wait` instead of `chunkctx.later_max_wait(...)`."""
    _later_partition_with_c_missing(rig, tmp_path)
    time.sleep(1.5)
    assert _parts_sent(rig) == ["3"], "the later partition left without a cloner that is alive"
    rig.tell("c", 1, "listening", {"C1": []}, part=5)
    assert wait_for(lambda: _parts_sent(rig) == ["3", "5"]), rig.runner.commands


def test_a_cloner_that_is_no_longer_alive_does_not_hold_a_later_partition(rig, tmp_path):
    """From test_sender::test_a_member_that_failed_is_not_waited_for_in_the_next_partition and
    test_sender_quiet_member_1430::test_a_quiet_member_does_not_hold_the_next_partition (#1430): the members
    are asked again on every poll, and one that is no longer alive (failed, or lost) is not waited for.

    Breaks when: `_wait_marks_inner` keeps the members it read first instead of asking `who()` again."""
    _later_partition_with_c_missing(rig, tmp_path)
    rig.members = ["a", "b"]                    # c failed, or was lost: the server no longer counts it alive
    assert wait_for(lambda: _parts_sent(rig) == ["3", "5"], 3), rig.runner.commands


def _wave_of_the_round(ctx) -> str:
    return ctx.conn.execute("SELECT wave_session_id FROM room_rounds ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def _room_wave_running(room_server, drawers: dict[str, list[str]]) -> str:
    """A room round of `drawers`, every cloner in its wave, the wave running. The wave's id."""
    deploy = room_server["deploy"]
    opened = deploy.post("/api/console/room", json=room_body(room_server, sum(map(len, drawers.values())), drawers))
    assert opened.status_code == 200, opened.text
    wave = _wave_of_the_round(room_server["ctx"])
    for mac, serials in drawers.items():
        cloner_hello(room_server["anon"], mac, serials)
    if room(deploy)["round"]["wave_state"] != "running":
        deploy.post("/api/console/room/start")
    assert wait_for(lambda: room(deploy)["round"]["wave_state"] == "running", 10), room(deploy)["round"]
    return wave


def test_a_cloner_that_failed_is_not_waited_for_in_the_next_partition(room_server):
    """From test_sender::test_a_member_that_failed_is_not_waited_for_in_the_next_partition (#1430, the VM lab
    15/10, `ses_989ab0ed`): one cloner of two reports `failed` after the first partition; the next one goes
    out to the other as soon as it listens -- not after ten minutes of waiting for the failed one.

    Breaks when: the `live_members` that `server.app` gives the engine counts a `failed` member."""
    recorder, anon = room_server["recorder"], room_server["anon"]
    drawers = {CLONER1: ["S1"], CLONER2: ["S2"]}
    wave = _room_wave_running(room_server, drawers)
    cloners = Cloners(anon, wave, drawers)
    cloners.listen(FIRST_PART)
    assert wait_for(lambda: len(recorder.commands) == 1, 10), recorder.commands
    cloners.ended(FIRST_PART, INDEX_CHUNKS)
    report(anon, wave, CLONER2, {"sda": "failed"}, top="failed")
    cloners.listen(LATER_PART, macs=[CLONER1])
    assert wait_for(lambda: len(recorder.commands) == 2, 10), (recorder.commands, room_server["ctx"].chunk.status())
    assert arg(recorder.commands[1], "--part") == str(LATER_PART)


# --- the app: refusing and closing (#2427, #439) -----------------------------------------------------------------


def test_a_wave_the_engine_refuses_fails_its_round_by_name(room_server):
    """From test_sender_races_2427::test_a_refused_start_fails_the_round_by_name: a refused start is a failed
    wave with the reason, not a quiet `running` (the engine here is busy with a wave it never let go of).

    Breaks when: `on_running` in `server.app` ignores what `engine.start` answered."""
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    chunk = ctx.chunk
    chunk._wave = chunksend._Wave("ses_busy", "img_7f3a91", chunk.key_for("ses_busy"))
    chunk._thread = threading.Thread(target=lambda: chunk._stop.wait(HANG_S), daemon=True)
    chunk._thread.start()
    try:
        opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]}))
        assert opened.status_code == 200, opened.text
        wave = _wave_of_the_round(ctx)
        cloner_hello(room_server["anon"], CLONER1, ["S1"])
        room_server["deploy"].post("/api/console/room/start")

        def row():
            return ctx.conn.execute("SELECT state, failed_reason FROM sessions WHERE id = ?", (wave,)).fetchone()

        assert wait_for(lambda: row()["state"] == "failed", 10), dict(row())
        assert "ses_busy" in row()["failed_reason"] and wave in row()["failed_reason"], dict(row())
        assert recorder.commands == []
    finally:
        chunk.stop()
        chunk._thread.join(2)


def test_closing_a_wave_is_not_ok_while_its_chunkcast_survives_the_stop(room_server, monkeypatch):
    """From test_sender_races_2427::test_closing_a_classroom_round_is_not_ok_while_udp_sender_survives: the
    close of a session (`/api/console/sessions/<id>/close`) says the sender is alive, by its PID, and is not
    200. The room round's own close has its twin in test_chunk_nic_gate_2533.

    Breaks when: the console's session close stops looking at what `ctx.chunk.stop` answered."""
    monkeypatch.setattr(chunkctx, "STOP_TERM_WAIT", 0.05)
    monkeypatch.setattr(chunkctx, "STOP_KILL_WAIT", 0.05)
    recorder = room_server["recorder"]
    recorder.block, recorder.ignore_term, recorder.ignore_kill = True, True, True
    wave = _room_wave_running(room_server, {CLONER1: ["S1"]})
    Cloners(room_server["anon"], wave, {CLONER1: ["S1"]}).listen(FIRST_PART)
    assert recorder.spawned.wait(timeout=HANG_S)
    process = recorder.processes[0]
    try:
        closed = room_server["deploy"].post(f"/api/console/sessions/{wave}/close",
                                            json={"confirm_name": MANIFEST_256["name"]})
        assert closed.status_code == 500, closed.text
        assert "chunkcast send עדיין רץ" in closed.json()["detail"], closed.text
        assert str(process.pid) in closed.json()["detail"], closed.text
    finally:
        process.release()


def test_close_round_racing_the_next_wave_leaves_no_open_wave(tmp_path, monkeypatch):
    """From test_sender_races_2427::test_close_round_racing_the_next_wave_leaves_no_open_wave: T1 is
    `close_round` reading the round (wave W1); T2 is `_finish_wave` opening W2 in W1's place, between T1's
    read and its CAS to `closing`. W2 must not stay `open` holding the broadcast slot. The context has the
    chunk engine and no old sender (`ServerContext.sender` is gone with #3173).

    Breaks when: `close_round` reads the round's wave before its CAS and not after."""
    from server.agent.registry import CLONERS_GROUP

    image = MANIFEST_256["id"]
    write_image(tmp_path / "images", MANIFEST_256)
    library = ImageLibrary(tmp_path / "images")
    db = connect(tmp_path / "room.db")
    db.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES (?, ?, 'cloner')", (CLONERS_GROUP, "שיכפול"))
    db.commit()
    engine = chunksend.ChunkEngine(library, live_members=lambda sid: [], lost_wait=lambda: 1, write_floor=lambda: 50,
                                   runner=Runner(block=True), ledger=C.MemLedger())
    store = SessionStore(db, on_running=engine.start, on_closed=lambda sid: engine.stop(sid))
    w1 = store.open(CLONERS_GROUP, image, "ROOM", 1, "", wait_seconds=300)
    db.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, wave_session_id, opened_by, created_at)"
               " VALUES ('room_2427', ?, 2, 'active', ?, 'noc', ?)", (image, w1, now_iso()))
    db.commit()

    real_visible = rooms.visible_round
    box: dict = {}

    def visible_then_next_wave(conn):
        row = real_visible(conn)
        w2 = store.open(CLONERS_GROUP, image, "ROOM", 1, "", wait_seconds=300, replaces=w1)
        assert rooms.update_one(conn, "UPDATE room_rounds SET wave_session_id = ?, wave_number = wave_number + 1"
                                " WHERE id = 'room_2427' AND state = 'active' AND wave_session_id = ?", (w2, w1))
        conn.commit()
        box["w2"] = w2
        return row

    monkeypatch.setattr(rooms, "visible_round", visible_then_next_wave)
    ctx = SimpleNamespace(conn=db, store=store, chunk=engine, library=library)       # as `ServerContext`
    try:
        rooms.close_round(ctx, "noc", MANIFEST_256["name"])
        assert db.execute("SELECT state FROM room_rounds WHERE id = 'room_2427'").fetchone()["state"] == "closed"
        assert db.execute("SELECT state FROM sessions WHERE id = ?", (box["w2"],)).fetchone()["state"] == "closed"
        # the slot is free: a new broadcast opens
        assert store.open(CLONERS_GROUP, image, "ROOM", 1, "", wait_seconds=300)
    finally:
        engine.close()


# --- the logs of the sender --------------------------------------------------------------------------------------


def test_the_same_partition_name_on_two_disks_keeps_two_logs(tmp_path, monkeypatch):
    """From test_sender::test_same_partition_basename_on_different_disks_keeps_both_logs: two disks may carry
    the same file name; the disk's part of the path keeps the evidence of each.

    Breaks when: `chunklog.log_path` derives the log from the file name alone."""
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    relative_a, relative_b = Path("sda") / "p3.win.pcl.zst", Path("sdb") / "p3.win.pcl.zst"
    log_a = chunklog.log_path(tmp_path / MANIFEST_256["id"] / relative_a)
    log_b = chunklog.log_path(tmp_path / MANIFEST_256["id"] / relative_b)
    assert log_a == chunklog.log_path(relative_a) and log_b == chunklog.log_path(relative_b)
    assert log_a != log_b
    log_a.write_text("evidence from sda", encoding="utf-8")
    log_b.write_text("evidence from sdb", encoding="utf-8")
    assert log_a.read_text(encoding="utf-8") == "evidence from sda"
    assert log_b.read_text(encoding="utf-8") == "evidence from sdb"


#: 16 times a typical pipe buffer (64KB): past any buffer of Linux.
OUTPUT_BYTES = 1 << 20
CHILD = ("import sys\n"
         f"for _ in range({OUTPUT_BYTES // 1024}):\n"
         "    sys.stdout.write('p' * 1023 + '\\n')\n"
         "sys.stdout.flush()\n")


@requires_native(posix=True, why="nice/ionice and a real child process")
def test_the_real_runner_does_not_stall_on_a_chatty_child(tmp_path, monkeypatch):
    """From test_sender_pipe_1912 (#1912, a guard for #22): the sender's output goes to the partition's log,
    not to a pipe nobody reads -- a child that prints past the pipe's buffer finishes. Here a python child
    prints 1MB in the place of `chunkcast send`.

    Breaks when: `chunklog.run_process` gives the child `stdout=subprocess.PIPE` (the child blocks and the
    wait expires)."""
    monkeypatch.setattr(chunklog, "LOG_DIR", tmp_path)
    part = tmp_path / "img_test" / "p2.img"
    proc = chunklog.run_process([sys.executable, "-c", CHILD, "--file", str(part)])
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("the child stalled on its own output for 60 s -- #22 is back "
                    f"(stdout={'PIPE' if proc.stdout else 'file'})")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert rc == 0
    assert proc.stdout is None, "stdout is a pipe -- nobody reads it"
    written = chunklog.log_path(part)
    assert written.parent == tmp_path
    assert written.stat().st_size >= OUTPUT_BYTES, "the output did not reach the partition's log"
