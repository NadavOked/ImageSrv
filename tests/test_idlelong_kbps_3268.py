"""#3268 (Nadav 18/11) x #3295 -- what the sustained write check does to `--write-kbps`.

`--write-kbps` is 0.9 x the slowest write check of the drives with no history (`ChunkEngine._write_kbps`, #1366). The check
is now the **sustained** rate (the last half of 256MiB, `agent/lib/restore/idlelong.sh`), not the 12MiB in the drive's cache.
The server did not change: the pace follows the number the agent hands over. What this pins:

* a drive whose check is its real sustained rate sets the pace at that rate -- not at a cache rate it never keeps (the
  APS: 209 in the short check, 43-55 sustained: 188 MB/s of `--write-kbps` against a drive that writes 55);
* a drive with a history (#3295) is still left out of the pace, whatever its check says: the two memories do not fight;
* a disk the sustained check measures under the floor is not in the wave at all (the agent fails it by name before the
  stream: `write_speed_verdict`), so its number never reaches the pace.

Guards, not negative controls: the server code is the same on both sides; the numbers are the arithmetic of the existing rule.
(80 MB/s and 400 MB/s are numbers for the test, not measurements; 209 is the APS short check of #3034.)
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_chunksend_2533 import rig   # noqa: F401 -- a fixture
from test_server_room import room_server  # noqa: F401 -- shared fixture
from test_write_rate_3295 import APS_BEHIND, CHECK, FAST, TODAY, _earlier, _wave, conn   # noqa: F401 -- conn is a fixture

from server.brain import writerate
from server.rounds import chunksend

SUSTAINED = 80   # MB/s: a drive that writes 80 when it is written for minutes, above the floor of 74


def test_the_pace_follows_the_sustained_number_the_check_now_hands_over(rig, conn):
    _engine, got = _wave(rig, conn, checks={"a": FAST, "b": SUSTAINED, "c": FAST})
    assert got == int(SUSTAINED * writerate.MARGIN) * 8000, got
    assert got < TODAY, "the short check said 209: a pace the drive never kept, and the chunks it skipped were the price"


def test_a_drive_with_a_history_is_still_left_out_of_the_pace_whatever_its_check_says(rig, conn):
    _earlier(conn, "ses_earlier", APS_BEHIND)
    _engine, got = _wave(rig, conn, checks={"a": FAST, "b": SUSTAINED, "c": FAST})
    assert got == int(FAST * writerate.MARGIN) * 8000, got


def test_the_old_short_number_is_unchanged_for_a_disk_that_has_no_cache_to_hide_behind(rig, conn):
    _engine, got = _wave(rig, conn, checks={"a": CHECK, "b": CHECK, "c": CHECK})
    assert got == TODAY
