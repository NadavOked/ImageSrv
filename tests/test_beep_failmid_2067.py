"""#2067 (mockup 08, Nadav 24/10) -- "מגירה נכשלה (איטית, שגיאת כתיבה): כן, כשל באמצע".

Until now a drawer was heard the moment it failed only when the kernel named it dead
(drawer_dead_mark: pulled / failure) or chunkcast failed it itself (chunkrecv.sh). Every other
failure of a drawer that was being written -- the server failing it between two chunk passes
(slow, did not keep up), a partition the server closed before the drawer had it all, a drawer
that came back and whose completion did not move (drawerback.sh), a fanout drop or a lost byte
in a udpcast stream -- went through ``fail_written_target`` silently, and was heard only in the
round's end tune.

Now ``fail_written_target`` plays ``failmid`` (the failure tune, mockup 08: "כשל באמצע" =
"כשל בסוף") when the drawer was ``writing``, and only then:

* a drawer the kernel named dead was heard already (its ``dead`` file);
* one refused before a write (``waiting``) or failed in its read-back (``verifying`` -- "בסוף"
  in the mockup) is the end tune's;
* ``failmid`` within 5 s of a failure tune is that tune: the drawers of a failed source, or of a
  partition the server closed, are heard once, not once each. A failure itself is still never
  skipped (``test_serialization_never_skips_a_failure``).

"Network back" is untouched: it still needs the server's answer (#3106, test_netback_evidence_3106).

Runs under busybox ash, as test_beeps_2067 does, on a fake PC Speaker node.

**Negative control** (Testrunner): ``agent/lib/core/roundbeep.sh`` and ``agent/lib/restore/failmark.sh``
at the base -> the drawer that fails while written is not heard, and failmid is "unknown tune";
the guards (waiting, verifying, failed, dead) pass on both.
"""

from __future__ import annotations

import time

import pytest

from test_agent import AGENT
from test_beeps_2067 import _function, expected, frequencies, log_lines, prelude, run_ash
from test_beeps_2067 import pytestmark  # noqa: F401 (busybox ash)

FAILMARK = AGENT / "lib" / "restore" / "failmark.sh"
#: the real fail_written_target; what it calls besides the tune is not this test's
STUBS = """
        target_set() { mkdir -p "$RUN_DIR/targets/$1"; echo "$2" > "$RUN_DIR/targets/$1/state"; }
        stage_at() { :; }; ata_capture() { :; }; wipe_written_gpt() { :; }
"""


def failing(tmp_path, body: str) -> str:
    return prelude(tmp_path) + STUBS + _function(FAILMARK, "fail_written_target", "wipe_written_gpt() {") + body


def drawer(name: str, state: str) -> str:
    return f'mkdir -p "$RUN_DIR/targets/{name}"; echo {state} > "$RUN_DIR/targets/{name}/state"; '


def test_a_drawer_that_fails_while_it_is_written_is_heard_now(tmp_path):
    run_ash(failing(tmp_path, drawer("sdb", "writing")
                    + 'fail_written_target sdb "מחיצה 3: השרת סגר את המחיצה לפני שהדיסק הושלם" stream; wait'))
    assert frequencies(tmp_path) == expected("failure")
    assert "beep failmid: played" in log_lines(tmp_path)
    assert (tmp_path / "targets/sdb/state").read_text().strip() == "failed"


@pytest.mark.parametrize("state", ["waiting", "verifying", "failed", "skipped"])
def test_a_drawer_that_was_not_being_written_is_the_end_tunes(tmp_path, state):
    """The guards: refused before a write, failed in its read-back ("בסוף"), or failed already."""
    run_ash(failing(tmp_path, drawer("sdb", state) + 'fail_written_target sdb "x"; wait'))
    assert frequencies(tmp_path) == []
    assert not [l for l in log_lines(tmp_path) if "failmid" in l], log_lines(tmp_path)


def test_a_drawer_the_kernel_named_dead_is_not_heard_twice(tmp_path):
    """drawer_dead_mark played its own tune (pulled / failure) and left `dead`."""
    run_ash(failing(tmp_path, drawer("sdb", "writing") + ': > "$RUN_DIR/targets/sdb/dead"; '
                    'fail_written_target sdb "המגירה נשלפה"; wait'))
    assert frequencies(tmp_path) == []


def test_every_drawer_of_one_failure_is_heard_once(tmp_path):
    """The source failed (source_failed_beep: failure), and with it the three drawers it was
    written to: one tune, not four."""
    run_ash(failing(tmp_path, drawer("sda", "writing") + drawer("sdb", "writing") + drawer("sdc", "writing")
                    + 'event_beep failure; wait; for d in sda sdb sdc; do fail_written_target "$d" "המקור נכשל"; done; wait'))
    assert frequencies(tmp_path) == expected("failure")
    log = log_lines(tmp_path)
    assert log.count("beep failure: played") == 1, log
    assert log.count("beep failmid: repeat within 5 s -- not heard") == 3, log


def test_two_drawers_that_fail_apart_are_heard_apart(tmp_path):
    """The guard of the window: a failure tune 6 s ago is not this one."""
    (tmp_path / "beep.failure").write_text(f"{int(time.time()) - 6}\n", encoding="utf-8")
    run_ash(failing(tmp_path, drawer("sdb", "writing") + 'fail_written_target sdb "x"; wait'))
    assert frequencies(tmp_path) == expected("failure")


def test_the_end_tune_is_never_skipped_after_a_drawer_failed_mid_work(tmp_path):
    """A failure (the round's end) right after a failmid is still played -- the window only
    folds a mid-work failure into a tune that was heard, never the other way."""
    run_ash(prelude(tmp_path) + "event_beep failmid; wait; event_beep failure; wait")
    assert frequencies(tmp_path) == expected("failure") * 2
    assert log_lines(tmp_path).count("beep failure: played") == 1
