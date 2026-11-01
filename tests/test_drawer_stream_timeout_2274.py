"""A stream whose wait ran out is the stream's failure -- even when the sha matched (#2274).

Found in the public failure of #2248: the stream's subshell writes fanout.rc
only after its pipe has ended. When that took longer than the stall ceiling
(there: 25s injected before `fanout.rc`), wait_progress killed it and set
WAIT_TIMED_OUT. But fanout had already reached EOF and written a matching
sha, and the sha was asked first: the timeout was never judged. In the shared
shape drawer_feed_finish had returned before it looked at the decoder, so the
decoder's failure reached each drawer as its own -- "הכתיבה לדיסק נכשלה
(fanout: ok; partclone rc=0)" -- and an ATA capture against a healthy disk
(#440, #1598).

The wait ran out and the watched pipe was killed on its ceiling: its exit code
was never read, so there is no positive evidence it ended well (principle 5).
That is a stream timeout, for every drawer, with no evidence against a disk.

The delay here goes after the stream's fanout, before `fanout.rc` -- the same
place -- and the stall ceiling is 3s so it costs seconds, not minutes.
"""

from __future__ import annotations

import hashlib

import pytest

import test_drawer_single_decoder as feed
from test_drawer_single_decoder import (  # noqa: F401 -- fanout_dir is a fixture
    DISKS, PACKED, SHELLS, fanout_dir, record, room, state)
from test_timeouts import log_of

pytestmark = feed.pytestmark   # gcc for the real fanout; serial, like the room it borrows

#: gzip with a broken CRC trailer: every byte and the sha are right, only the
#: decoder's exit says it failed -- the payload of the #2248 finding.
BAD_TRAILER = PACKED[:-8] + bytes([PACKED[-8] ^ 1]) + PACKED[-7:]

#: The stream's fanout runs to its end, then its subshell lingers past the
#: stall ceiling before fanout.rc can be written.
LATE_RC = 'feed_fanout() { fanout "$@"; _late=$?; sleep 6; return "$_late"; }; '


@pytest.mark.parametrize("payload", [pytest.param(PACKED, id="good"),
                                     pytest.param(BAD_TRAILER, id="bad-trailer")])
@pytest.mark.parametrize("shape", ["shared", "each"])
@pytest.mark.parametrize("shell", SHELLS)
def test_a_stream_wait_that_ran_out_is_the_stream_s_even_with_a_matching_sha(
        tmp_path, fanout_dir, shell, shape, payload):
    sha = hashlib.sha256(payload).hexdigest()
    box, run, out = room(tmp_path, fanout_dir, shell, payload=payload, sha=sha,
                         tune=f"IMAGESRV_DRAWER_DECODE={shape}\n", before_restore=LATE_RC,
                         env="WAIT_STREAM_STALL_S=3")
    log = log_of(run)
    assert f"decode={shape}" in log, log
    # The scenario itself: the whole stream arrived, and the wait still ran out.
    assert record(run / "sha.out").split()[0] == sha, log
    assert "בלי התקדמות" in log, log
    assert "rc=1" in out.splitlines(), out + log
    for d in DISKS:
        assert state(run, d) == "failed", (d, log)
        assert "פג הזמן -- הזרם לא הגיע" in state(run, d, "error"), (d, state(run, d, "error"), log)
        assert not (run / "targets" / d / "ata_log.json").exists(), (d, "a healthy disk was blamed", log)
