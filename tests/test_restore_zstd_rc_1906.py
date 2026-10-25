"""#1906 -- the single restore reads zstd's exit code, not only partclone's.

busybox ash has no ``pipefail``: ``zstd -dc < feed | partclone ...; echo $?``
is partclone's code alone. A zstd killed after fanout fed it everything (OOM),
and a partclone that exits 0 on a short input, gave ``pipe.rc=0`` -- and the
sha256 of the compressed bytes matched, because fanout hashed what arrived.
The partition was done. #527 fixed this for the drawers only (``zstd.rc``,
verdict.sh); the station, the GUI restore and the unicast pull ran the old line.

The fix is one helper for both paths (``restore_pipe``, drawers.sh), which
records ``zstd.rc`` beside the pipe's own code; ``restore_partition`` fails a
partition whose zstd did not exit 0 -- or whose code was not recorded.
"""

from __future__ import annotations

from test_agent import BASH
from native import requires_native
from test_stream_honesty import GOOD_FANOUT, GOOD_SHA, station_run
from test_timeouts import log_of

pytestmark = requires_native(("bash", BASH))

#: zstd that "decompresses" all of it -- and then dies, as an OOM kill does.
ZSTD_DIES_AT_THE_END = '#!/bin/sh\ncat\nexit 137\n'
#: partclone that takes whatever it got and says 0, as on a short input.
PARTCLONE_OK = '#!/bin/sh\ncat > /dev/null\nexit 0\n'


def test_a_zstd_that_died_fails_the_partition_even_when_partclone_said_0(tmp_path):
    run, out = station_run(tmp_path, GOOD_SHA, {"fanout": GOOD_FANOUT, "zstd": ZSTD_DIES_AT_THE_END,
                                                 "partclone.dd": PARTCLONE_OK})
    assert out.strip().endswith("rc=1"), (out, log_of(run))
    assert (run / "targets" / "sda" / "zstd.rc").read_text().strip() == "137"
    assert "write failed" in log_of(run), log_of(run)


def test_a_whole_write_still_passes(tmp_path):
    """The guard: zstd 0, partclone 0, the sha matches -- the partition is done."""
    run, out = station_run(tmp_path, GOOD_SHA, {"fanout": GOOD_FANOUT, "partclone.dd": PARTCLONE_OK})
    assert out.strip().endswith("rc=0"), (out, log_of(run))
    assert (run / "targets" / "sda" / "zstd.rc").read_text().strip() == "0"


def test_a_partclone_that_failed_still_fails(tmp_path):
    run, out = station_run(tmp_path, GOOD_SHA, {"fanout": GOOD_FANOUT,
                                                 "partclone.dd": '#!/bin/sh\ncat > /dev/null\nexit 3\n'})
    assert out.strip().endswith("rc=1"), (out, log_of(run))
