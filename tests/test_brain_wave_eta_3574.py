"""#3574 (K3, `server/brain/waveeta.py`): the slow drive of a chunk wave is learned from the engine's `chunk_write_behind`
line, and a serial with spaces (`t10.ATA     APS-SL3N-512 ... SICSD40302WL`, the APS of cloner 2) did not match
`(\\S+)`. K3 then predicted the wave without the APS and put the fast Samsung in its place: -54% to -55% of the real time
on metal, 17/11 (`lab/metal-1117-r19`, RD2-2 and IN2-1).

The two serials below are copied from `RD2-2-chunk/journal.txt` of that run. The positive tests go through the REAL
emitter (`ChunkEngine`, as `test_write_rate_3295.py` drives it) and then through `WaveEta`, so the line one writes is
the line the other reads; a format changed on one side only fails here.

**Negative control:** with `server/brain/waveeta.py` and `server/rounds/chunksend.py` BOTH back at `d6fb4201e`, the tests
marked (neg) fail on behaviour (`behind=0`, no `drive=<serial>` in the evidence, no log line). The tests marked (guard)
pass in both states: a serial without spaces, and the figures of the prediction itself.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import waveeta as W

from test_brain_wave_eta_3295 import MANIFEST, MB, _eta, _rows
from test_chunksend_2533 import SID, rig, wait_for   # noqa: F401 -- rig is a fixture

APS = "t10.ATA     APS-SL3N-512                            SICSD40302WL"      # RD2-2 journal.txt, line 7
SAMSUNG = "naa.5002538d42a58a2c"                                                # RD2-2 journal.txt, line 8
#: the line of RD2-2 as the engine wrote it BEFORE #3574 (journal.txt, line 7): the format the brain no longer reads
OLD_LINE = f"{SID} {APS} check=179MB/s wrote=49MB/s in=ses_27c0be8d counted:all_behind"


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "imagesrv.db")
    c.execute("PRAGMA foreign_keys = OFF")
    # one session only: a second `running` one is a UNIQUE violation (`sessions.kind`)
    c.execute("INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, wait_seconds, state,"
              " opened_by, created_at, last_join_at) VALUES (?, 'g', 'img', 'ROOM', 1, 1, 'running', 'x', 'now', 0)", (SID,))
    c.execute("INSERT INTO session_members (session_id, mac, state, updated_at) VALUES (?, 'b', 'writing', 'now')", (SID,))
    c.commit()
    return c


def _wave(rig, drive_a: str, drive_b: str, history: dict[str, tuple[int, str]]):
    """A wave of the real engine: one drive on cloner `a`, one on cloner `b`. `history`: serial -> (what it wrote, in which
    wave), as `chunk_rate.write_history` answers. Returns the `chunk_write_behind` lines the engine journaled."""
    rig.members = ["a", "b"]
    engine = rig.make()
    engine.write_history = history.get
    for mac, serial, check in (("a", drive_a, 209), ("b", drive_b, 179)):
        assert rig.tell(mac, 1, "listening", {serial: []}, mbps=check)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    return rig.named("chunk_write_behind")


def _predict(conn, lines, drive_a, drive_b):
    eta = _eta(conn, [0.0])
    for line in lines:
        eta.on_event("chunk_write_behind", line, SID)
    eta.started(SID, 700 * MB, {("a", drive_a): 209, ("b", drive_b): 179})
    return _rows(conn, "WAVE_ETA")[0]


@pytest.mark.parametrize("serial", [
    APS,
    'with "quotes" and \\ and a whole check=1MB/s wrote=2MB/s in=ses_x left_out line inside',
    "two\nlines",
    "  padded  ",
], ids=["aps-spaces", "quotes-backslash-and-a-fake-tail", "newline", "padding"])
def test_a_serial_with_any_character_is_the_drive_k3_waits_for(rig, conn, serial):
    """(neg) The APS is behind (49MB/s), the Samsung is not: the engine's line names the APS, whole, and the
    prediction carries it. Before #3574 the line did not match, `behind=0`, and the wave was predicted as its first pass."""
    lines = _wave(rig, SAMSUNG, serial, {serial: (49, "ses_27c0be8d")})
    assert len(lines) == 1, lines
    row = _predict(conn, lines, SAMSUNG, serial)
    ev = row["evidence"]
    assert "behind=1" in ev, (ev, lines)
    assert any(e.startswith(f"drive={serial}:49MB/s:http:") for e in ev), (ev, lines)
    assert serial in row["reason"], row["reason"]
    assert row["verdict"] == "PREDICT"


def test_a_serial_without_spaces_is_still_the_drive_k3_waits_for(rig, conn):
    """(guard, both states) The Samsung's own serial, behind: it matched before #3574 and still does."""
    lines = _wave(rig, "naa.5000000000000000", SAMSUNG, {SAMSUNG: (49, "ses_6881ed98")})
    ev = _predict(conn, lines, "naa.5000000000000000", SAMSUNG)["evidence"]
    assert "behind=1" in ev and any(e.startswith(f"drive={SAMSUNG}:49MB/s:") for e in ev), ev


