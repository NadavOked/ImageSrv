"""#1575 -- a build machine's failure hold yields to the next task.

Metal 17/10 (HP build machine): the direct send failed at 08:52 and the agent
went into ``ui_error_hold`` -> ``hold_watch``. At 08:56 the GUI's "קליטה
נוספת" ordered a capture (tsk_d58c, pending on the server); the hold beat's
hello carried it in its answer and threw the answer away, every 10 seconds,
until the machine was rebooted.

The real ``hold_watch``/``hold_beat``/``attended_hello`` run here; only the
wire (``http_post_json``) and the hello body (``build_hello``) are stubs. The
stub answers beat by beat from files, and ends the hold's subshell with exit
7 on the seventh beat -- that is "it would have held for ever", by count, not by timeout.

Negative control: on main ``hold_watch`` never returns, and the first test
ends in ``sub=7`` (still holding after six beats) instead of ``returned``.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH), "jq")


def _hold(tmp_path: Path, answers: dict[str, dict], yield_task: str | None) -> tuple[str, str]:
    run, ans = tmp_path / "run", tmp_path / "ans"
    run.mkdir(); ans.mkdir()
    for name, body in answers.items():
        (ans / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    libs = " ".join(f". {posix(flat_lib())}/{n}.sh;" for n in ("common", "jsonq", "hold", "attended"))
    setyield = f"HOLD_YIELD_TASK={yield_task}; " if yield_task else ""
    out = sh(
        f"export RUN_DIR={posix(run)!r} ANS={posix(ans)!r} HOLD_BEAT_S=0; {libs} "
        "build_hello() { echo '{\"schema\":1}'; }; "
        'http_post_json() { n=$(cat "$ANS/n" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$ANS/n"; '
        '  [ "$n" -gt 6 ] && exit 7; '
        '  f="$ANS/$n.json"; [ -f "$f" ] || f="$ANS/default.json"; cat "$f"; }; '
        f'( HOLD_PROMPT="FAILED: direct deployment did not complete"; {setyield}'
        "hold_watch hold_beat; "
        'echo "returned beats=$(cat "$ANS/n") prompt=[$HOLD_PROMPT] yield=[${HOLD_YIELD_TASK:-}]" ); '
        'echo "sub=$?"'
    )
    log = (run / "agent.log").read_text("utf-8", "replace") if (run / "agent.log").exists() else ""
    return out, log


def _task(tid: str) -> dict:
    return {"schema": 1, "known": True, "role": "build",
            "task": {"id": tid, "type": "capture", "disk": "sda", "token": "t" * 48}}


def test_a_new_task_ends_the_hold_of_a_failed_build_task(tmp_path):
    """Two beats still carry nothing new (the failed task, then none); the
    third carries the GUI's capture -- the hold ends there, and says so."""
    out, log = _hold(tmp_path, {"1": _task("tsk_49ca"), "2": {"schema": 1, "task": None},
                                "3": _task("tsk_d58c"), "default": _task("tsk_d58c")},
                     "tsk_49ca")
    assert "returned beats=3" in out, out + log
    assert "prompt=[]" in out and "yield=[]" in out, out   # nothing leaks into the next task
    assert "hold ended: the server gave a new task (tsk_d58c) after tsk_49ca failed" in log, log


def test_the_failed_task_itself_or_no_task_keeps_the_hold(tmp_path):
    """The check is not "any answer": the same task id, or no task at all,
    is not a reason to leave -- six beats and still holding."""
    out, log = _hold(tmp_path, {"1": _task("tsk_49ca"), "default": {"schema": 1, "task": None}},
                     "tsk_49ca")
    assert "sub=7" in out and "returned" not in out, out + log


def test_a_hold_that_did_not_ask_to_yield_never_does(tmp_path):
    """Restores, drawers, unheard reports: no HOLD_YIELD_TASK, and a task in
    the answer changes nothing -- a broken disk is still not rebooted into."""
    out, log = _hold(tmp_path, {"default": _task("tsk_d58c")}, None)
    assert "sub=7" in out and "returned" not in out, out + log
