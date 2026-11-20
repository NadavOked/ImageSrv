"""#3269/#3295: a drive that was behind in a past chunk wave does not set the START rate (`--kbps`) of the next one.

#3316 leaves such a drive out of `--write-kbps` (`ChunkEngine._write_kbps`). `_start_rate` gave `wave_start`
(`chunk_rate.start`) every drawer's write check, and `start` takes the LOWEST of them as the measured start when the
learned base is stale (#3269) -- so the APS (checked 209MB/s, wrote ~61MB/s, #3034) still pulled the stream's start
down. Nadav 15/11 on #3295 (option ב'): "the slow one does not slow the wave". The same drives are left out here.
The numbers (`CHECK`, `FAST`, `APS_BEHIND`) are #3316's (`test_write_rate_3295.py`), not new measurements.

**Negative control:** with `server/rounds/chunksend.py` back at #3316 (`c359a21bb`), the test marked (neg) fails on
behaviour: the APS is in the write checks `wave_start` receives. The guards pass in both states.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import db
from server.rounds import chunk_rate

from test_chunksend_2533 import rig, wait_for   # noqa: F401 -- rig is a fixture
from test_write_rate_3295 import APS_BEHIND, CHECK, FAST, _earlier


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _probes(rig, history) -> dict:
    """One wave of the rig (A1 and C1 check at `FAST`, B1 at `CHECK`): the write checks `wave_start` was given."""
    seen: list[dict] = []
    engine = rig.make()
    engine.write_history = history
    engine.wave_start = lambda sid, bits, probes: seen.append(dict(probes)) or bits
    for mac, mbps in (("a", FAST), ("b", CHECK), ("c", FAST)):
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, mbps=mbps)["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    engine.stop()
    assert len(seen) == 1, seen
    return seen[0]


ALL_THREE = {("a", "A1"): FAST, ("b", "B1"): CHECK, ("c", "C1"): FAST}


def test_a_drive_that_was_behind_does_not_set_the_start_rate(rig, conn):
    """(neg) the APS was behind in ses_earlier: its 209MB/s check is not a candidate for the start -- the fast ones are."""
    _earlier(conn, "ses_earlier", APS_BEHIND)
    got = _probes(rig, lambda serial: chunk_rate.write_history(conn, serial))
    assert got == {("a", "A1"): FAST, ("c", "C1"): FAST}, got


def test_a_drive_with_no_history_counts_as_today(rig, conn):
    """Guard, both states: no past wave -- every drawer's check, as before."""
    assert _probes(rig, lambda serial: chunk_rate.write_history(conn, serial)) == ALL_THREE


def test_when_every_drive_was_behind_all_of_them_count(rig, conn):
    """Guard, both states: nobody is left to set it -- every check, as before (like `_write_kbps`)."""
    _earlier(conn, "ses_a", {"A1": APS_BEHIND["B1"]})
    _earlier(conn, "ses_b", APS_BEHIND)
    _earlier(conn, "ses_c", {"C1": APS_BEHIND["B1"]})
    assert _probes(rig, lambda serial: chunk_rate.write_history(conn, serial)) == ALL_THREE


def test_a_history_hook_that_fails_counts_the_drive(rig):
    """Guard, both states: the learning never stops a wave, nor changes what it measured."""
    assert _probes(rig, lambda serial: 1 / 0) == ALL_THREE
