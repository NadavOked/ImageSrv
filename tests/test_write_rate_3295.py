"""#3295: a drive that was behind in a chunk wave does not set the pace of the next one -- the fast drives do.

`--write-kbps` (what the receivers must WRITE) is 0.9 x the slowest drive's write check (`ChunkEngine._write_kbps`,
#1366). The check is 4MiB in three places (`agent/lib/restore/writecheck.sh`) and reads the drive's cache. The source
of the numbers here: #3034's measurement on cloner 2, 14/11 (`lab/vm-1114d` @ f1c38d930), APS-SL3N-512:
  - the write check said 209MB/s, so `--write-kbps` was 1504000 (0.9 x 209 = 188MB/s);
  - in ses_fae905e4 (700m) 10,546 chunks arrived in 306 s and 6,043 of them (57%) were skipped for the APS
    ("the drive was behind"): 4,503 durable. 4,503 x 4MiB / 306 s = 61MB/s -- a computation from those numbers,
    not a measurement of its own (the disk's diskstats said 50.8MB/s at 98% util in that wave).

Nadav, 15/11 on #3295: "ההפצה בקצב של הדיסקים המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך." `chunkcast send` sends
every target at one rate, so a drive that was behind in a past wave (`rate_outcome.behind`, `chunk_rate.sustained`)
is LEFT OUT of `--write-kbps`; what it wrote is kept for the prediction, not used as a ceiling. A drive with no
history counts as today. `FAST` is a number for the test (the other drives' check), not a measurement.

**Negative control:** with `server/rounds/chunksend.py`, `server/rounds/chunk_rate.py`, `server/app.py` and
`server/console/journal_he.py` back at the base, the tests marked (neg) fail on behaviour: `--write-kbps` is the APS's
1504000, `behind` is missing from the outcome, the drive behind is failed "slow", the app does not wire the hook.
The guards pass in both states.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import writerate
from server.rounds import chunk_rate, chunksend, drive_rates

from test_chunk_rate_engine_3163 import TimedRunner
from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_server_room import room_server  # noqa: F401 -- shared fixture

CHECK = 209                                    # MB/s: the APS's write check (#3034)
FAST = 400                                     # MB/s: the other drives' check -- a number for the test
DURABLE, SECONDS = 4503, 306.0                 # ses_fae905e4's first pass for the APS (#3034)
TODAY = int(CHECK * writerate.MARGIN) * 8000          # 1504000: what #3034 measured on the sender
PACE_OF_THE_FAST = int(FAST * writerate.MARGIN) * 8000      # 2880000


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _earlier(conn, sid: str, behind: dict | None) -> None:
    """A chunk wave that ended in `wave_rates`, by the server's own path (`decide`, then `chunk_rate.record` of the
    engine's outcome). `behind`: `rate_outcome.behind` of that wave."""
    drive_rates.decide(conn, sid, lambda: [{"serial": "B1", "model": "APS-SL3N-512", "mac": "b", "port": 2}], None,
                       adjust=lambda chosen, wave: chunk_rate.adjust(conn, chosen, wave))
    chunk_rate.record(conn, sid, {"chunks": 10546, "bytes": 10546 * chunksend.CHUNK_BYTES, "first_pass_s": SECONDS,
                                  "stream_zbytes": 10 ** 10, "lost_chunks": 3, "away": 0, "cloners": 1,
                                  "repair_rounds": 0, "ended": "send_done",
                                  **({"behind": behind, "paced_ms": {"wire": 0, "disk": int(SECONDS * 1000 * 0.74)}}     # #3530: a real wave's
                                     if behind is not None else {})})
    for serial in behind or {}:        # ... skipped chunks (`chunk_pass_counters`): what of the pass's 10,546 chunks the drive did not make durable
        skipped = 10546 - DURABLE
        conn.execute("INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, at)"
                     " VALUES (?,?,?,?,?,?,?,'2026-11-16T04:00:00+00:00')", (sid, 1, 1, "b", "receive", skipped, json.dumps({serial: skipped})))
    conn.commit()


APS_BEHIND = {"B1": {"bytes": DURABLE * chunksend.CHUNK_BYTES, "seconds": SECONDS}}