def test_with_two_drives_behind_the_slow_one_is_the_one_the_eta_follows(rig, conn):
    """(neg) BOTH drives have a history and BOTH are slower than the pace of the wave (the slowest check, 179MB/s x 0.9 = 161MB/s): APS
    49MB/s, Samsung 150MB/s -- nobody keeps up, everyone counts. Before #3574 only the Samsung was learned, and the reason said "the
    slow drive is the Samsung". Now both are listed, the APS carries the prediction (its own T, far above the first pass) and the
    Samsung adds its own, smaller one. (RD2-2 had the Samsung at 248MB/s -- over the pace; since #3295 of 18/11 such a drive is not
    left out as "behind": `test_write_rate_all_history_3295.py`.)"""
    lines = _wave(rig, SAMSUNG, APS, {APS: (49, "ses_27c0be8d"), SAMSUNG: (150, "ses_6881ed98")})
    assert len(lines) == 2 and all(l.endswith("counted:all_behind") for l in lines), lines
    row = _predict(conn, lines, SAMSUNG, APS)
    ev = row["evidence"]
    assert "behind=2" in ev, ev
    want = W.predict(10_000, MANIFEST["total_compressed_bytes"], 700 * MB,
                     [W.Slow(APS, 49, W.HTTP), W.Slow(SAMSUNG, 150, W.HTTP)])
    t = {serial: tt for serial, _w, _s, tt in want.drives}
    assert t[APS] > 2 * want.t1_s and want.t1_s < t[SAMSUNG] < t[APS]      # the figures of the prediction (guard)
    assert f"eta_s={want.eta_s:.0f}" in ev, (ev, want)
    assert f"drive={APS}:49MB/s:http:{t[APS]:.0f}s" in ev, ev
    assert f"והכונן האיטי {APS} (49MB/s)" in row["reason"], row["reason"]


# --- a line that is not read is not swallowed ------------------------------------------------------------------------

UNREAD = [
    pytest.param(OLD_LINE, id="the-line-of-RD2-2-before-the-fix"),
    pytest.param("", id="empty"),
    pytest.param(f'{SID} "unterminated check=1MB/s wrote=2MB/s in=ses_x left_out', id="broken-quote"),
    pytest.param(f'{SID} "bad \\q escape" check=1MB/s wrote=2MB/s in=ses_x left_out', id="not-json"),
    pytest.param(f'{SID} "" check=1MB/s wrote=2MB/s in=ses_x left_out', id="no-serial"),
    pytest.param(f'{SID} "{SAMSUNG}" wrote=2MB/s in=ses_x left_out', id="a-field-missing"),
]


@pytest.mark.parametrize("detail", UNREAD)
def test_a_chunk_write_behind_line_k3_cannot_read_is_logged_with_its_name(conn, caplog, detail):
    """(neg) Before #3574 `got is None` was swallowed: no log, no record, and K3 predicted without the drive. It must not
    raise into the wave (the brain never does), and the prediction goes on without the drive -- but a warning says why."""
    eta = _eta(conn, [0.0])
    with caplog.at_level(logging.WARNING, logger="imagesrv.brain"):
        eta.on_event("chunk_write_behind", detail, SID)                 # does not raise
    said = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("chunk_write_behind" in m and SID in m for m in said), said
    eta.started(SID, 700 * MB, {("a", SAMSUNG): 209})
    assert "behind=0" in _rows(conn, "WAVE_ETA")[0]["evidence"]


def test_a_line_of_another_wave_is_not_used_and_says_so(conn, caplog):
    """(neg) The line is well formed, but names another wave: it is not this wave's drive."""
    eta = _eta(conn, [0.0])
    with caplog.at_level(logging.WARNING, logger="imagesrv.brain"):
        eta.on_event("chunk_write_behind", f'W-1 "{APS}" check=179MB/s wrote=49MB/s in=ses_x left_out', SID)
    assert any("chunk_write_behind" in r.getMessage() and "W-1" in r.getMessage() for r in caplog.records), caplog.records
    eta.started(SID, 700 * MB, {})
    assert "behind=0" in _rows(conn, "WAVE_ETA")[0]["evidence"]


def test_a_line_it_reads_is_not_logged_as_unread(rig, conn, caplog):
    """(guard, both states) The ordinary line leaves no warning behind."""
    lines = _wave(rig, "naa.5000000000000000", SAMSUNG, {SAMSUNG: (49, "ses_6881ed98")})
    eta = _eta(conn, [0.0])
    with caplog.at_level(logging.WARNING, logger="imagesrv.brain"):
        for line in lines:
            eta.on_event("chunk_write_behind", line, SID)
    assert [r for r in caplog.records if "chunk_write_behind" in r.getMessage()] == []
