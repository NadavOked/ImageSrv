"""#1877 -- the incomplete-write mark stays on through the post-deploy writes.

#1798 (PR #1876) named every partition ``imagectl-incomplete`` with the table
and took the names off in ``finish_grow``. But the machine's name
(``name_this_machine`` -> hostname.sh) and the drivers (``stage_drivers`` /
``stage_drawer_drivers`` -> postdeploy.sh) write to the same disk AFTER
``finish_grow``: a ``reboot -f`` or a power cut in them left a disk with no
mark, no name or half its drivers -- "תקין" on every screen.

The fix: the three paths that have post-deploy writes (the class station, the
cloner's drawers, the GUI restore) run the restore under ``WRITEMARK_HOLD=1``;
``finish_grow`` then leaves the mark on, and ``writemark_release_done`` takes it
off after the last write. A mark that does not come off fails the target, in the
open (principle 4).

Here: the sgdisk stub of test_writemark_1798.py (a GPT kept in files, so the
disk outlives the shell -- a fresh process is the reboot), and the paths
themselves cut out of imagectl-agent with their neighbours stubbed. No jq: the
path tests stub ``json_get``, so they run on Windows too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_final_report import cut_function
from test_restore_evidence import log_of, rc_of
from test_writemark_1798 import MARK, _box, _fail, _names, _state

pytestmark = requires_native(("bash", BASH))


def _fresh_state(tmp_path: Path, box: Path) -> str:
    """A new process with a new RUN_DIR reads the disk: the reboot."""
    fresh = tmp_path / "after-reboot"
    fresh.mkdir(exist_ok=True)
    out = sh(f'export PATH="$(cd {posix(box)!r}/stubs && pwd):$PATH" RUN_DIR={posix(fresh)!r} '
             f'DEVROOT={posix(box)}/dev LOG_FILE={posix(fresh)}/log; log() {{ :; }}; '
             f'. {posix(AGENT)}/lib/writemark.sh; writemark_state sda')
    return out.strip().splitlines()[-1]


# --- finish_grow under the hold: done, and still marked ------------------------------


def test_under_the_hold_the_grow_leaves_the_mark_on_and_a_cut_after_it_shows(tmp_path):
    """The heart of #1877: the restore and the grow are done, the name and the
    drivers are not -- a cut now (a new process) must still read incomplete."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; WRITEMARK_HOLD=1 finish_grow sda; '
             'echo "rc=$?"; writemark_state sda')
    lines = out.strip().splitlines()
    assert lines[-2:] == ["rc=0", "incomplete"], (out, log_of(run))
    assert _state(run)[0] == "done"
    assert _names(box) == {1: MARK, 2: MARK, 3: MARK}
    assert _fresh_state(tmp_path, box) == "incomplete"
    assert "stays on until the post-deploy writes (#1877)" in log_of(run)


