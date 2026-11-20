"""#2533 (#3112): "בטל דיסק זה" על מגירה שמשלימה ברקע -- ההשלמה נעצרת **לפני** שה-GPT נמחק.

מגירה שנשלפה וחזרה משלימה ברקע (`drawerback.sh`: subshell, ובתוכו `chunkcast apply` אחד). הבקשה "בטל דיסק
זה" (#1386) מתבצעת בין מעברים (`cancel_pending`, chunkrecv.sh) -- ו-`cancel_drawer` (verdict.sh) מוחק את ה-GPT
ומסמן `cancelled`, כשהוא סומך על כך שהכותב כבר נעצר (``the GPT goes only after the writer has stopped``). אצל
מגירה שמשלימה ברקע זה לא נכון: ה-subshell וה-apply שלו אינם ה-reader של fifo (אין `reader.pid`), ולכן הם המשיכו
לכתוב לדיסק שאינו נראה עוד כמערכת, עד סוף ההורדה.

כאן: אותה רתמה של test_chunkrecv_2533.py ו-test_chunk_drawer_returns_2533.py (מגירה שחזרה כ-sdc, הורדה של כמה
שניות), עם ה-`cancel_drawer` **האמיתי** ברגע שההשלמה כבר כותבת. מה שנבדק: ה-apply נהרג (אינו מסיים, גם
אחרי שהזמן שלו עבר), ה-GPT נמחק פעם אחת, המגירה `cancelled` ולא `failed`, והמגירה האחרת אינה מושפעת.

**בקרה שלילית:** החזרת `verdict.sh`, `drawerback.sh` ו-`chunkfetch.sh` לבסיס: ה-apply מסיים אחרי המחיקה.
"""

from __future__ import annotations

import time

import pytest

import test_chunkrecv_2533 as base
from native import requires_native
from test_agent import BASH
from test_chunk_drawer_returns_2533 import PULLED_BACK, RECEIVE2, ZAP
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

STUB = 'cancel_pending() { trace "cancel_pending"; }'
#: the real cancel_drawer, once -- at the first pass boundary after the completion has begun to write (its apply's stdin exists)
REAL = r'''cancel_pending() {
    trace "cancel_pending"
    [ -f "$RUN_DIR/targets/sdc/catchup" ] && [ ! -e "$RUN_DIR/cancelled.once" ] || return 0
    _w=0; until [ -f "$RUN_DIR/apply.stdin" ] || [ "$_w" -ge 50 ]; do command sleep 0.1; _w=$((_w + 1)); done
    : > "$RUN_DIR/cancelled.once"; echo "על ידי nadav" > "$RUN_DIR/targets/sdc/cancel"
    cancel_drawer sdc; trace "CANCELLED rc=$?"
}'''


def _cancelled_run(tmp_path, monkeypatch, apply_slow: int = 4) -> tuple[dict, list[str]]:
    assert STUB in base.HARNESS
    monkeypatch.setattr(base, "HARNESS", base.HARNESS.replace(STUB, REAL))
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"APPLY_SLOW={apply_slow} {PULLED_BACK}")
    time.sleep(apply_slow + 3)                  # past the end the apply would have had, were it let to go on
    return got, (tmp_path / "trace").read_text(encoding="utf-8").splitlines()


def test_cancel_stops_a_completion_in_the_background_before_the_gpt_goes(tmp_path, monkeypatch):
    got, trace = _cancelled_run(tmp_path, monkeypatch)
    assert got["trace"][-1] == "rc=0", got
    assert "CANCELLED rc=0" in trace and "SET sdc cancelled" in trace, trace
    assert trace.count(f"{ZAP}sdc") == 1, trace                              # the GPT was wiped, once
    assert "APPLY-END" not in trace, "the completion went on writing into the disk after its GPT was wiped"
    assert "DONE sdc" not in trace and not any(t.startswith("FAIL sdc") for t in trace), trace   # cancelled, not done, not failed
    assert "its completion in the background was stopped before its GPT was wiped" in got["log"], got["log"]
    assert not (tmp_path / "targets" / "sdc" / "catchup").exists()
    assert not (tmp_path / "chunk" / "back.sdc" / "apply.pid").exists()


def test_the_other_drawer_is_not_touched_by_the_cancel(tmp_path, monkeypatch):
    _, trace = _cancelled_run(tmp_path, monkeypatch)
    assert "DONE sda" in trace and f"{ZAP}sda" not in trace, trace
    assert not any(t.startswith("FAIL sda") for t in trace), trace


def test_a_completion_nobody_cancelled_still_ends_whole(tmp_path):
    """The guard: the harness without the cancel -- the same completion ends and the drawer is done."""
    got = _run(tmp_path, [RECEIVE1, RECEIVE2, DONE], env=f"APPLY_SLOW=2 {PULLED_BACK}")
    assert got["trace"][-1] == "rc=0" and "APPLY-END" in got["trace"] and "DONE sdc" in got["trace"], got
    assert f"{ZAP}sdc" not in got["trace"]