def _wave(rig, conn, checks=None):
    """A wave of the rig: B1 (the APS) checks at `CHECK`, A1 and C1 at `FAST` (or `checks`: mac -> MB/s), with
    `write_history` wired as `server.app` wires it. Returns (the engine, `--write-kbps` of its first pass)."""
    checks = checks or {"a": FAST, "b": CHECK, "c": FAST}
    engine = rig.make()
    engine.write_history = lambda serial: chunk_rate.write_history(conn, serial)
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=checks[mac])["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    return engine, int(cmd[cmd.index("--write-kbps") + 1])


def test_a_drive_that_was_behind_does_not_set_the_pace_the_fast_drives_do(rig, conn):
    """(neg) Nadav 15/11: the APS (checked at 209, wrote 61) is left out -- the stream goes at the others' 400."""
    _earlier(conn, "ses_earlier", APS_BEHIND)
    _engine, got = _wave(rig, conn)
    assert got == PACE_OF_THE_FAST, got
    assert got > TODAY
    assert rig.named("chunk_write_behind") == [f'{SID} "B1" check={CHECK}MB/s wrote=61MB/s in=ses_earlier left_out']


def test_what_the_drive_wrote_is_not_a_ceiling_for_the_stream(rig, conn):
    """(neg) Against the first version of #3316: the 61MB/s it sustained is never `--write-kbps` (432000); it is kept
    for the prediction."""
    _earlier(conn, "ses_earlier", APS_BEHIND)
    _engine, got = _wave(rig, conn)
    assert got != int(61 * writerate.MARGIN) * 8000 and got > TODAY, got
    assert chunk_rate.write_history(conn, "B1") == (61, "ses_earlier")


def test_a_drive_with_no_history_counts_as_today(rig, conn):
    """Guard, both states: no past wave -- the slowest check sets the pace, as before."""
    assert _wave(rig, conn)[1] == TODAY
    assert rig.named("chunk_write_behind") == []


def test_a_drive_that_kept_up_teaches_nothing(rig, conn):
    """Guard, both states: a past wave the drive was not behind in is no history."""
    _earlier(conn, "ses_earlier", {})
    assert _wave(rig, conn)[1] == TODAY


def test_a_pass_too_short_to_tell_teaches_nothing(rig, conn):
    """Guard, both states: under `MIN_SAMPLE_S` (60 s) the number is not a rate of the drive."""
    _earlier(conn, "ses_earlier", {"B1": {"bytes": DURABLE * chunksend.CHUNK_BYTES, "seconds": 30.0}})
    assert _wave(rig, conn)[1] == TODAY


def test_with_the_automatic_rate_switched_off_every_drive_counts(rig, conn):
    """Guard, both states: `learned_rate_enabled` off -- nothing learned is used."""
    _earlier(conn, "ses_earlier", APS_BEHIND)
    db.set_setting(conn, drive_rates.SETTING, "false")
    assert _wave(rig, conn)[1] == TODAY


def test_when_every_drive_was_behind_the_slowest_still_sets_the_pace(rig, conn):
    """Guard, both states: nobody is left to set it -- the slowest check, as before."""
    _earlier(conn, "ses_a", {"A1": APS_BEHIND["B1"]})
    _earlier(conn, "ses_b", APS_BEHIND)
    _earlier(conn, "ses_c", {"C1": APS_BEHIND["B1"]})
    assert _wave(rig, conn)[1] == TODAY


def test_a_hook_that_fails_or_answers_nonsense_counts_the_drive(rig):
    """Guard, both states: the learning never stops a wave."""
    for answer in (lambda serial: 1 / 0, lambda serial: "61", lambda serial: (0, "w"), lambda serial: (61, None)):
        rig.runner.commands.clear()
        engine = rig.make()
        engine.write_history = answer
        for mac, mbps in (("a", FAST), ("b", CHECK), ("c", FAST)):
            assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=mbps)["action"] == "receive"
        assert wait_for(lambda: len(rig.runner.commands) == 1)
        cmd = rig.runner.commands[0]
        assert int(cmd[cmd.index("--write-kbps") + 1]) == TODAY
        engine.stop()


def test_a_drive_behind_by_design_is_completed_not_failed_slow(rig, conn):
    """(neg) The APS misses chunks by design: 2 of the 10 that reached it are durable, 8 skipped. At the pace of the
    fast drives that reads 0.2 x 360 = 72MB/s, above the minimum speed (50, `write_floor`): it fetches what it misses.
    At the APS's own pace (188) the same pass reads 37MB/s and the drive is failed "slow" (`repair.slow_reason`)."""
    _earlier(conn, "ses_earlier", APS_BEHIND)
    engine, _got = _wave(rig, conn)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("c", 1, "ended", {"C1": ALL}, skipped={"C1": 0})
    assert rig.ask("b", 1, {"B1": [[0, 1]]}, skipped={"B1": 8})["action"] == "fetch"
    assert not any("לא עמד בקצב" in str(detail) for _e, detail, _s in rig.events), rig.events
    engine.stop()


def test_the_outcome_says_what_a_drive_that_was_behind_wrote_and_the_server_keeps_it(rig, conn):
    """(neg) `rate_outcome.behind`: the drive the receiver skipped chunks for -- its durable chunks x `CHUNK_BYTES` over
    the pass's seconds; a drive that skipped nothing is not in it. `chunk_rate.record` keeps it in the wave's sample."""
    clock = [100.0]
    rig.runner = TimedRunner(clock, costs=[5.0])
    engine = rig.make(clock=lambda: clock[0])
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL}, skipped={"A1": 0})
    rig.tell("b", 1, "ended", {"B1": [[0, 3]]}, skipped={"B1": 6})          # 4 durable, 6 skipped
    rig.tell("c", 1, "ended", {"C1": ALL}, skipped={"C1": 0})              # b is not failed any more: it fetches, and the pass is over
    assert wait_for(lambda: engine.rate_outcome(SID).get("chunks") == 10)
    got = engine.rate_outcome(SID).get("behind")
    assert got == {"B1": {"bytes": 4 * chunksend.CHUNK_BYTES, "seconds": 5.0}}, got
    drive_rates.decide(conn, SID, lambda: [{"serial": "B1", "model": None, "mac": "b", "port": 1}], None)
    chunk_rate.record(conn, SID, {**engine.rate_outcome(SID), "ended": "send_done"})
    stream = json.loads(conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (SID,)).fetchone()["stream"])
    assert stream["chunk"]["behind"] == got
    engine.stop()


def test_the_server_wires_the_write_history_of_its_chunk_engine(room_server):
    """(neg) `server.app` gives the engine `chunk_rate.write_history`: with no past wave, no history."""
    hook = getattr(room_server["ctx"].chunk, "write_history", None)
    assert hook is not None
    assert hook("S1") is None
