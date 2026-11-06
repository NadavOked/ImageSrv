"""#2067 -- "המקור נכשל" is heard at the moment the source could not be read.

Nadav 24/10 (mockup 08): the same tune as a failure at the end, repeated -- and the 06/11 triage
found it sounded only in ``lc_end``, after the copy's cleanup (the RAM views released, the report),
not when the read failed. Three places in the local clone decide "the source failed": the real
``lc_stream`` (``lccopy.sh``: the reader's exit code, no progress for LC_STALL_S, no hash) and
``lc_stream_any`` (``lcraw.sh``: the operator stopped at bad sectors). They now call
``source_failed_beep``, which plays the failure tune at once and leaves a mark so ``lc_end`` does not
play it a second time (one failure, one tune). A target that died, or an operator's stop of the whole
copy, is not "the source failed": no tune from these sites.
"""

from __future__ import annotations

import pytest

from test_beeps_2067 import BUSYBOX, expected, frequencies, prelude, run_ash
from test_lc_raw_2017 import PCL_FAILS, STUB, _stream
from test_local_clone_agent import run, pytestmark  # noqa: F401  (bash)
from native import requires_native
from test_local_clone_agent import STREAM_STUBS

#: the tune is not the subject of the call-site tests: a call is one line in $RUN_DIR/beeps
CALLED = 'source_failed_beep() { echo source >> "$RUN_DIR/beeps"; }; '


def _beeps(tmp_path):
    f = tmp_path / "run" / "beeps"
    return f.read_text().split() if f.exists() else []


# --- the call sites ------------------------------------------------------------------------


def test_a_source_that_failed_to_read_is_heard_when_lc_stream_decides_it(tmp_path):
    """lccopy.sh: the reader exited non-zero and no target took the stream -- fanout's own word."""
    fan = STREAM_STUBS["fanout"].replace('    echo "$f ok"\n', '    echo "$f failed write error"\n')
    out = _stream(tmp_path, "ntfs", {"partclone.ntfs": PCL_FAILS, "fanout": fan}, pre=CALLED)
    assert "rc=1 src=" in out and "קריאת המקור נכשלה" in out, out
    assert _beeps(tmp_path) == ["source"], out


def test_a_partclone_refusal_is_not_a_failed_source_yet(tmp_path):
    """rc=3: lcraw.sh streams it again, raw -- nothing failed, so nothing sounds (the guard)."""
    out = _stream(tmp_path, "ntfs", {"partclone.ntfs": PCL_FAILS}, pre=CALLED)
    assert "rc=3 src=1" in out, out
    assert _beeps(tmp_path) == [], out


def _any(tmp_path, rcs: str, answer: str):
    out = run(tmp_path, f'RCS="{rcs}"; FS=ntfs; SIZE=167772160; ' + STUB + CALLED
              + f'gui_ask_choice() {{ echo "$*" > "$LC/run/asked"; echo {answer}; }}; '
              + 'lc_stream_any 4 ntfs sdc sda sdb; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_the_operator_stopping_at_bad_sectors_is_heard_when_he_stops(tmp_path):
    out = _any(tmp_path, "3 4", "stop")
    assert "rc=1" in out, out
    assert _beeps(tmp_path) == ["source"], out


@pytest.mark.parametrize("rcs,answer", [("3 0", "stop"), ("3 4", "continue")])
def test_a_copy_that_goes_on_sounds_nothing_from_the_read(tmp_path, rcs, answer):
    """A refused-then-raw read that works, and bad sectors the operator copied through: no tune here."""
    _any(tmp_path, rcs, answer)
    assert _beeps(tmp_path) == []


# --- lc_end: one failure, one tune -----------------------------------------------------------

LC_END = ('LC="$RUN_DIR/lc"; mkdir -p "$LC/run"; _lr_r="$LC/run"; _lr_all="sda sdb"; _lr_beat=; '
          'lc_report() { :; }; wait_pid() { :; }; round_beep_result() { echo end >> "$RUN_DIR/beeps"; }; ')


@pytest.mark.parametrize("mark,want", [("", ["end"]), ("echo 1 > $_lr_r/beeped; ", []), ("echo 1 > $_lr_r/stopped; ", [])])
def test_lc_end_plays_its_own_unless_the_source_was_heard_or_the_copy_was_stopped(tmp_path, mark, want):
    out = run(tmp_path, LC_END + mark + "lc_end")
    assert out.returncode == 0, out.stderr
    assert _beeps(tmp_path) == want


# --- the function itself (busybox ash, the agent's shell) --------------------------------------


@requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")
def test_source_failed_beep_plays_the_failure_tune_once_and_leaves_the_mark(tmp_path):
    run_ash(prelude(tmp_path) + 'LC="$RUN_DIR/lc"; mkdir -p "$LC/run"; source_failed_beep; wait')
    assert frequencies(tmp_path) == expected("failure")
    assert (tmp_path / "lc/run/beeped").exists()


@requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")
def test_a_mark_that_cannot_be_written_still_sounds(tmp_path):
    """A mark we cannot write may sound twice (the end's own); it must never be silence."""
    run_ash(prelude(tmp_path) + 'LC="$RUN_DIR/nodir"; source_failed_beep; wait')   # no directory: the mark fails
    assert frequencies(tmp_path) == expected("failure")
    assert any("source-failure mark not saved" in line
               for line in (tmp_path / "agent.log").read_text().splitlines())
