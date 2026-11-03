"""#2229 (metal 31/10, cloner 2, v0.76.7): a local clone, then "מהשרת" -- the
standby screen drew the local clone's target as a round that ended on this
machine ("ממתין לשידור מהשרת · הסבב הסתיים במחשב הזה"). The source was
``$RUN_DIR/targets/sda``, left over from the local copy: ``cloner_gui_state``
turns every ``targets/*/`` into a ``drawer=`` record of the round.

Driven through the real ``local_clone_offer`` and the real ``lc_run`` (it
initialises its targets and then refuses a source that was never inventoried
-- the residue is the same ``targets/<dev>``), and read back through the real
``cloner_gui_state``: the screen's own input, not a file listing.
"""

from __future__ import annotations

from test_cloner_gui import posix
from test_local_clone_agent import BOOT, REPO, run, pytestmark  # noqa: F401

AGENT = REPO / "agent"
#: what gui_libs loads for cloner_gui_state (test_cloner_gui.PRELUDE) on top of LIBS
SCREEN = (f'. {posix(AGENT)}/lib/hw/disklink.sh; . {posix(AGENT)}/lib/gui/askgui.sh; '
          f'. {posix(AGENT)}/lib/hw/slotnames.sh; . {posix(AGENT)}/lib/gui/clonergui.sh; '
          f'. {posix(AGENT)}/lib/restore/writemark.sh; ')
#: not the subject here: no beep, no report to a server that is not there
QUIET = 'round_beep_result() { :; }; lc_report() { :; }; '
#: the standby screen's state, as the kiosk builds it after the choice
SCREEN_STATE = ('cloner_gui_state; echo "--- state"; cat "$GUI_DIR/state"; '
                'echo "--- targets"; ls "$RUN_DIR/targets"; ')


def test_server_after_a_local_clone_does_not_show_its_targets_as_a_round(tmp_path):
    out = run(tmp_path, SCREEN + BOOT + QUIET + 'local_clone_arm; '
              # the person: local clone, a copy to sdb and sdc, back, then "from the server"
              'lc_session() { lc_run sda "sdb sdc" 0; LC_END=back; '
              '  echo "during=$(ls "$RUN_DIR/targets" | tr "\\n" " ")"; '
              '  ( sleep 1; echo server > "$LC/boot.decision" ) & }; '
              '( sleep 1; echo local > "$LC/boot.decision" ) & '
              'local_clone_offer; ' + SCREEN_STATE + 'kill $_gui_pid',
              LC_BOOT_S="30")
    assert out.returncode == 0, out.stderr
    assert "'local clone' chosen" in out.stdout and "'from the server' chosen" in out.stdout, out.stdout
    # the copy really left its targets behind while the local result was on the screen
    assert "during=sdb sdc" in out.stdout, out.stdout
    state = out.stdout.split("--- state", 1)[1].split("--- targets", 1)[0].splitlines()
    assert not [line for line in state if line.startswith("drawer=")], state
    targets = out.stdout.split("--- targets", 1)[1].split()
    assert "sdb" not in targets and "sdc" not in targets, targets


def test_server_without_a_local_copy_keeps_the_targets_it_found(tmp_path):
    """The guard against an over-broad fix: only what the local copy wrote goes.
    A target from before the choice (a round earlier in this boot) is not the
    local flow's to clear."""
    out = run(tmp_path, SCREEN + BOOT + QUIET + 'target_init sda 100; echo done > "$RUN_DIR/targets/sda/state"; '
              'local_clone_arm; lc_session() { echo SESSION; LC_END=; }; '
              '( sleep 1; echo server > "$LC/boot.decision" ) & '
              'local_clone_offer; ' + SCREEN_STATE + 'kill $_gui_pid',
              LC_BOOT_S="30")
    assert out.returncode == 0, out.stderr
    assert "SESSION" not in out.stdout and "'from the server' chosen" in out.stdout, out.stdout
    state = out.stdout.split("--- state", 1)[1].split("--- targets", 1)[0].splitlines()
    assert [line for line in state if line.startswith("drawer=") and "|sda|done|" in line], state
