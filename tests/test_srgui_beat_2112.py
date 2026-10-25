"""#2112 -- the restore screen's keeper (srgui.sh sr_gui_open) must not kill a
GUI that has just started.

The keeper's loop checks the beat (gui_beat_fresh) at once, while the start's
grace -- ``: > beat`` -- was made inside the ``{ ...; } &`` block that starts the
GUI. A block scheduled after the keeper's first look left it a missing beat; if
the block then wrote restore.gpid before the keeper's ``cat``, the new GUI got
``kill -9`` (seen once on the Testrunner, #2099 f1, 24/10: "on the GUI" and
"heartbeat older than 2s ... killing it" in the same second, two starts).

Deterministic, not by load: the shims below hold the block's first command
until the keeper has looked once, and hold the keeper's log line until the
GUI's pid is written -- the order of the bug, forced. The default
GUI_BEAT_MAX_S (90) is kept: a missing beat is old at any bound.

**Negative control**: see the PR (srgui.sh of the base, the test fails with two
GUI starts and the kill in the log).
"""

from __future__ import annotations

import pytest

import test_gui_keeper_1726 as keeper
from test_gui_heartbeat_1946 import BEATING_SR_GUI

#: bash (the test's shell): the real gui_beat_fresh/log, wrapped.
SHIMS = (
    'eval "$(declare -f gui_beat_fresh | sed \'1s/^gui_beat_fresh/_real_beat_fresh/\')"; '
    'eval "$(declare -f log | sed \'1s/^log/_real_log/\')"; '
    # the keeper's look is recorded
    'gui_beat_fresh() { : > "$RUN_DIR/looked"; _real_beat_fresh; }; '
    # the GUI's start (rm restore.rc is its first command) waits for that look
    'rm() { case "$*" in *restore.rc*) n=0; until [ -e "$RUN_DIR/looked" ] || [ "$n" -ge 30 ]; '
    'do sleep 0.1; n=$((n + 1)); done ;; esac; command rm "$@"; }; '
    # the kill's log line waits until the new GUI's pid is there to be read
    'log() { case "$*" in *"heartbeat older"*) n=0; until [ -s "$RUN_DIR/gui/restore.gpid" ] || [ "$n" -ge 50 ]; '
    'do sleep 0.1; n=$((n + 1)); done ;; esac; _real_log "$@"; }; '
)


@pytest.fixture
def beating(monkeypatch):
    monkeypatch.setattr(keeper, "SR_GUI", BEATING_SR_GUI)


@keeper.POSIX
def test_a_restore_gui_whose_start_runs_late_is_not_killed(tmp_path, beating):
    proc = keeper._start(tmp_path, SHIMS + 'sr_gui_open fresh; echo "OPEN rc=$?"; sleep 30')
    try:
        stations = tmp_path / "run" / "stations"
        first = keeper._await(lambda: keeper._lines(stations)[:1], 20)
        assert first, "the GUI never started\n" + keeper._report(tmp_path)
        keeper._await(lambda: len(keeper._lines(stations)) > 1, 6)   # the window of a kill and a respawn
        assert (tmp_path / "run" / "looked").exists(), "the keeper never checked the beat"
        log = (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")
        assert "heartbeat older than" not in log, (
            "the keeper found no beat for a GUI that was starting (#2112)\n" + keeper._report(tmp_path))
        assert len(keeper._lines(stations)) == 1, (
            "a GUI that had just started was killed and started again (#2112)\n" + keeper._report(tmp_path))
        assert keeper._alive(int(first[0])), keeper._report(tmp_path)
        assert proc.poll() is None, "the agent's flow ended early\n" + keeper._report(tmp_path)
    finally:
        keeper._stop(proc)