def test_the_release_after_the_post_deploy_puts_the_names_back(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; WRITEMARK_HOLD=1 finish_grow sda || exit 8; '
             'writemark_release_done sda; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-2:] == ["rc=0", "clean"], (out, log_of(run))
    assert _state(run)[0] == "done"
    assert _names(box) == {1: "", 2: "", 3: ""}
    assert _fresh_state(tmp_path, box) == "clean"


def test_without_the_hold_the_grow_still_clears_it(tmp_path):
    """The local clone has no post-deploy: its finish_grow is the last write,
    exactly as in #1798."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; finish_grow sda; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-2:] == ["rc=0", "clean"], (out, log_of(run))


def test_a_release_that_does_not_come_off_fails_the_target(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; WRITEMARK_HOLD=1 finish_grow sda || exit 8; '
             f'echo "-c *" > {posix(box)}/sgdisk.fail; '
             'writemark_release_done sda; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-2:] == ["rc=1", "incomplete"], (out, log_of(run))
    state, error = _state(run)
    assert state == "failed" and "לא הוסר מהדיסק" in error, (state, error)


def test_a_target_that_did_not_reach_done_keeps_its_mark(tmp_path):
    """Only done targets are released: a failed one is never made to look whole."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; target_set sda failed "cut"; '
             'writemark_release_done sda; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-2:] == ["rc=0", "incomplete"], (out, log_of(run))


# --- the paths: the mark comes off AFTER the name and the drivers --------------------


#: The neighbours of the paths, each leaving a line in the trace. run_restore
#: and run_restore_drawers record the hold they ran under.
PATH_STUBS = r'''
log() { echo "LOG $*" >> "$RUN_DIR/agent.log"; }
json_get() { case "$2" in .session.image_id) echo img_1 ;; .session.id) echo ses_1 ;; *) echo 0 ;; esac; }
http_get() { echo '{}'; }
target_init() { mkdir -p "$RUN_DIR/targets/$1"; echo waiting > "$RUN_DIR/targets/$1/state"; }
target_set() { echo "$2" > "$RUN_DIR/targets/$1/state"; }
progress_loop() { :; }
smart_gate() { shift; echo "$@"; return 0; }
drawer_gates() { echo "$@"; }   # #1808/#1898: the drawer questions -- their own tests
ui_clear() { :; }; ui_header() { :; }; hold_beat() { :; }
crc_delta_after() { :; }; crc_delta_measure() { :; }
pick_internal_disk() { echo sda; }
sr_share_gate() { return 0; }   # #1898: the split question -- its own tests (test_single_restore_share_1898.py)
resolve_target_drawers() { _disks="sdb sdc"; }
run_restore() { echo "RESTORE hold=${WRITEMARK_HOLD:-0}" >> "$RUN_DIR/trace"; target_set "$2" done; echo done > "$RUN_DIR/state"; return 0; }
run_restore_drawers() { echo "DRAWERS hold=${WRITEMARK_HOLD:-0}" >> "$RUN_DIR/trace"; shift 4; for _t in "$@"; do target_set "$_t" done; done; echo done > "$RUN_DIR/state"; return 0; }
name_this_machine() { echo "NAMED $1" >> "$RUN_DIR/trace"; echo done > "$RUN_DIR/state"; }
stage_drivers() { echo "DRIVERS $1" >> "$RUN_DIR/trace"; echo done > "$RUN_DIR/state"; }
stage_drawer_drivers() { shift 2; echo "DRIVERS $*" >> "$RUN_DIR/trace"; }
writemark_release() { echo "RELEASE $1" >> "$RUN_DIR/trace"; [ ! -f "$RUN_DIR/release.fail" ] || { target_set "$1" failed; return 1; }; }
report_final() { echo "REPORT $(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; return 0; }
ui_error_hold() { echo "HOLD $1" >> "$RUN_DIR/trace"; }
end_tally() { _et_ok=1; _et_n=1; }; end_countdown() { return 0; }; end_failed_round() { echo "ENDFAILED $1" >> "$RUN_DIR/trace"; }
round_closed() { return 1; }
'''


def _run_path(tmp_path: Path, call: str, *, release_fails: bool = False) -> list[str]:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    if release_fails:
        (run / "release.fail").write_text("y\n")
    funcs = tmp_path / "funcs.sh"
    funcs.write_text(cut_function("do_restore") + "\n" + cut_function("do_restore_drawers") + "\n",
                     encoding="utf-8", newline="\n")
    script = (f'export RUN_DIR={posix(run)!r} RESP=x SERVER=http://127.0.0.1:9 MAC=m D_ROLE=classroom '
              f'IMAGECTL_TEST=1; . {posix(AGENT)}/lib/writemark.sh; . {posix(AGENT)}/lib/verdict.sh; '
              + PATH_STUBS + f'. {posix(funcs)}; {call}; echo "RETURNED rc=$?"')
    out = tmp_path / "out.txt"
    with out.open("w", encoding="utf-8") as sink:
        subprocess.run([BASH, "-c", script], stdout=sink, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, timeout=120)
    trace = run / "trace"
    return trace.read_text(encoding="utf-8").splitlines() if trace.exists() else [out.read_text()]


def test_the_class_station_releases_the_mark_after_the_name_and_the_drivers(tmp_path):
    trace = _run_path(tmp_path, "do_restore")
    assert trace == ["RESTORE hold=1", "NAMED sda", "DRIVERS sda", "RELEASE sda", "REPORT done"], trace


def test_the_class_station_whose_mark_stays_is_failed_and_does_not_report_done(tmp_path):
    trace = _run_path(tmp_path, "do_restore", release_fails=True)
    assert trace[:4] == ["RESTORE hold=1", "NAMED sda", "DRIVERS sda", "RELEASE sda"], trace
    assert not any(t.startswith("REPORT") for t in trace), trace
    assert any(t.startswith("HOLD ") for t in trace), trace
    assert (tmp_path / "run" / "state").read_text().strip() == "failed"


def test_the_cloner_releases_each_drawer_after_its_drivers(tmp_path):
    trace = _run_path(tmp_path, "do_restore_drawers img_1 ses_1")
    assert trace == ["DRAWERS hold=1", "DRIVERS sdb sdc", "RELEASE sdb", "RELEASE sdc", "REPORT done"], trace


def test_the_cloner_whose_marks_stay_ends_the_round_failed(tmp_path):
    trace = _run_path(tmp_path, "do_restore_drawers img_1 ses_1", release_fails=True)
    assert trace[:4] == ["DRAWERS hold=1", "DRIVERS sdb sdc", "RELEASE sdb", "RELEASE sdc"], trace
    assert not any(t.startswith("REPORT") for t in trace), trace
    assert any(t.startswith("ENDFAILED") for t in trace), trace


# --- the GUI restore (single_restore.sh) ---------------------------------------------


SR_STUBS = r'''
sr_refused() { return 1; }; die_local() { echo "DIE $*" >> "$RUN_DIR/trace"; exit 1; }
smart_red() { return 1; }; sr_gui_start() { :; }; pull_open() { PULL_SESSION=p; return 0; }
sr_cancel_watch() { :; }; sr_cancel_requested() { return 1; }
pull_close() { echo "CLOSE $(cat "$RUN_DIR/state")" >> "$RUN_DIR/trace"; }; sr_gui_done() { :; }
single_restore_failed_screen() { echo "FAILSCREEN" >> "$RUN_DIR/trace"; }
'''


def _sr(tmp_path: Path, *, release_fails: bool = False) -> tuple[list[str], str]:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True)
    if release_fails:
        (run / "release.fail").write_text("y\n")
    script = (f'export RUN_DIR={posix(run)!r} SERVER=http://127.0.0.1:9 MAC=m; '
              f'. {posix(AGENT)}/lib/writemark.sh; . {posix(AGENT)}/lib/single_restore.sh; '
              + PATH_STUBS + SR_STUBS + 'single_restore_run img_1; echo "rc=$?" >> "$RUN_DIR/trace"')
    subprocess.run([BASH, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   stdin=subprocess.DEVNULL, timeout=120)
    trace = (run / "trace").read_text(encoding="utf-8").splitlines()
    return trace, (run / "state").read_text(encoding="utf-8").strip()


def test_the_gui_restore_releases_the_mark_after_the_drivers(tmp_path):
    trace, state = _sr(tmp_path)
    assert trace == ["RESTORE hold=1", "DRIVERS sda", "RELEASE sda", "CLOSE done", "rc=0"], trace
    assert state == "done"


def test_the_gui_restore_whose_mark_stays_is_failed(tmp_path):
    trace, state = _sr(tmp_path, release_fails=True)
    assert trace == ["RESTORE hold=1", "DRIVERS sda", "RELEASE sda", "FAILSCREEN", "HOLD restore did not complete", "rc=1"], trace
    assert state == "failed"
