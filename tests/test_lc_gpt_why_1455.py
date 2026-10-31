"""#1455: a failed apply_gpt step without PLAN_ERROR still names its cause on the card."""

from __future__ import annotations

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))


def _why(tmp_path, plan_error: str) -> str:
    log = tmp_path / "log"
    log.write_text("one\nsgdisk: Problem opening /dev/sdb for writing!\nblockdev: BLKRRPART: Device or resource busy\n",
                   encoding="utf-8", newline="\n")
    pre = f"PLAN_ERROR={plan_error!r}; " if plan_error else "unset PLAN_ERROR; "
    return sh(f"LOG_FILE={posix(log)!r}; {pre}. {posix(AGENT)}/lib/localclone/lccopy.sh; _lc_gpt_why").strip()


def test_an_empty_plan_error_carries_the_logged_cause(tmp_path):
    out = _why(tmp_path, "")
    assert out.startswith("טבלת המחיצות לא נכתבה: "), out
    assert "BLKRRPART: Device or resource busy" in out and "Problem opening" in out, out


def test_a_set_plan_error_wins(tmp_path):
    assert _why(tmp_path, "partition 2 has no file") == "partition 2 has no file"
