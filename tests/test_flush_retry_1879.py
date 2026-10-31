"""#1879 -- a transient ``blockdev --flushbufs`` refusal is retried; a persistent
one still fails the drawer, by name.

Run through the real ``readback_drawers`` (agent/lib/restore/readback.sh), with the
stand-ins of test_readback_agent_1805.py. Here ``blockdev`` refuses
``--flushbufs`` on a node as many times as ``$BOX/flaky.<node>`` says, then
accepts -- the shape seen on the Testrunner (20/10): rc=1 on partition 1 of a
loop target right after its table changed, and a pass on the next run.

**Negative control** (agent/lib/restore/readback.sh of 9001fc91, before #1879): the
two-refusal test fails -- sdc is ``failed`` after ONE call. The persistent test
fails there on the count of tries only: a refusal that stays is a failure before
and after the fix (principle 5).
"""

from __future__ import annotations

import json

from test_readback_agent_1805 import NOT_RUN, box_for, manifest, posix, run
from test_readback_agent_1805 import pytestmark  # noqa: F401 -- bash and jq, as there

#: The same calls-log as the 1805 stand-in, plus a per-node budget of refusals.
FLAKY_BLOCKDEV = (
    'cat > "$BOX/stubs/blockdev" <<\'STUB\'\n'
    '#!/bin/sh\n'
    'echo "blockdev $1 $(basename "$2")" >> "$BOX/calls"\n'
    'case "$1" in\n'
    '  --flushbufs) f="$BOX/flaky.$(basename "$2")"\n'
    '               [ -s "$f" ] || exit 0\n'
    '               n=$(cat "$f"); [ "$n" -gt 0 ] || exit 0\n'
    '               echo $((n - 1)) > "$f"; echo "BLKFLSBUF: Device or resource busy" >&2; exit 1 ;;\n'
    '  --getsize64) wc -c < "$2" | tr -d " " ;;\n'
    '  *) exit 1 ;;\n'
    'esac\n'
    'STUB\n'
    'chmod 0755 "$BOX/stubs/blockdev"\n'   # the real `sleep 1` between tries: the waits count on it
)


def _drawers_with_refusals(tmp_path, refusals: int) -> dict:
    box = box_for(tmp_path)
    (box / "flaky.sdc3").write_text(f"{refusals}\n", newline="\n")
    (box / "m.json").write_text(json.dumps(manifest("sample")), encoding="utf-8", newline="\n")
    return run(box, FLAKY_BLOCKDEV + 'RB_SERVER=http://s RB_IMAGE=img_x readback_drawers '
                                     f'{posix(box / "m.json")!r} sdb sdc; echo "RC $?"')


def test_two_refusals_then_a_flush_is_a_flush(tmp_path):
    res = _drawers_with_refusals(tmp_path, 2)
    assert res["t"]["sdc"]["state"] != "failed", res["raw"]
    assert res["t"]["sdc"]["readback"] == "sampled", res["t"]["sdc"]
    assert res["calls"].count("blockdev --flushbufs sdc3") == 3, res["calls"]
    assert res["t"]["sdb"]["readback"] == "sampled"


def test_a_refusal_that_stays_fails_the_drawer_after_three_tries(tmp_path):
    res = _drawers_with_refusals(tmp_path, 99)
    assert res["t"]["sdc"]["state"] == "failed", res["raw"]
    assert res["t"]["sdc"]["error"].startswith(
        f"{NOT_RUN} — ריקון המטמון של מחיצה 3 נכשל (blockdev --flushbufs rc=1)"), res["t"]["sdc"]
    assert res["calls"].count("blockdev --flushbufs sdc3") == 3, res["calls"]
    assert not any(c.startswith("readback sdc") for c in res["calls"])
    assert res["t"]["sdb"]["readback"] == "sampled"      # the other drawer is not held by it
