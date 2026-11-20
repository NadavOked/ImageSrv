"""#3072: the chunk engine's receiver gets the receive buffer it asks for.

`chunkcast recv` asks SO_RCVBUF of 16MiB and the kernel grants min(ask, rmem_max) * 2 -- 425,984 bytes on a
stock kernel (every chunk wave on cloner 2, 13/11: "receive buffer 425984 bytes (asked 16777216)"). The one
place that raises rmem_max is rcvbuf_check (rcvbuf.sh, #2994/#3004), called before every udp-receiver from
stream_source -- and the chunk path (drawers.sh -> chunk_partition_drawers) never goes through it.

The fix (agent/lib/restore/chunkrecv.sh): chunk_partition_drawers calls rcvbuf_check once per partition,
before its first `chunkcast recv`. Nothing of rcvbuf.sh is copied; an rmem_max set by hand higher stays.
What the socket got is chunkcast's own log line (it reads SO_RCVBUF back); it is not put in the report's
udp_drops -- the chunk path writes no udp_drops (said in the PR).

The branch carries #3004 (cherry-picked from main, 56d6766e): lab/vm-1113g has no rcvbuf.sh.

Negative control: chunkrecv.sh of that cherry-pick (e0c9f27a, without the call) -- rmem_max stays 212992 and
no rcvbuf line is written. The guard (a higher rmem_max set by hand is kept) passes in both states.
"""

from __future__ import annotations

import pytest

import test_chunkrecv_2533 as R

pytestmark = R.pytestmark


@pytest.fixture(autouse=True)
def _with_rcvbuf(monkeypatch):
    """The receiver's room with rcvbuf.sh in it, as imagesrv-agent sources it (beside waits.sh)."""
    harness = R.HARNESS.replace(". agent/lib/core/waits.sh;", ". agent/lib/core/waits.sh; . agent/lib/core/rcvbuf.sh;", 1)
    assert harness != R.HARNESS
    monkeypatch.setattr(R, "HARNESS", harness)


def _rmem(tmp_path, value):
    f = tmp_path / "sysroot" / "proc" / "sys" / "net" / "core" / "rmem_max"
    f.parent.mkdir(parents=True)
    f.write_text(f"{value}\n", newline="\n")
    return f


def test_the_chunk_receiver_runs_after_rmem_max_is_raised_to_its_ask(tmp_path):
    f = _rmem(tmp_path, 212992)
    got = R._run(tmp_path, [R.RECEIVE1, R.DONE])
    assert got["trace"][-1] == "rc=0", got
    assert f.read_text().strip() == "16777216", got["log"]
    lines = got["log"].splitlines()
    said = [i for i, l in enumerate(lines) if "udp rcvbuf: asked 16777216, the socket gets 33554432 bytes (rmem_max 16777216)" in l]
    recv = [i for i, l in enumerate(lines) if "chunkcast recv pass 1 rc=" in l]
    assert said and recv and said[0] < recv[0], got["log"]
    assert any(t.startswith("chunkcast recv ") for t in got["trace"]), got["trace"]


def test_an_rmem_max_set_higher_by_hand_is_kept(tmp_path):
    """The guard, in both states: rcvbuf.sh never lowers what the operator set."""
    f = _rmem(tmp_path, 67108864)
    got = R._run(tmp_path, [R.RECEIVE1, R.DONE])
    assert got["trace"][-1] == "rc=0", got
    assert f.read_text().strip() == "67108864"
