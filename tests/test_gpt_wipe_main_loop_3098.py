"""#3098: a drawer whose GPT wipe never comes back does not hold the main loop of the chunk engine.

The ceiling itself (`wipe_written_gpt` under `capped_run`, failmark.sh) is tested at the function in
test_gpt_wipe_cap_3098.py, and the *watch* of a pass (background, chunkwatch.sh) in test_chunk_drawer_pull_2533.py.
What the 19/11 check of the issue found missing is the third thing, the one the issue's "done" asks for: **the
main loop** -- the code that calls `fail_written_target` itself, in line, with the other drawers waiting behind
it -- carries on to them. (The old engine, udpcast, is gone since #3173: the chunk engine is the only loop.)

Here the real `chunk_partition_drawers` runs against the fake receiver, server and disks of test_chunkrecv_2533.py,
and the wipe of one drawer is held by the disk itself (`ZAP_HANGS=sda`: a minute here, no end on a dying disk;
the ceiling is `WAIT_HELPER_S=1`). The two in-line callers that decide a drawer's verdict:

- the server's own word (`fail` in its answer, chunkrecv.sh) -- sda is failed, sdb is written and done;
- "the server closed the partition before the drawer was complete" (chunkrecv.sh, the end of the loop) -- both
  are failed by name: the one behind the held drawer must still be reached, and still be wiped.

What is asserted is what the operator and the server see: who failed, with what words, who was done, and that the
loop came back in seconds -- never an exit code alone.

Negative control: failmark.sh of `fe43a255f~1` (the wipe with no ceiling) -- both tests wait the minute out and fail
on the time; the guard (nothing hangs) passes in both states.
"""

from __future__ import annotations

import time

import pytest

from native import requires_native
from test_agent import BASH
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

pytestmark = [
    requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן"),
    # the ceiling is real seconds against a loop that ticks in real seconds (#2118): one request at a time
    pytest.mark.serial,
]

HANG_S = 60            # what the harness's held wipe lasts (test_chunkrecv_2533.py, sgdisk)
BUDGET_S = 25          # the ceiling is 1 s; the rest is the shell's start-up and the loop's own waits
REAL_PASS = "REAL_SLEEP=1 TEST_START_CEILING=30 WAIT_HELPER_S=1"
ZAP = "sgdisk --zap-all /dev"
HELD = "sda: the GPT wipe did not end within 1s after a failed restore -- not wiped"
WIPED = "sdb: wiped primary and backup GPT after a failed restore"


def _timed(tmp_path, answers, env):
    started = time.monotonic()
    got = _run(tmp_path, answers, env=env)
    return got, time.monotonic() - started


def test_a_wipe_held_by_the_drawer_the_server_failed_does_not_hold_the_main_loop(tmp_path):
    """הלולאה הראשית קוראת ל-`fail_written_target` בתוך הזרימה שלה, על מגירה ש**השרת** הכשיל (`fail` בתשובה).
    המחיקה של sda תקועה על הדיסק עצמו. sda נכשלת בשמה, ה-GPT שלה "לא נמחק" ולא "נמחק", ו-sdb -- שמחכה מאחוריה --
    נכתבת ונחתמת, תוך שניות."""
    server_fails_sda = {**DONE, "fail": {"SER-sda": "השרת הכשיל את הדיסק"}}
    got, took = _timed(tmp_path, [RECEIVE1, server_fails_sda], f"ZAP_HANGS=sda {REAL_PASS}")
    trace, log = got["trace"], got["log"]
    assert took < BUDGET_S, f"the main loop waited {took:.0f}s for a wipe the dying disk holds ({HANG_S}s here, no end there)"
    assert f"{ZAP}/sda" in trace, trace                                       # the wipe did begin, and was held
    fail = [t for t in trace if t.startswith("FAIL sda")]
    assert len(fail) == 1 and "השרת הכשיל את הדיסק" in fail[0], trace         # failed by its own reason, once
    assert HELD in log and "sda: wiped primary" not in log, log               # "did not end" is not "wiped" (principle 5)
    assert "DONE sdb" in trace and "DONE sda" not in trace, trace             # the drawer behind it carried on to its end
    assert not any(t.startswith("FAIL sdb") for t in trace), trace
    assert trace[-1] == "rc=0", got


def test_every_drawer_closed_before_it_was_whole_is_reached_though_the_first_wipe_is_held(tmp_path):
    """הסוף של הלולאה: השרת סגר את המחיצה, ושתי המגירות חסרות (2 מתוך 5 חלקים). הלולאה עוברת עליהן אחת אחת וקוראת
    לכל אחת `fail_written_target`. המחיקה של הראשונה (sda) תקועה; השנייה (sdb) חייבת עדיין להיכשל בשמה ולהימחק --
    ולא להישאר עם GPT חצי-כתוב ובלי שם בגלל שמישהי לפניה נתקעה."""
    got, took = _timed(tmp_path, [RECEIVE1, DONE], f"ZAP_HANGS=sda RECV_COUNT=2 RECV_RANGES=0-0,2-2 {REAL_PASS}")
    trace, log = got["trace"], got["log"]
    assert took < BUDGET_S, f"the loop waited {took:.0f}s for a wipe the dying disk holds ({HANG_S}s here, no end there)"
    for disk in ("sda", "sdb"):
        fail = [t for t in trace if t.startswith(f"FAIL {disk}")]
        assert len(fail) == 1 and "השרת סגר את המחיצה לפני שהדיסק הושלם (2 מתוך 5 חלקים)" in fail[0], (disk, trace)
        assert f"{ZAP}/{disk}" in trace, (disk, trace)
    sdb_fail = next(t for t in trace if t.startswith("FAIL sdb"))
    assert trace.index(f"{ZAP}/sda") < trace.index(sdb_fail), trace           # sda first: the one that is held
    assert HELD in log and WIPED in log, log                                  # the held one said so, the other was wiped
    assert not any(t.startswith("DONE") for t in trace), trace


def test_nothing_held_nothing_changes(tmp_path):
    """השומר, בשני המצבים: בלי מחיקה תקועה שתי המגירות נכתבות, אף אחת אינה נכשלת, ואף שורה אינה אומרת "לא נמחק"."""
    got, _took = _timed(tmp_path, [RECEIVE1, DONE], REAL_PASS)
    trace, log = got["trace"], got["log"]
    assert "DONE sda" in trace and "DONE sdb" in trace and trace[-1] == "rc=0", got
    assert not any(t.startswith("FAIL") or t.startswith("sgdisk") for t in trace), trace
    assert "did not end" not in log, log
